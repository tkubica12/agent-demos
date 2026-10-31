"""Collective Learning export built from Hermes-native skill state and the owner's sharing choices."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from blueprint import governed_state_hash, worker_manifest_path, worker_refresh_requires_export, _require_export_receipt
from native_learning import (
    LearningConflictError,
    artifact_hash,
    current_share_choice,
    governed_changes,
    learning_receipts,
    ledger_chain,
    native_module,
    utc_now,
    write_json_atomic,
)


LEARNING_PACKET_VERSION = "3.0"
PACKET_EVALUATION = {
    "agentProposedScenarios": "not_generated",
    "executionStatus": "not_run",
    "independentHoldout": "not_supplied",
}
PRIVATE_EXCLUSIONS = [
    ".env",
    "auth/",
    "learning/",
    "logs/",
    "memories/",
    "pending/",
    "sessions/",
    "skills/private/",
    "state.db",
    "state.db-shm",
    "state.db-wal",
    "workspace/",
]
MAX_SKILL_BYTES = 1_000_000
GOVERNED_ARTIFACT_PATH = re.compile(r"^skills/(?:role|candidates)/[a-z0-9][a-z0-9-]{0,62}$")
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_GUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b", re.IGNORECASE)
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_INTERNAL_URL = re.compile(r"https?://(?:localhost|127\.0\.0\.1|[^/\s]*\.(?:internal|local))(?:[/:][^\s]*)?", re.IGNORECASE)
_SECRET = re.compile(
    r"(?:bearer\s+[A-Za-z0-9._~+/=-]{12,}|api[_ -]?key\s*[:=]\s*\S+|client[_ -]?secret\s*[:=]\s*\S+|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|[A-Za-z0-9_-]{40,})",
    re.IGNORECASE,
)
_ABSOLUTE_PATH = re.compile(
    r"(?:\b[A-Za-z]:\\Users\\[^\\\s]+|/(?:root|data|etc|home|tmp|users|var)/[^\s]*)",
    re.IGNORECASE,
)


class CollectiveLearningError(ValueError):
    pass


def redaction_findings(value: Any, path: str = "record") -> list[str]:
    """Privacy scan for anything that may leave the Worker."""
    findings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            findings.extend(redaction_findings(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(redaction_findings(child, f"{path}[{index}]"))
    elif isinstance(value, str):
        for label, pattern in (
            ("email address", _EMAIL),
            ("GUID or tenant identifier", _GUID),
            ("IP address", _IPV4),
            ("internal URL", _INTERNAL_URL),
            ("secret or token-shaped value", _SECRET),
            ("user-specific absolute path", _ABSOLUTE_PATH),
        ):
            if pattern.search(value):
                findings.append(f"{path} contains a {label}.")
    return findings


def validate_governed_artifact(artifact_path: str, files: dict[str, str]) -> None:
    """Export transfer contract: exactly one bounded SKILL.md whose frontmatter names its directory."""
    if not isinstance(artifact_path, str) or not GOVERNED_ARTIFACT_PATH.fullmatch(artifact_path):
        raise CollectiveLearningError("Governed artifact path must identify one lowercase skill name.")
    if set(files) != {f"{artifact_path}/SKILL.md"}:
        raise CollectiveLearningError(
            "Export transfers exactly one SKILL.md per skill; deletions and supporting files stay local."
        )
    text = files[f"{artifact_path}/SKILL.md"]
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_SKILL_BYTES:
        raise CollectiveLearningError("SKILL.md must be bounded UTF-8 text.")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", text, re.DOTALL)
    if not match:
        raise CollectiveLearningError("SKILL.md must start with YAML frontmatter.")
    try:
        metadata = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise CollectiveLearningError("SKILL.md frontmatter is invalid YAML.") from exc
    if not isinstance(metadata, dict) or metadata.get("name") != artifact_path.rsplit("/", 1)[1]:
        raise CollectiveLearningError("SKILL.md frontmatter name must match the skill directory.")
    description = metadata.get("description")
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        raise CollectiveLearningError("SKILL.md frontmatter needs a description of at most 1024 characters.")


def file_hashes(files: dict[str, str]) -> dict[str, str]:
    return {path: hashlib.sha256(text.encode("utf-8")).hexdigest() for path, text in files.items()}


def exported_artifact_hash(files: dict[str, str]) -> str | None:
    """Artifact identity of exported text files, matching the Worker's native-state artifact hash."""
    return artifact_hash(file_hashes(files))


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


