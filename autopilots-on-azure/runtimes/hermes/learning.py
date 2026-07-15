from __future__ import annotations

import argparse
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"
PACKET_VERSION = "1.0"
TRANSFERABLE_CLASSIFICATIONS = {"transferable_procedural", "transferable_domain"}
SOURCE_TYPES = {"private_session", "tool_result", "public_source"}
TARGET_KINDS = {"skill", "knowledge"}
PRIVATE_EXCLUSIONS = [
    ".env",
    "auth/",
    "logs/",
    "memories/",
    "sessions/",
    "state.db",
    "state.db-shm",
    "state.db-wal",
    "workspace/",
]

_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_GUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b", re.IGNORECASE)
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_SECRET = re.compile(
    r"(?:bearer\s+[A-Za-z0-9._~+/=-]{12,}|api[_ -]?key\s*[:=]\s*\S+|client[_ -]?secret\s*[:=]\s*\S+|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|[A-Za-z0-9_-]{40,})",
    re.IGNORECASE,
)
_ABSOLUTE_PATH = re.compile(r"(?:\b[A-Za-z]:\\Users\\[^\\\s]+|/(?:home|users)/[^/\s]+)", re.IGNORECASE)
_SAFE_TARGET = re.compile(r"^(?:skills/[a-z0-9][a-z0-9-]*|knowledge/[a-z0-9][a-z0-9-]*\.md)$")


class LearningRecordError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def learning_file(profile_home: Path) -> Path:
    return profile_home / "learning" / "records.jsonl"


def ensure_learning_state(profile_home: Path) -> Path:
    path = learning_file(profile_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    return path


def _require_string(record: dict[str, Any], key: str, *, maximum: int) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise LearningRecordError(f"{key} must be a non-empty string.")
    value = value.strip()
    if len(value) > maximum:
        raise LearningRecordError(f"{key} must be at most {maximum} characters.")
    return value


def _redaction_findings(value: Any, path: str = "record") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            findings.extend(_redaction_findings(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(_redaction_findings(child, f"{path}[{index}]"))
    elif isinstance(value, str):
        checks = [
            ("email address", _EMAIL),
            ("GUID or tenant identifier", _GUID),
            ("IP address", _IPV4),
            ("secret or token-shaped value", _SECRET),
            ("user-specific absolute path", _ABSOLUTE_PATH),
        ]
        for label, pattern in checks:
            if pattern.search(value):
                findings.append(f"{path} contains a {label}.")
    return findings


def validate_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "classification",
        "title",
        "generalizedLearning",
        "rationale",
        "evidence",
        "confidence",
        "proposedTarget",
    }
    unexpected = sorted(set(candidate) - allowed)
    if unexpected:
        raise LearningRecordError(f"Unexpected fields: {', '.join(unexpected)}.")

    classification = candidate.get("classification")
    if classification not in TRANSFERABLE_CLASSIFICATIONS:
        raise LearningRecordError(
            "classification must be transferable_procedural or transferable_domain; private and cache learnings stay local."
        )
    title = _require_string(candidate, "title", maximum=120)
    generalized = _require_string(candidate, "generalizedLearning", maximum=2000)
    rationale = _require_string(candidate, "rationale", maximum=1000)

    evidence_value = candidate.get("evidence")
    if not isinstance(evidence_value, list) or not 1 <= len(evidence_value) <= 5:
        raise LearningRecordError("evidence must contain between 1 and 5 summarized evidence items.")
    evidence: list[dict[str, str]] = []
    for index, item in enumerate(evidence_value):
        if not isinstance(item, dict) or set(item) != {"sourceType", "summary"}:
            raise LearningRecordError(f"evidence[{index}] must contain only sourceType and summary.")
        source_type = item.get("sourceType")
        if source_type not in SOURCE_TYPES:
            raise LearningRecordError(f"evidence[{index}].sourceType is invalid.")
        summary = item.get("summary")
        if not isinstance(summary, str) or not summary.strip() or len(summary.strip()) > 500:
            raise LearningRecordError(f"evidence[{index}].summary must be 1-500 characters.")
        evidence.append({"sourceType": source_type, "summary": summary.strip()})

    confidence = candidate.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise LearningRecordError("confidence must be a number from 0 to 1.")

    proposed_target = candidate.get("proposedTarget")
    if not isinstance(proposed_target, dict) or set(proposed_target) != {"kind", "path"}:
        raise LearningRecordError("proposedTarget must contain only kind and path.")
    target_kind = proposed_target.get("kind")
    target_path = proposed_target.get("path")
    if target_kind not in TARGET_KINDS or not isinstance(target_path, str) or not _SAFE_TARGET.fullmatch(target_path):
        raise LearningRecordError("proposedTarget must use a safe skills/<name> or knowledge/<name>.md path.")
    expected_prefix = "skills/" if target_kind == "skill" else "knowledge/"
    if not target_path.startswith(expected_prefix):
        raise LearningRecordError("proposedTarget kind does not match its path.")

    normalized = {
        "classification": classification,
        "title": title,
        "generalizedLearning": generalized,
        "rationale": rationale,
        "evidence": evidence,
        "confidence": float(confidence),
        "proposedTarget": {"kind": target_kind, "path": target_path},
    }
    findings = _redaction_findings(normalized)
    if findings:
        raise LearningRecordError("Redaction rejected the record: " + " ".join(findings))
    return normalized


def append_candidate(profile_home: Path, candidate: dict[str, Any]) -> dict[str, Any]:
    normalized = validate_candidate(candidate)
    path = ensure_learning_state(profile_home)
    record = {
        "schemaVersion": SCHEMA_VERSION,
        "recordId": f"lr-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}",
        "createdAt": utc_now(),
        **normalized,
        "privacy": {
            "redactionStatus": "passed",
            "warnings": [],
        },
    }
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, separators=(",", ":"), ensure_ascii=True) + "\n")
    return record


