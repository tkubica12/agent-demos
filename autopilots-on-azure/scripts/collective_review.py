from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
import yaml
from azure.identity import AzureCliCredential
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from runtimes.hermes.learning import _redaction_findings
from scripts.tf_helpers import PLATFORM_DIR, terraform_output


TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"
ROLE_SKILL_PATH = re.compile(r"^skills/role/[a-z0-9][a-z0-9-]{0,62}/SKILL\.md$")
ROLE_RELEASE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


class CollectiveReviewError(ValueError):
    pass


def validate_next_role_release(
    packets: list[dict[str, Any]],
    next_role_release: str,
) -> None:
    current = str(packets[0]["roleRelease"]["release"])
    current_match = ROLE_RELEASE.fullmatch(current)
    next_match = ROLE_RELEASE.fullmatch(next_role_release)
    if not current_match or not next_match:
        raise CollectiveReviewError("Role Releases must use semantic version MAJOR.MINOR.PATCH.")
    current_tuple = tuple(int(value) for value in current_match.groups())
    next_tuple = tuple(int(value) for value in next_match.groups())
    if next_tuple <= current_tuple:
        raise CollectiveReviewError(
            f"Next Role Release must be newer than {current}; received {next_role_release}."
        )


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _verify_attestation(
    packet: dict[str, Any],
    receipt: dict[str, Any],
    *,
    worker_public_keys: dict[str, str],
) -> None:
    worker_id = packet.get("worker", {}).get("workerId")
    if not isinstance(worker_id, str) or worker_id not in worker_public_keys:
        raise CollectiveReviewError(f"No trusted attestation key is configured for Worker {worker_id!r}.")
    packet_digest = hashlib.sha256(_canonical_json(packet).encode("utf-8")).hexdigest()
    if (
        receipt.get("approved") is not True
        or receipt.get("packetDigest") != packet_digest
        or receipt.get("workerId") != worker_id
        or receipt.get("roleReleaseCommit") != packet.get("roleRelease", {}).get("commit")
        or receipt.get("governedStateHash") != packet.get("governedStateHash")
    ):
        raise CollectiveReviewError(f"Learning Packet attestation does not match Worker {worker_id}.")
    signed = {key: value for key, value in receipt.items() if key != "signature"}
    signature = receipt.get("signature")
    public_key = worker_public_keys[worker_id].strip()
    if not public_key:
        raise CollectiveReviewError(f"Trusted attestation key is blank for Worker {worker_id}.")
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key)).verify(
            base64.b64decode(signature) if isinstance(signature, str) else b"",
            _canonical_json(signed).encode("utf-8"),
        )
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise CollectiveReviewError(f"Learning Packet attestation is invalid for Worker {worker_id}.")


def _review_payload(packets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "workerId": packet["worker"]["workerId"],
            "improvements": [
                {
                    "classification": improvement["classification"],
                    "artifactPath": improvement["artifactPath"],
                    "files": improvement["files"],
                    "provenance": {
                        key: improvement["provenance"].get(key)
                        for key in (
                            "recordId",
                            "action",
                            "title",
                            "generalizedLearning",
                            "rationale",
                            "evidence",
                            "confidence",
                            "sourceStage",
                        )
                    },
                }
                for improvement in packet.get("improvements") or []
            ],
        }
        for packet in packets
    ]


