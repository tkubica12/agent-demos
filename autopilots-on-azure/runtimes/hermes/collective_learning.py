from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from blueprint import governed_state_hash, worker_refresh_requires_export
from learning import (
    PRIVATE_EXCLUSIONS,
    _artifact_files,
    _artifact_hash,
    _decoded_snapshot,
    _file_snapshot,
    _redaction_findings,
    _skill_content_for_dlp,
    stored_learning_records,
    utc_now,
    worker_manifest_path,
)


LEARNING_PACKET_VERSION = "1.0"


class CollectiveLearningError(ValueError):
    pass


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _packet_digest(packet: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(packet).encode("utf-8")).hexdigest()


def _approval_public_key() -> Ed25519PublicKey:
    value = os.getenv("COLLECTIVE_LEARNING_APPROVAL_PUBLIC_KEY", "")
    if not value:
        raise CollectiveLearningError(
            "COLLECTIVE_LEARNING_APPROVAL_PUBLIC_KEY is required to verify Learning Packet approval."
        )
    try:
        return Ed25519PublicKey.from_public_bytes(base64.b64decode(value))
    except (ValueError, TypeError) as exc:
        raise CollectiveLearningError("Collective Learning approval public key is invalid.") from exc


def _verify_receipt_signature(receipt: dict[str, Any]) -> None:
    signature = receipt.get("signature")
    if not isinstance(signature, str):
        raise CollectiveLearningError("Learning Packet approval signature is missing.")
    signed = {key: value for key, value in receipt.items() if key != "signature"}
    try:
        _approval_public_key().verify(
            base64.b64decode(signature),
            _canonical_json(signed).encode("utf-8"),
        )
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise CollectiveLearningError("Learning Packet approval signature is invalid.") from exc