def _ledger_record(profile_home: Path, entry: dict[str, Any], artifact_path: str) -> dict[str, Any]:
    files: dict[str, dict[str, str | None]] = {}
    for section in ("before", "after"):
        for item in entry.get(section) or []:
            try:
                relative = Path(os.path.normpath(str(item.get("path") or ""))).relative_to(
                    os.path.normpath(str(profile_home))
                ).as_posix()
            except ValueError:
                continue
            if relative.startswith(f"{artifact_path}/") and Path(relative).name != ".usage.json":
                files.setdefault(relative, {"before": None, "after": None})[section] = str(item.get("sha256") or "")
    return {
        "id": str(entry.get("id") or ""),
        "ts": str(entry.get("ts") or ""),
        "actor": str(entry.get("actor") or ""),
        "action": str(entry.get("action") or ""),
        "files": files,
    }


def _export_texts(profile_home: Path, change: dict[str, Any]) -> dict[str, str]:
    texts: dict[str, str] = {}
    for relative in sorted(change["fileHashes"]):
        try:
            texts[relative] = profile_home.joinpath(*relative.split("/")).read_bytes().decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CollectiveLearningError(f"{relative} is not UTF-8 text.") from exc
    return texts


def _improvement(
    profile_home: Path,
    manifest: dict[str, Any],
    change: dict[str, Any],
    *,
    ledger: Any,
    entries: list[dict[str, Any]] | None,
    receipts: list[dict[str, Any]],
) -> dict[str, Any]:
    """One shareable improvement, or CollectiveLearningError with the exclusion reason (fail closed)."""
    path = change["artifactPath"]
    choice = current_share_choice(profile_home, change, manifest)
    if not choice:
        raise CollectiveLearningError("The Worker owner has not chosen Share for this change.")
    if not choice["current"]:
        raise CollectiveLearningError("The skill changed after the owner chose Share; the owner must choose again.")
    texts = _export_texts(profile_home, change)
    validate_governed_artifact(path, texts)
    if exported_artifact_hash(texts) != change["artifactHash"]:
        raise CollectiveLearningError("The skill changed while the packet was being prepared.")
    findings = redaction_findings(texts, f"artifact.{path}")
    if findings:
        raise CollectiveLearningError("Privacy checks rejected the skill: " + " ".join(findings))
    try:
        chain = ledger_chain(profile_home, change, ledger=ledger, entries=entries)
    except LearningConflictError as exc:
        raise CollectiveLearningError(str(exc)) from exc
    chain_ids = {str(entry.get("id")) for entry in chain}
    approvals = [
        {key: receipt.get(key) for key in ("receiptId", "pendingId", "origin", "action", "decidedBy", "decidedAt")}
        for receipt in receipts
        if receipt.get("kind") == "pending" and receipt.get("approved") is True
        and chain_ids & set(receipt.get("ledgerEntryIds") or [])
    ]
    return {
        "improvementId": "li-" + hashlib.sha256(
            f"{manifest['workerId']}\n{path}\n{change['artifactHash']}".encode("utf-8")
        ).hexdigest()[:24],
        "classification": change["classification"],
        "artifactPath": path,
        "files": texts,
        "baselineFileHashes": change["baselineFileHashes"],
        "artifactHash": change["artifactHash"],
        "ledger": [_ledger_record(profile_home, entry, path) for entry in chain],
        "shareDecision": {key: choice.get(key) for key in ("receiptId", "decidedBy", "decidedAt")},
        "approvals": approvals,
    }