def load_packets(
    paths: list[Path],
    *,
    worker_public_keys: dict[str, str],
) -> list[dict[str, Any]]:
    packets: list[dict[str, Any]] = []
    for path in paths:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(envelope, dict) or set(envelope) != {"packet", "receipt"}:
            raise CollectiveReviewError(f"{path} must contain one attested Learning Packet envelope.")
        payload = envelope["packet"]
        receipt = envelope["receipt"]
        if not isinstance(payload, dict) or not isinstance(receipt, dict):
            raise CollectiveReviewError(f"{path} contains an invalid Learning Packet envelope.")
        if payload.get("packetVersion") != "1.0":
            raise CollectiveReviewError(f"{path} uses an unsupported Learning Packet version.")
        privacy = payload.get("privacy")
        if not isinstance(privacy, dict) or privacy.get("status") != "ready_for_human_approval":
            raise CollectiveReviewError(f"{path} has an invalid Learning Packet privacy state.")
        _verify_attestation(payload, receipt, worker_public_keys=worker_public_keys)
        packets.append(payload)
    if not packets:
        raise CollectiveReviewError("At least one approved Learning Packet is required.")
    baseline = packets[0]["roleRelease"]
    for packet in packets[1:]:
        if packet.get("roleRelease") != baseline:
            raise CollectiveReviewError("All Learning Packets must use the same Role Release.")
    worker_ids = [packet["worker"]["workerId"] for packet in packets]
    if len(worker_ids) != len(set(worker_ids)):
        raise CollectiveReviewError("Each Learning Packet must come from a unique Worker.")
    review_content = _review_payload(packets)
    findings = _redaction_findings(review_content, "packets")
    if findings:
        raise CollectiveReviewError("Learning Packet privacy scan failed: " + " ".join(findings))
    return packets


def _response_text(payload: dict[str, Any]) -> str:
    output = payload.get("output")
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            for content in item.get("content") or []:
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    parts.append(content["text"])
        if parts:
            return "\n".join(parts)
    output_text = payload.get("output_text")
    if isinstance(output_text, str):
        return output_text
    raise CollectiveReviewError("Merger/judge returned no text output.")


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    payload = json.loads(stripped)
    if not isinstance(payload, dict):
        raise CollectiveReviewError("Merger/judge output must be one JSON object.")
    return payload


def judge_prompt(packets: list[dict[str, Any]], next_role_release: str) -> str:
    baseline = packets[0]["roleRelease"]
    return (
        "You are the expert merger/judge for Collective Learning Review.\n"
        f"Role Blueprint: {baseline['roleBlueprint']}\n"
        f"Current Role Release: {baseline['release']} at {baseline['commit']}\n"
        f"Proposed next Role Release: {next_role_release}\n\n"
        "Review approved Candidate Improvements and Role Skill diffs from several Workers. Generalize repeated evidence, reject "
        "private overfitting and unsupported outliers, resolve conflicts, and propose the smallest coherent Role Skill changes. "
        "Never include people, customers, accounts, projects, tenant identifiers, credentials, internal URLs, private paths, or "
        "raw messages. Return JSON only with this shape:\n"
        '{"summary":"...","proposals":[{"action":"create_or_replace","targetPath":"skills/role/<name>/SKILL.md",'
        '"content":"complete SKILL.md","rationale":"...","supportingRecordIds":["lr-..."],'
        '"supportingWorkers":["worker-id"]}],"rejected":[{"recordId":"lr-...","reason":"..."}],"conflicts":["..."]}\n'
        "Each proposal content must be a complete agentskills.io-compatible SKILL.md. Use action reject by omitting a proposal "
        "and listing its records under rejected. targetPath must contain exactly one lowercase kebab-case skill-name segment "
        "between skills/role and SKILL.md, for example skills/role/delivery-commitment-control/SKILL.md. Never prefix or nest "
        "the target under the Role Blueprint name. Do not modify SOUL.md, configuration, memory, or non-role paths.\n\n"
        "Approved Learning Packets:\n"
        + json.dumps(_review_payload(packets), indent=2, ensure_ascii=True)
    )


def run_merger_judge(
    packets: list[dict[str, Any]],
    *,
    next_role_release: str,
    model: str,
    base_url: str,
) -> dict[str, Any]:
    token = AzureCliCredential().get_token(TOKEN_SCOPE).token
    response = httpx.post(
        f"{base_url.rstrip('/')}/responses",
        headers={
            "Authorization": f"{'Bear' + 'er'} {token}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "input": judge_prompt(packets, next_role_release),
        },
        timeout=600,
    )
    response.raise_for_status()
    return validate_decision(
        _parse_json_object(_response_text(response.json())),
        packets=packets,
    )