def _load_worker_manifest(profile_home: Path) -> dict[str, Any]:
    path = worker_manifest_path(profile_home)
    if not path.is_file():
        raise CollectiveLearningError(f"Worker manifest is missing: {path}.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CollectiveLearningError("Worker manifest must contain one JSON object.")
    return payload


def _artifact_roots(paths: set[str], namespace: str) -> set[str]:
    prefix = f"skills/{namespace}/"
    roots: set[str] = set()
    for path in paths:
        if not path.startswith(prefix):
            continue
        parts = path.split("/")
        if len(parts) >= 4:
            roots.add("/".join(parts[:3]))
    return roots


def _artifact_hashes_from_map(files: dict[str, bytes], roots: set[str]) -> dict[str, str | None]:
    return {root: _artifact_hash(_artifact_files(files, root)) for root in sorted(roots)}


def _governed_artifacts(profile_home: Path, manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    current = _decoded_snapshot(_file_snapshot(profile_home))
    baseline = manifest.get("roleSkillBaseline")
    if not isinstance(baseline, dict):
        raise CollectiveLearningError("Worker manifest roleSkillBaseline must be an object.")
    baseline_paths = set(str(path) for path in baseline)
    current_paths = set(current)
    role_roots = _artifact_roots(baseline_paths | current_paths, "role")
    candidate_roots = _artifact_roots(current_paths, "candidates")
    artifacts: dict[str, dict[str, Any]] = {}
    for root in sorted(role_roots):
        current_files = _artifact_files(current, root)
        current_hashes = {
            path: hashlib.sha256(content).hexdigest()
            for path, content in current_files.items()
        }
        baseline_hashes = {
            path: str(value)
            for path, value in baseline.items()
            if path.startswith(f"{root}/")
        }
        if current_hashes != baseline_hashes:
            artifacts[root] = {
                "classification": "role_skill_improvement",
                "files": current_files,
                "afterHash": _artifact_hash(current_files),
            }
    for root, after_hash in _artifact_hashes_from_map(current, candidate_roots).items():
        artifacts[root] = {
            "classification": "candidate_improvement",
            "files": _artifact_files(current, root),
            "afterHash": after_hash,
        }
    return artifacts


def _matching_provenance(
    records: list[dict[str, Any]],
    *,
    artifact_path: str,
    after_hash: str | None,
) -> dict[str, Any]:
    matches = [
        record
        for record in records
        if record["artifact"]["path"] == artifact_path
        and record["artifact"]["afterHash"] == after_hash
    ]
    if not matches:
        raise CollectiveLearningError(
            f"{artifact_path} has no provenance matching its current artifact hash."
        )
    return matches[-1]


def prepare_learning_packet(profile_home: Path) -> dict[str, Any]:
    manifest = _load_worker_manifest(profile_home)
    records, rejected = stored_learning_records(profile_home)
    if rejected:
        raise CollectiveLearningError("Stored provenance contains invalid records; repair it before export.")
    artifacts = _governed_artifacts(profile_home, manifest)
    improvements: list[dict[str, Any]] = []
    for artifact_path, artifact in sorted(artifacts.items()):
        text_files = _skill_content_for_dlp(artifact["files"])
        findings = _redaction_findings(text_files, f"artifact.{artifact_path}")
        if findings:
            raise CollectiveLearningError(
                f"Privacy checks rejected {artifact_path}: {' '.join(findings)}"
            )
        provenance = _matching_provenance(
            records,
            artifact_path=artifact_path,
            after_hash=artifact["afterHash"],
        )
        improvements.append(
            {
                "classification": artifact["classification"],
                "artifactPath": artifact_path,
                "files": text_files,
                "provenance": provenance,
            }
        )
    packet = {
        "packetVersion": LEARNING_PACKET_VERSION,
        "createdAt": utc_now(),
        "roleRelease": {
            "roleBlueprint": manifest["roleBlueprint"],
            "source": manifest["roleBlueprintSource"],
            "path": manifest["roleBlueprintPath"],
            "release": manifest["roleRelease"],
            "commit": manifest["roleReleaseCommit"],
        },
        "worker": {
            "workerId": manifest["workerId"],
            "assignmentScope": manifest["assignmentScope"],
        },
        "governedStateHash": governed_state_hash(profile_home),
        "improvements": improvements,
        "privacy": {
            "status": "ready_for_human_approval",
            "excludedPaths": PRIVATE_EXCLUSIONS,
        },
    }
    digest = _packet_digest(packet)
    exports = profile_home / "learning" / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    pending_path = exports / f"{manifest['roleReleaseCommit']}.pending.json"
    pending_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "roleRelease": packet["roleRelease"],
        "worker": packet["worker"],
        "packetDigest": digest,
        "improvements": [
            {
                "classification": improvement["classification"],
                "artifactPath": improvement["artifactPath"],
                "action": improvement["provenance"]["action"],
                "title": improvement["provenance"]["title"],
            }
            for improvement in improvements
        ],
        "approvalRequired": True,
    }


def pending_learning_packet(profile_home: Path) -> dict[str, Any]:
    manifest = _load_worker_manifest(profile_home)
    path = profile_home / "learning" / "exports" / f"{manifest['roleReleaseCommit']}.pending.json"
    if not path.is_file():
        raise CollectiveLearningError("Prepare the Learning Packet before approval.")
    packet = json.loads(path.read_text(encoding="utf-8"))
    return {"packet": packet, "packetDigest": _packet_digest(packet)}


def attest_learning_packet(
    profile_home: Path,
    *,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    manifest = _load_worker_manifest(profile_home)
    exports = profile_home / "learning" / "exports"
    pending_path = exports / f"{manifest['roleReleaseCommit']}.pending.json"
    if not pending_path.is_file():
        raise CollectiveLearningError("Prepare the Learning Packet before approval.")
    packet = json.loads(pending_path.read_text(encoding="utf-8"))
    actual_digest = _packet_digest(packet)
    required = {
        "approved",
        "approvedAt",
        "approvedBy",
        "workerId",
        "roleReleaseCommit",
        "governedStateHash",
        "packetDigest",
        "signature",
    }
    if set(receipt) != required:
        raise CollectiveLearningError("Learning Packet approval receipt fields are invalid.")
    if receipt.get("approved") is not True:
        raise CollectiveLearningError("Learning Packet receipt is not approved.")
    if receipt.get("packetDigest") != actual_digest:
        raise CollectiveLearningError("Learning Packet digest does not match the prepared packet.")
    if packet.get("governedStateHash") != governed_state_hash(profile_home):
        raise CollectiveLearningError("Governed skills changed after packet preparation; prepare it again.")
    if (
        receipt.get("workerId") != manifest["workerId"]
        or receipt.get("roleReleaseCommit") != manifest["roleReleaseCommit"]
        or receipt.get("governedStateHash") != packet["governedStateHash"]
        or not isinstance(receipt.get("approvedBy"), str)
        or not receipt["approvedBy"].strip()
    ):
        raise CollectiveLearningError("Learning Packet receipt does not match this Worker or Role Release.")
    _verify_receipt_signature(receipt)
    packet_path = exports / f"{manifest['roleReleaseCommit']}.packet.json"
    packet_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt_path = exports / f"{manifest['roleReleaseCommit']}.approved.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pending_path.unlink()
    return receipt


def approved_learning_packet(profile_home: Path) -> dict[str, Any]:
    manifest = _load_worker_manifest(profile_home)
    exports = profile_home / "learning" / "exports"
    receipt_path = exports / f"{manifest['roleReleaseCommit']}.approved.json"
    packet_path = exports / f"{manifest['roleReleaseCommit']}.packet.json"
    if not receipt_path.is_file() or not packet_path.is_file():
        raise CollectiveLearningError("No approved Learning Packet is available.")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    if receipt.get("governedStateHash") != governed_state_hash(profile_home):
        raise CollectiveLearningError("Governed skills changed after Learning Packet approval.")
    if receipt.get("packetDigest") != _packet_digest(packet):
        raise CollectiveLearningError("Approved Learning Packet digest is invalid.")
    _verify_receipt_signature(receipt)
    return {"packet": packet, "receipt": receipt}


def worker_refresh_readiness(profile_home: Path) -> dict[str, Any]:
    manifest = _load_worker_manifest(profile_home)
    if not worker_refresh_requires_export(profile_home, manifest):
        return {
            "ready": True,
            "exportRequired": False,
            "roleReleaseCommit": manifest["roleReleaseCommit"],
        }
    approved_learning_packet(profile_home)
    return {
        "ready": True,
        "exportRequired": True,
        "roleReleaseCommit": manifest["roleReleaseCommit"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare and approve Collective Learning Review packets.")
    parser.add_argument("--profile-home", required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("prepare")
    attest = subparsers.add_parser("attest")
    attest.add_argument("--receipt", required=True)
    subparsers.add_parser("export")
    args = parser.parse_args()
    profile_home = Path(args.profile_home)
    if args.command == "prepare":
        result = prepare_learning_packet(profile_home)
    elif args.command == "attest":
        receipt = json.loads(args.receipt)
        if not isinstance(receipt, dict):
            raise CollectiveLearningError("--receipt must be one JSON object.")
        result = attest_learning_packet(profile_home, receipt=receipt)
    else:
        result = approved_learning_packet(profile_home)
    print(json.dumps(result, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