def append_candidates(profile_home: Path, candidates: list[Any]) -> dict[str, Any]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        try:
            if not isinstance(candidate, dict):
                raise LearningRecordError("candidate is not a JSON object.")
            accepted.append(append_candidate(profile_home, candidate))
        except LearningRecordError as exc:
            rejected.append({"index": index, "reason": str(exc)})
    return {"accepted": accepted, "rejected": rejected}


def validate_stored_record(record: dict[str, Any]) -> dict[str, Any]:
    required = {
        "schemaVersion",
        "recordId",
        "createdAt",
        "classification",
        "title",
        "generalizedLearning",
        "rationale",
        "evidence",
        "confidence",
        "proposedTarget",
        "privacy",
    }
    if set(record) != required:
        raise LearningRecordError("Stored record fields do not match schema version 1.0.")
    if record.get("schemaVersion") != SCHEMA_VERSION:
        raise LearningRecordError(f"Unsupported schemaVersion {record.get('schemaVersion')!r}.")
    if not isinstance(record.get("recordId"), str) or not record["recordId"].startswith("lr-"):
        raise LearningRecordError("recordId is invalid.")
    if not isinstance(record.get("createdAt"), str) or not record["createdAt"].endswith("Z"):
        raise LearningRecordError("createdAt must be a UTC timestamp.")
    privacy = record.get("privacy")
    if privacy != {"redactionStatus": "passed", "warnings": []}:
        raise LearningRecordError("privacy must record a successful deterministic redaction check.")
    validate_candidate({key: record[key] for key in required if key not in {"schemaVersion", "recordId", "createdAt", "privacy"}})
    return record


def build_learning_packet(profile_home: Path) -> dict[str, Any]:
    path = ensure_learning_state(profile_home)
    records: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise LearningRecordError("record is not a JSON object.")
            records.append(validate_stored_record(payload))
        except (json.JSONDecodeError, LearningRecordError) as exc:
            rejected.append({"line": line_number, "reason": str(exc)})

    manifest_path = profile_home / "local" / "autopilots-instance.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    return {
        "packetVersion": PACKET_VERSION,
        "generatedAt": utc_now(),
        "instance": {
            "instanceId": manifest.get("instanceId", os.getenv("AUTOPILOT_INSTANCE_ID", "")),
            "assigneeScope": manifest.get("assigneeScope", os.getenv("HERMES_ASSIGNEE_SCOPE", "")),
        },
        "blueprint": {
            "name": manifest.get("blueprintName", os.getenv("HERMES_BLUEPRINT_NAME", "")),
            "version": manifest.get("blueprintVersion", os.getenv("HERMES_BLUEPRINT_VERSION", "")),
            "commit": manifest.get("blueprintCommit", os.getenv("HERMES_BLUEPRINT_COMMIT", "")),
        },
        "records": records,
        "rejectedRecords": rejected,
        "privatePathsExcluded": PRIVATE_EXCLUSIONS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and store transferable Hermes learning records.")
    parser.add_argument("--profile-home", default=os.getenv("HERMES_HOME", ""))
    subparsers = parser.add_subparsers(dest="command", required=True)
    append = subparsers.add_parser("append")
    append.add_argument("--record", required=True, help="Candidate record as one JSON object.")
    subparsers.add_parser("packet")
    args = parser.parse_args()

    if not args.profile_home:
        raise SystemExit("--profile-home or HERMES_HOME is required.")
    profile_home = Path(args.profile_home)
    if args.command == "append":
        candidate = json.loads(args.record)
        if not isinstance(candidate, dict):
            raise LearningRecordError("--record must be a JSON object.")
        result = append_candidate(profile_home, candidate)
    else:
        result = build_learning_packet(profile_home)
    print(json.dumps(result, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