def prepare_learning_packet(profile_home: Path, *, ledger: Any = None) -> dict[str, Any]:
    """Build the pending Learning Packet from governed changes the owner marked Share.

    File hashes are recomputed from disk against the Role Release baseline. Every other governed change,
    or a shared change without a complete native trace, is excluded with a reason and stays local.
    """
    manifest = _load_worker_manifest(profile_home)
    ledger = native_module(ledger, "tools.skill_ledger")
    entries = ledger.list_entries()
    receipts = learning_receipts(profile_home)
    improvements: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    try:
        changes = governed_changes(profile_home, manifest)
    except LearningConflictError as exc:
        raise CollectiveLearningError(str(exc)) from exc
    for change in changes:
        try:
            improvements.append(
                _improvement(profile_home, manifest, change, ledger=ledger, entries=entries, receipts=receipts)
            )
        except CollectiveLearningError as exc:
            excluded.append({
                "artifactPath": change["artifactPath"],
                "classification": change["classification"],
                "reason": str(exc),
            })
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
        "evaluation": dict(PACKET_EVALUATION),
        "privacy": {
            "status": "ready_for_human_approval",
            "excludedPaths": PRIVATE_EXCLUSIONS,
        },
    }
    exports = profile_home / "learning" / "exports"
    pending_path = exports / f"{manifest['roleReleaseCommit']}.pending.json"
    if pending_path.is_file():
        previous = json.loads(pending_path.read_text(encoding="utf-8"))
        if isinstance(previous, dict) and (
            {key: value for key, value in previous.items() if key != "createdAt"}
            == {key: value for key, value in packet.items() if key != "createdAt"}
        ):
            packet = previous
    write_json_atomic(pending_path, packet)
    return {
        "roleRelease": packet["roleRelease"],
        "worker": packet["worker"],
        "packetDigest": _packet_digest(packet),
        "improvements": [
            {
                "improvementId": improvement["improvementId"],
                "classification": improvement["classification"],
                "artifactPath": improvement["artifactPath"],
                "ledgerEntryCount": len(improvement["ledger"]),
                "approvalCount": len(improvement["approvals"]),
            }
            for improvement in improvements
        ],
        "excluded": excluded,
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
        approved = exports / f"{manifest['roleReleaseCommit']}.approved.json"
        if approved.is_file() and json.loads(approved.read_text(encoding="utf-8")) == receipt:
            return approved_learning_packet(profile_home)["receipt"]
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
    write_json_atomic(exports / f"{manifest['roleReleaseCommit']}.packet.json", packet)
    write_json_atomic(exports / f"{manifest['roleReleaseCommit']}.approved.json", receipt)
    (exports / f"{manifest['roleReleaseCommit']}.rejected.json").unlink(missing_ok=True)
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


def prepare_refresh_rejection(profile_home: Path) -> dict[str, Any]:
    """Prepare a local discard disposition even when artifacts cannot pass export checks."""
    manifest = _load_worker_manifest(profile_home)
    descriptor = {
        "dispositionVersion": "1.0",
        "disposition": "reject_and_refresh",
        "createdAt": utc_now(),
        "workerId": manifest["workerId"],
        "roleReleaseCommit": manifest["roleReleaseCommit"],
        "governedStateHash": governed_state_hash(profile_home),
    }
    exports = profile_home / "learning" / "exports"
    pending = exports / f"{manifest['roleReleaseCommit']}.rejection.pending.json"
    if pending.is_file():
        previous = json.loads(pending.read_text(encoding="utf-8"))
        if isinstance(previous, dict) and (
            {key: value for key, value in previous.items() if key != "createdAt"}
            == {key: value for key, value in descriptor.items() if key != "createdAt"}
        ):
            descriptor = previous
    write_json_atomic(pending, descriptor)
    return {"disposition": descriptor, "dispositionDigest": _packet_digest(descriptor)}


def attest_refresh_rejection(profile_home: Path, *, receipt: dict[str, Any]) -> dict[str, Any]:
    manifest = _load_worker_manifest(profile_home)
    exports = profile_home / "learning" / "exports"
    pending = exports / f"{manifest['roleReleaseCommit']}.rejection.pending.json"
    if not pending.is_file():
        stored = exports / f"{manifest['roleReleaseCommit']}.rejected.json"
        if stored.is_file():
            audit = json.loads(stored.read_text(encoding="utf-8"))
            if isinstance(audit, dict) and audit.get("receipt") == receipt:
                if receipt.get("governedStateHash") != governed_state_hash(profile_home):
                    raise CollectiveLearningError("Governed skills changed after the rejection.")
                _verify_receipt_signature(receipt)
                return receipt
        raise CollectiveLearningError("Prepare the reject-and-refresh disposition first.")
    descriptor = json.loads(pending.read_text(encoding="utf-8"))
    if set(receipt) != {
        "disposition", "rejectedAt", "rejectedBy", "reason", "workerId",
        "roleReleaseCommit", "governedStateHash", "dispositionDigest", "signature",
    }:
        raise CollectiveLearningError("Reject-and-refresh receipt fields are invalid.")
    if (
        receipt["disposition"] != "reject_and_refresh"
        or receipt["dispositionDigest"] != _packet_digest(descriptor)
        or receipt["workerId"] != manifest["workerId"]
        or receipt["roleReleaseCommit"] != manifest["roleReleaseCommit"]
        or receipt["governedStateHash"] != descriptor["governedStateHash"]
        or descriptor["governedStateHash"] != governed_state_hash(profile_home)
    ):
        raise CollectiveLearningError("Reject-and-refresh digest or governed state does not match.")
    if not all(isinstance(receipt[key], str) and receipt[key].strip()
               for key in ("rejectedAt", "rejectedBy", "reason")):
        raise CollectiveLearningError("Rejection requires rejectedAt, rejectedBy, and a reason.")
    _verify_receipt_signature(receipt)
    audit = {"disposition": descriptor, "receipt": receipt}
    write_json_atomic(profile_home / "learning" / "dispositions" / f"{receipt['dispositionDigest']}.json", audit)
    write_json_atomic(exports / f"{manifest['roleReleaseCommit']}.rejected.json", audit)
    for suffix in ("approved.json", "packet.json", "pending.json"):
        (exports / f"{manifest['roleReleaseCommit']}.{suffix}").unlink(missing_ok=True)
    pending.unlink()
    return receipt


def pending_refresh_rejection(profile_home: Path) -> dict[str, Any]:
    manifest = _load_worker_manifest(profile_home)
    path = profile_home / "learning" / "exports" / f"{manifest['roleReleaseCommit']}.rejection.pending.json"
    if not path.is_file():
        raise CollectiveLearningError("Prepare the reject-and-refresh disposition first.")
    descriptor = json.loads(path.read_text(encoding="utf-8"))
    if descriptor.get("governedStateHash") != governed_state_hash(profile_home):
        raise CollectiveLearningError("Governed skills changed; prepare the rejection again.")
    return {"disposition": descriptor, "dispositionDigest": _packet_digest(descriptor)}


def worker_refresh_readiness(profile_home: Path) -> dict[str, Any]:
    manifest = _load_worker_manifest(profile_home)
    if not worker_refresh_requires_export(profile_home, manifest):
        return {
            "ready": True,
            "exportRequired": False,
            "roleReleaseCommit": manifest["roleReleaseCommit"],
        }
    _require_export_receipt(profile_home, manifest)
    return {
        "ready": True,
        "exportRequired": True,
        "roleReleaseCommit": manifest["roleReleaseCommit"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare and approve Collective Learning Review packets. Run with the Hermes Python environment.",
    )
    parser.add_argument("--profile-home", required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("prepare")
    subparsers.add_parser("inspect")
    subparsers.add_parser("inspect-rejection")
    subparsers.add_parser("prepare-rejection")
    reject = subparsers.add_parser("reject")
    reject.add_argument("--receipt", required=True)
    attest = subparsers.add_parser("attest")
    attest.add_argument("--receipt", required=True)
    subparsers.add_parser("export")
    args = parser.parse_args()
    profile_home = Path(args.profile_home)
    if args.command == "prepare":
        result = prepare_learning_packet(profile_home)
    elif args.command == "inspect":
        result = pending_learning_packet(profile_home)
    elif args.command == "prepare-rejection":
        result = prepare_refresh_rejection(profile_home)
    elif args.command == "inspect-rejection":
        result = pending_refresh_rejection(profile_home)
    elif args.command == "reject":
        result = attest_refresh_rejection(profile_home, receipt=json.loads(args.receipt))
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