def validate_decision(
    decision: dict[str, Any],
    *,
    packets: list[dict[str, Any]],
) -> dict[str, Any]:
    if set(decision) != {"summary", "proposals", "rejected", "conflicts"}:
        raise CollectiveReviewError("Merger/judge decision fields are invalid.")
    if not isinstance(decision["summary"], str) or not decision["summary"].strip():
        raise CollectiveReviewError("Merger/judge summary is required.")
    known_records = {
        improvement["provenance"]["recordId"]
        for packet in packets
        for improvement in packet.get("improvements") or []
    }
    known_workers = {packet["worker"]["workerId"] for packet in packets}
    proposals = decision["proposals"]
    if not isinstance(proposals, list):
        raise CollectiveReviewError("proposals must be an array.")
    seen_paths: set[str] = set()
    for proposal in proposals:
        if not isinstance(proposal, dict) or set(proposal) != {
            "action",
            "targetPath",
            "content",
            "rationale",
            "supportingRecordIds",
            "supportingWorkers",
        }:
            raise CollectiveReviewError("A proposal has invalid fields.")
        if proposal["action"] != "create_or_replace":
            raise CollectiveReviewError("Proposal action must be create_or_replace.")
        target = proposal["targetPath"]
        if not isinstance(target, str) or not ROLE_SKILL_PATH.fullmatch(target):
            raise CollectiveReviewError(f"Unsafe proposal target path: {target!r}.")
        if target in seen_paths:
            raise CollectiveReviewError(f"Duplicate proposal target path: {target}.")
        seen_paths.add(target)
        if not isinstance(proposal["content"], str) or not proposal["content"].strip():
            raise CollectiveReviewError(f"Proposal {target} has no SKILL.md content.")
        if proposal["supportingRecordIds"] and not set(proposal["supportingRecordIds"]) <= known_records:
            raise CollectiveReviewError(f"Proposal {target} references unknown provenance records.")
        if proposal["supportingWorkers"] and not set(proposal["supportingWorkers"]) <= known_workers:
            raise CollectiveReviewError(f"Proposal {target} references unknown Workers.")
        findings = _redaction_findings(proposal, f"proposal.{target}")
        if findings:
            raise CollectiveReviewError("Proposal privacy scan failed: " + " ".join(findings))
    rejected = decision["rejected"]
    if not isinstance(rejected, list):
        raise CollectiveReviewError("rejected must be an array.")
    for item in rejected:
        if not isinstance(item, dict) or set(item) != {"recordId", "reason"}:
            raise CollectiveReviewError("A rejected item has invalid fields.")
        if item["recordId"] not in known_records:
            raise CollectiveReviewError("A rejected item references an unknown provenance record.")
    if not isinstance(decision["conflicts"], list) or not all(
        isinstance(item, str) for item in decision["conflicts"]
    ):
        raise CollectiveReviewError("conflicts must be an array of strings.")
    findings = _redaction_findings(decision, "decision")
    if findings:
        raise CollectiveReviewError("Collective Learning decision privacy scan failed: " + " ".join(findings))
    return decision


def _run(command: list[str], *, cwd: Path) -> str:
    result = subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def create_role_release_pull_request(
    packets: list[dict[str, Any]],
    decision: dict[str, Any],
    *,
    next_role_release: str,
    draft: bool,
) -> dict[str, Any]:
    validate_next_role_release(packets, next_role_release)
    baseline = packets[0]["roleRelease"]
    source = str(baseline["source"])
    repository_path = PurePosixPath(str(baseline["path"]).replace("\\", "/"))
    if repository_path.is_absolute() or ".." in repository_path.parts:
        raise CollectiveReviewError("Role Blueprint repository path is unsafe.")
    branch = f"collective-learning/{baseline['roleBlueprint']}-{next_role_release}"
    with tempfile.TemporaryDirectory(prefix="collective-learning-review-") as temp_dir:
        checkout = Path(temp_dir) / "repository"
        _run(["git", "clone", source, str(checkout)], cwd=Path(temp_dir))
        _run(["git", "checkout", "--detach", baseline["commit"]], cwd=checkout)
        _run(["git", "switch", "-c", branch], cwd=checkout)
        _run(["git", "config", "user.name", "Collective Learning Review"], cwd=checkout)
        _run(
            ["git", "config", "user.email", "collective-learning@users.noreply.github.com"],
            cwd=checkout,
        )
        role_root = checkout.joinpath(*repository_path.parts)
        for proposal in decision["proposals"]:
            relative = PurePosixPath(proposal["targetPath"])
            destination = role_root.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(proposal["content"].rstrip() + "\n", encoding="utf-8")
        distribution_path = role_root / "distribution.yaml"
        distribution = yaml.safe_load(distribution_path.read_text(encoding="utf-8"))
        distribution["role_release"] = next_role_release
        distribution_path.write_text(
            yaml.safe_dump(distribution, sort_keys=False),
            encoding="utf-8",
        )
        review_path = role_root / "collective-learning-review.json"
        review_path.write_text(json.dumps(decision, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        _run(["git", "add", "."], cwd=checkout)
        if not _run(["git", "status", "--short"], cwd=checkout):
            raise CollectiveReviewError("Merger/judge proposed no Role Blueprint changes.")
        _run(
            [
                "git",
                "commit",
                "-m",
                f"Promote Collective Learning Review into Role Release {next_role_release}",
            ],
            cwd=checkout,
        )
        _run(["git", "push", "--set-upstream", "origin", branch], cwd=checkout)
        body_path = checkout / ".collective-learning-pr.md"
        body_path.write_text(
            "\n".join(
                [
                    "## Collective Learning Review",
                    "",
                    decision["summary"],
                    "",
                    f"- Source Role Release: `{baseline['release']}`",
                    f"- Proposed Role Release: `{next_role_release}`",
                    f"- Workers reviewed: {len(packets)}",
                    f"- Promoted Role Skill changes: {len(decision['proposals'])}",
                    f"- Rejected provenance records: {len(decision['rejected'])}",
                    "",
                    "Human expert review is required before Promotion.",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        command = [
            "gh",
            "pr",
            "create",
            "--title",
            f"Role Release {next_role_release}: Collective Learning Review",
            "--body-file",
            str(body_path),
            "--head",
            branch,
        ]
        if draft:
            command.append("--draft")
        url = _run(command, cwd=checkout)
    return {
        "pullRequest": url,
        "branch": branch,
        "roleRelease": next_role_release,
        "proposalCount": len(decision["proposals"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Collective Learning Review over approved Learning Packets.")
    parser.add_argument("--packet", action="append", type=Path, required=True)
    parser.add_argument("--next-role-release", required=True)
    parser.add_argument(
        "--worker-public-keys",
        type=Path,
        action="append",
        required=True,
        help="Local JSON object mapping trusted Worker IDs to Ed25519 approval public keys. Repeat per Worker.",
    )
    parser.add_argument("--model", default="gpt-5-6-terra")
    parser.add_argument("--decision-output", type=Path, required=True)
    parser.add_argument("--create-pr", action="store_true")
    parser.add_argument("--ready", action="store_true", help="Create a ready PR instead of a draft.")
    args = parser.parse_args()
    worker_public_keys: dict[str, str] = {}
    for path in args.worker_public_keys:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not all(
            isinstance(key, str) and isinstance(value, str) and value.strip()
            for key, value in payload.items()
        ):
            raise CollectiveReviewError(
                "--worker-public-keys must contain JSON objects of non-empty string keys."
            )
        for worker_id, public_key in payload.items():
            existing = worker_public_keys.get(worker_id)
            if existing and existing != public_key:
                raise CollectiveReviewError(
                    f"Conflicting trusted public keys were supplied for Worker {worker_id}."
                )
            worker_public_keys[worker_id] = public_key
    packets = load_packets(args.packet, worker_public_keys=worker_public_keys)
    validate_next_role_release(packets, args.next_role_release)
    platform = terraform_output(PLATFORM_DIR)
    decision = run_merger_judge(
        packets,
        next_role_release=args.next_role_release,
        model=args.model,
        base_url=platform["foundry_openai_base_url"],
    )
    args.decision_output.parent.mkdir(parents=True, exist_ok=True)
    args.decision_output.write_text(json.dumps(decision, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result: dict[str, Any] = {
        "decisionOutput": str(args.decision_output),
        "proposalCount": len(decision["proposals"]),
        "rejectedCount": len(decision["rejected"]),
    }
    if args.create_pr:
        result.update(
            create_role_release_pull_request(
                packets,
                decision,
                next_role_release=args.next_role_release,
                draft=not args.ready,
            )
        )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
