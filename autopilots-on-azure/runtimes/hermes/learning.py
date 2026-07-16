from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA_VERSION = "2.0"
PACKET_VERSION = "2.0"
ROLE_SKILLS_ROOT = PurePosixPath("skills/role")
PRIVATE_PLAYBOOKS_ROOT = PurePosixPath("skills/private")
CANDIDATE_IMPROVEMENTS_ROOT = PurePosixPath("skills/candidates")
GOVERNED_ROOTS = (ROLE_SKILLS_ROOT, CANDIDATE_IMPROVEMENTS_ROOT)
ALL_A10_ROOTS = (*GOVERNED_ROOTS, PRIVATE_PLAYBOOKS_ROOT)
CLASSIFICATIONS = {"role_skill_improvement", "candidate_improvement"}
SOURCE_STAGES = {"foreground", "dream", "background_review", "operator"}
SOURCE_TYPES = {"private_session", "tool_result", "public_source"}
SOURCE_TYPE_ALIASES = {
    "session": "private_session",
    "recent_session": "private_session",
    "local_session": "private_session",
    "private_session_summary": "private_session",
    "tool": "tool_result",
    "tool_output": "tool_result",
    "mcp_result": "tool_result",
    "public": "public_source",
    "documentation": "public_source",
}
PRIVATE_EXCLUSIONS = [
    ".env",
    "auth/",
    "logs/",
    "learning/quarantine/",
    "memories/",
    "sessions/",
    "skills/private/",
    "state.db",
    "state.db-shm",
    "state.db-wal",
    "workspace/",
]
MAX_SNAPSHOT_FILE_BYTES = 1_000_000
LEARNING_LEASE_SECONDS = 900
GOVERNED_TEXT_SUFFIXES = {
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".py",
    ".sh",
    ".ps1",
    ".js",
    ".ts",
}

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
_ARTIFACT_PATH = re.compile(r"^skills/(?:role|candidates)/[a-z0-9][a-z0-9-]{0,62}$")


class LearningRecordError(ValueError):
    pass


class DuplicateLearningRecord(LearningRecordError):
    def __init__(self, record_id: str) -> None:
        super().__init__(f"Duplicate of existing provenance record {record_id}.")
        self.record_id = record_id


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def provenance_file(profile_home: Path) -> Path:
    return profile_home / "learning" / "records.jsonl"


def worker_manifest_path(profile_home: Path) -> Path:
    return profile_home / "local" / "worker.json"


def governed_ledger_path(profile_home: Path) -> Path:
    return profile_home / "learning" / "governed-state.json"


def ensure_learning_state(profile_home: Path) -> Path:
    path = provenance_file(profile_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    (profile_home / "learning" / "pending").mkdir(parents=True, exist_ok=True)
    (profile_home / "learning" / "exports").mkdir(parents=True, exist_ok=True)
    for root in ALL_A10_ROOTS:
        profile_home.joinpath(*root.parts).mkdir(parents=True, exist_ok=True)
    return path


def assert_legacy_state_migrated(profile_home: Path) -> None:
    private_cache = profile_home / "local" / "private-cache.md"
    if private_cache.exists() and private_cache.read_text(encoding="utf-8").strip():
        raise RuntimeError(
            "Legacy local/private-cache.md must be converted to a Private Playbook before Role Release 3.0."
        )
    if private_cache.exists():
        private_cache.unlink()
    hot_learning = profile_home / "skills" / "hot-learning"
    if hot_learning.exists():
        raise RuntimeError(
            "Legacy skills/hot-learning must be converted to Candidate Improvements before Role Release 3.0."
        )
    path = provenance_file(profile_home)
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Legacy learning journal contains invalid JSON.") from exc
        if payload.get("schemaVersion") != SCHEMA_VERSION:
            raise RuntimeError(
                "Legacy learning/records.jsonl must be archived or migrated before Role Release 3.0."
            )


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
            ("internal URL", _INTERNAL_URL),
            ("secret or token-shaped value", _SECRET),
            ("user-specific absolute path", _ABSOLUTE_PATH),
        ]
        for label, pattern in checks:
            if pattern.search(value):
                findings.append(f"{path} contains a {label}.")
    return findings


def _safe_relative_path(value: str) -> PurePosixPath:
    relative = PurePosixPath(value.replace("\\", "/").strip("/"))
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise LearningRecordError(f"Unsafe artifact path {value!r}.")
    return relative


def _artifact_root_for_file(relative: str) -> str | None:
    path = _safe_relative_path(relative)
    if len(path.parts) < 4 or path.parts[0] != "skills":
        return None
    if path.parts[1] not in {"role", "private", "candidates"}:
        return None
    return PurePosixPath(*path.parts[:3]).as_posix()


def _namespace_for_artifact(artifact_path: str) -> str:
    path = _safe_relative_path(artifact_path)
    if len(path.parts) != 3 or path.parts[0] != "skills":
        raise LearningRecordError("artifactPath must identify one skill directory.")
    return path.parts[1]


def _file_snapshot(profile_home: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for root in ALL_A10_ROOTS:
        directory = profile_home.joinpath(*root.parts)
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise LearningRecordError(f"Skill namespaces cannot contain symlinks: {path}.")
            if not path.is_file() or path.name == ".usage.json":
                continue
            data = path.read_bytes()
            if len(data) > MAX_SNAPSHOT_FILE_BYTES:
                raise LearningRecordError(f"Skill file exceeds {MAX_SNAPSHOT_FILE_BYTES} bytes: {path}.")
            relative = path.relative_to(profile_home).as_posix()
            snapshot[relative] = base64.b64encode(data).decode("ascii")
    return snapshot


def _governed_snapshot(snapshot: dict[str, str]) -> dict[str, str]:
    return {
        path: content
        for path, content in snapshot.items()
        if path.startswith("skills/role/") or path.startswith("skills/candidates/")
    }


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_governed_ledger_snapshot(
    profile_home: Path,
    files: dict[str, str],
) -> None:
    manifest = _load_worker_manifest(profile_home)
    _write_json_atomic(
        governed_ledger_path(profile_home),
        {
            "ledgerVersion": "1.0",
            "roleReleaseCommit": manifest["roleReleaseCommit"],
            "files": files,
            "updatedAt": utc_now(),
        },
    )


def initialize_governed_state(profile_home: Path) -> None:
    manifest = _load_worker_manifest(profile_home)
    path = governed_ledger_path(profile_home)
    existing: dict[str, Any] = {}
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            existing = payload
    if existing.get("roleReleaseCommit") == manifest["roleReleaseCommit"]:
        return
    _write_governed_ledger_snapshot(
        profile_home,
        _governed_snapshot(_file_snapshot(profile_home)),
    )


def _update_governed_ledger(profile_home: Path) -> None:
    _write_governed_ledger_snapshot(
        profile_home,
        _governed_snapshot(_file_snapshot(profile_home)),
    )


def _recover_unprovenanced_drift(profile_home: Path) -> list[str]:
    initialize_governed_state(profile_home)
    ledger = json.loads(governed_ledger_path(profile_home).read_text(encoding="utf-8"))
    expected = _decoded_snapshot(ledger["files"])
    current = _decoded_snapshot(_governed_snapshot(_file_snapshot(profile_home)))
    changed = _changed_files(expected, current)
    if changed:
        quarantine = profile_home / "learning" / "quarantine"
        quarantine.mkdir(parents=True, exist_ok=True)
        (quarantine / f"unprovenanced-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json").write_text(
            json.dumps(
                {
                    "quarantineVersion": "1.0",
                    "createdAt": utc_now(),
                    "changedFiles": changed,
                    "observedFiles": {
                        path: base64.b64encode(content).decode("ascii")
                        for path, content in current.items()
                        if path in changed
                    },
                    "reason": "Governed skill drift was observed outside a reconciled foreground or Dreaming turn.",
                },
                separators=(",", ":"),
                ensure_ascii=True,
            )
            + "\n",
            encoding="utf-8",
        )
        _restore_governed_files(profile_home, expected)
    return changed


def _decoded_snapshot(snapshot: dict[str, str]) -> dict[str, bytes]:
    return {path: base64.b64decode(content.encode("ascii")) for path, content in snapshot.items()}


def _changed_files(before: dict[str, bytes], after: dict[str, bytes]) -> list[str]:
    return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))


def _artifact_files(snapshot: dict[str, bytes], artifact_path: str) -> dict[str, bytes]:
    prefix = f"{artifact_path.rstrip('/')}/"
    return {path: content for path, content in snapshot.items() if path.startswith(prefix)}


def _artifact_hash(files: dict[str, bytes]) -> str | None:
    if not files:
        return None
    digest = hashlib.sha256()
    for path, content in sorted(files.items()):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def _skill_content_for_dlp(files: dict[str, bytes]) -> dict[str, str]:
    content: dict[str, str] = {}
    for path, value in files.items():
        suffix = Path(path).suffix.lower()
        if suffix not in GOVERNED_TEXT_SUFFIXES:
            raise LearningRecordError(f"Governed skill artifact contains unsupported binary file: {path}.")
        try:
            content[path] = value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LearningRecordError(f"Governed skill artifact is not UTF-8 text: {path}.") from exc
    return content


def _load_worker_manifest(profile_home: Path) -> dict[str, Any]:
    path = worker_manifest_path(profile_home)
    if not path.is_file():
        raise LearningRecordError(f"Worker manifest is missing: {path}.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise LearningRecordError("Worker manifest must contain one JSON object.")
    return payload


def _journal_record_ids(profile_home: Path) -> set[str]:
    ids: set[str] = set()
    path = provenance_file(profile_home)
    if not path.exists():
        return ids
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        record_id = payload.get("recordId") if isinstance(payload, dict) else None
        if isinstance(record_id, str):
            ids.add(record_id)
    return ids


def _remove_journal_records(profile_home: Path, record_ids: set[str]) -> None:
    path = provenance_file(profile_home)
    retained: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            retained.append(line)
            continue
        if not isinstance(payload, dict) or payload.get("recordId") not in record_ids:
            retained.append(line)
    temporary = path.with_suffix(".jsonl.tmp")
    temporary.write_text(("\n".join(retained) + "\n") if retained else "", encoding="utf-8")
    temporary.replace(path)


def _recover_pending_transaction(profile_home: Path, path: Path) -> None:
    transaction = json.loads(path.read_text(encoding="utf-8"))
    before = _decoded_snapshot(transaction["files"])
    commit_record_ids = set(transaction.get("commitRecordIds") or [])
    after_files = transaction.get("afterGovernedFiles")
    if commit_record_ids and isinstance(after_files, dict):
        existing = _journal_record_ids(profile_home)
        if commit_record_ids <= existing:
            _restore_governed_files(profile_home, _decoded_snapshot(after_files))
            _write_governed_ledger_snapshot(profile_home, after_files)
            path.unlink()
            return
        _remove_journal_records(profile_home, commit_record_ids)
    _restore_governed_files(profile_home, before)
    path.unlink()


def _lease_path(profile_home: Path) -> Path:
    return profile_home / "learning" / "transaction.lock"


def _read_lease(profile_home: Path) -> dict[str, Any]:
    path = _lease_path(profile_home) / "lease.json"
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _release_lease(profile_home: Path, token: str) -> None:
    lock = _lease_path(profile_home)
    lease = _read_lease(profile_home)
    if lease.get("token") == token and lock.exists():
        shutil.rmtree(lock)


def _acquire_lease(profile_home: Path, token: str) -> None:
    lock = _lease_path(profile_home)
    if lock.exists():
        lease = _read_lease(profile_home)
        created = lease.get("createdAtEpoch")
        if isinstance(created, (int, float)) and time.time() - created < LEARNING_LEASE_SECONDS:
            raise LearningRecordError("Another Worker learning transaction is active.")
        pending_paths = sorted((profile_home / "learning" / "pending").glob("lt-*.json"))
        for path in pending_paths:
            _recover_pending_transaction(profile_home, path)
        shutil.rmtree(lock)
    lock.mkdir(parents=False)
    _write_json_atomic(
        lock / "lease.json",
        {
            "leaseVersion": "1.0",
            "token": token,
            "createdAt": utc_now(),
            "createdAtEpoch": time.time(),
        },
    )


def begin_learning_turn(profile_home: Path) -> dict[str, Any]:
    ensure_learning_state(profile_home)
    token = f"lt-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(6)}"
    _acquire_lease(profile_home, token)
    try:
        pending_paths = sorted((profile_home / "learning" / "pending").glob("lt-*.json"))
        for path in pending_paths:
            _recover_pending_transaction(profile_home, path)
        recovered = _recover_unprovenanced_drift(profile_home)
        validate_skill_namespaces(profile_home)
        payload = {
            "snapshotVersion": "1.0",
            "token": token,
            "createdAt": utc_now(),
            "files": _file_snapshot(profile_home),
        }
        path = profile_home / "learning" / "pending" / f"{token}.json"
        _write_json_atomic(path, payload)
    except Exception:
        _release_lease(profile_home, token)
        raise
    return {"token": token, "recoveredUnprovenancedFiles": recovered}


def abort_learning_turn(profile_home: Path, *, token: str) -> dict[str, Any]:
    if _read_lease(profile_home).get("token") != token:
        raise LearningRecordError("Learning transaction lease is missing or belongs to another turn.")
    pending_path = profile_home / "learning" / "pending" / f"{token}.json"
    if pending_path.is_file():
        transaction = json.loads(pending_path.read_text(encoding="utf-8"))
        _restore_governed_files(profile_home, _decoded_snapshot(transaction["files"]))
        commit_record_ids = set(transaction.get("commitRecordIds") or [])
        if commit_record_ids:
            _remove_journal_records(profile_home, commit_record_ids)
        pending_path.unlink()
    _release_lease(profile_home, token)
    _update_governed_ledger(profile_home)
    return {"aborted": True, "token": token}


def _restore_skill_files(
    profile_home: Path,
    before: dict[str, bytes],
    roots: tuple[PurePosixPath, ...],
) -> None:
    for root in roots:
        directory = profile_home.joinpath(*root.parts)
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)
    for relative, content in before.items():
        path = _safe_relative_path(relative)
        if PurePosixPath(*path.parts[:2]) not in roots:
            continue
        destination = profile_home.joinpath(*path.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)


def _restore_governed_files(profile_home: Path, before: dict[str, bytes]) -> None:
    _restore_skill_files(profile_home, before, GOVERNED_ROOTS)


def _validate_evidence(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 5:
        raise LearningRecordError("evidence must contain between 1 and 5 summarized evidence items.")
    evidence: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != {"sourceType", "summary"}:
            raise LearningRecordError(f"evidence[{index}] must contain only sourceType and summary.")
        source_type = item.get("sourceType")
        if isinstance(source_type, str):
            source_type = SOURCE_TYPE_ALIASES.get(source_type.strip().lower(), source_type.strip().lower())
        if source_type not in SOURCE_TYPES:
            raise LearningRecordError(f"evidence[{index}].sourceType is invalid.")
        summary = item.get("summary")
        if not isinstance(summary, str) or not summary.strip() or len(summary.strip()) > 500:
            raise LearningRecordError(f"evidence[{index}].summary must be 1-500 characters.")
        evidence.append({"sourceType": source_type, "summary": summary.strip()})
    return evidence


def _normalized_provenance(
    candidate: dict[str, Any],
    *,
    artifact_path: str,
    action: str,
) -> dict[str, Any]:
    allowed = {
        "classification",
        "artifactPath",
        "action",
        "title",
        "generalizedLearning",
        "rationale",
        "evidence",
        "confidence",
        "sourceStage",
    }
    unexpected = sorted(set(candidate) - allowed)
    if unexpected:
        raise LearningRecordError(f"Unexpected provenance fields: {', '.join(unexpected)}.")
    classification = candidate.get("classification")
    if classification not in CLASSIFICATIONS:
        raise LearningRecordError("classification must be role_skill_improvement or candidate_improvement.")
    expected_classification = (
        "role_skill_improvement" if _namespace_for_artifact(artifact_path) == "role" else "candidate_improvement"
    )
    if classification != expected_classification:
        raise LearningRecordError(
            f"{artifact_path} requires classification {expected_classification}."
        )
    if candidate.get("artifactPath") != artifact_path:
        raise LearningRecordError(f"artifactPath must be exactly {artifact_path}.")
    if candidate.get("action") != action:
        raise LearningRecordError(f"action must be {action} for {artifact_path}.")
    confidence = candidate.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise LearningRecordError("confidence must be a number from 0 to 1.")
    source_stage = candidate.get("sourceStage")
    if source_stage not in SOURCE_STAGES:
        raise LearningRecordError(f"sourceStage must be one of: {', '.join(sorted(SOURCE_STAGES))}.")
    normalized = {
        "classification": classification,
        "artifactPath": artifact_path,
        "action": action,
        "title": _require_string(candidate, "title", maximum=120),
        "generalizedLearning": _require_string(candidate, "generalizedLearning", maximum=2000),
        "rationale": _require_string(candidate, "rationale", maximum=1000),
        "evidence": _validate_evidence(candidate.get("evidence")),
        "confidence": float(confidence),
        "sourceStage": source_stage,
    }
    findings = _redaction_findings(normalized)
    if findings:
        raise LearningRecordError("Redaction rejected the provenance: " + " ".join(findings))
    return normalized


def _change_action(before_files: dict[str, bytes], after_files: dict[str, bytes]) -> str:
    if not before_files and after_files:
        return "create"
    if before_files and not after_files:
        return "delete"
    return "patch"


def _append_records_atomic(profile_home: Path, records: list[dict[str, Any]]) -> None:
    path = provenance_file(profile_home)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    additions = "".join(
        json.dumps(record, separators=(",", ":"), ensure_ascii=True) + "\n"
        for record in records
    )
    temporary = path.with_suffix(".jsonl.tmp")
    temporary.write_text(existing + additions, encoding="utf-8")
    temporary.replace(path)


def _existing_records(profile_home: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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
    return records, rejected


def reconcile_learning_turn(
    profile_home: Path,
    *,
    token: str,
    provenance: list[Any],
) -> dict[str, Any]:
    if _read_lease(profile_home).get("token") != token:
        raise LearningRecordError("Learning transaction lease is missing or belongs to another turn.")
    pending_path = profile_home / "learning" / "pending" / f"{token}.json"
    if not pending_path.is_file():
        _release_lease(profile_home, token)
        raise LearningRecordError(f"Unknown or expired learning snapshot {token}.")
    snapshot = json.loads(pending_path.read_text(encoding="utf-8"))
    before = _decoded_snapshot(snapshot["files"])
    try:
        after = _decoded_snapshot(_file_snapshot(profile_home))
    except LearningRecordError:
        _restore_skill_files(profile_home, before, ALL_A10_ROOTS)
        pending_path.unlink()
        _release_lease(profile_home, token)
        raise
    changed_files = _changed_files(before, after)
    changed_artifacts = sorted(
        {
            artifact
            for relative in changed_files
            if (artifact := _artifact_root_for_file(relative)) is not None
        }
    )
    private_playbooks = [artifact for artifact in changed_artifacts if artifact.startswith("skills/private/")]
    governed_artifacts = [
        artifact
        for artifact in changed_artifacts
        if artifact.startswith("skills/role/") or artifact.startswith("skills/candidates/")
    ]
    accepted: list[dict[str, Any]] = []
    records_to_append: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    skipped_duplicates: list[dict[str, Any]] = []
    candidates_by_artifact: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(provenance):
        if not isinstance(item, dict):
            rejected.append({"index": index, "reason": "provenance item is not a JSON object."})
            continue
        artifact_path = item.get("artifactPath")
        if not isinstance(artifact_path, str) or not _ARTIFACT_PATH.fullmatch(artifact_path):
            rejected.append({"index": index, "reason": "artifactPath must identify skills/role/<name> or skills/candidates/<name>."})
            continue
        if artifact_path in candidates_by_artifact:
            rejected.append({"index": index, "reason": f"Duplicate provenance for {artifact_path}."})
            continue
        candidates_by_artifact[artifact_path] = item

    manifest = _load_worker_manifest(profile_home)
    existing_records, invalid_records = _existing_records(profile_home)
    if invalid_records:
        rejected.extend({"reason": f"Stored provenance line {item['line']}: {item['reason']}"} for item in invalid_records)

    for artifact_path in governed_artifacts:
        before_files = _artifact_files(before, artifact_path)
        after_files = _artifact_files(after, artifact_path)
        action = _change_action(before_files, after_files)
        candidate = candidates_by_artifact.pop(artifact_path, None)
        if candidate is None:
            rejected.append({"artifactPath": artifact_path, "reason": "Governed skill change has no matching provenance."})
            continue
        try:
            normalized = _normalized_provenance(candidate, artifact_path=artifact_path, action=action)
            content = _skill_content_for_dlp(after_files)
            findings = _redaction_findings(content, "artifact")
            if findings:
                raise LearningRecordError("Redaction rejected the skill artifact: " + " ".join(findings))
            before_hash = _artifact_hash(before_files)
            after_hash = _artifact_hash(after_files)
            duplicate = next(
                (
                    record
                    for record in existing_records
                    if record["artifact"]["path"] == artifact_path
                    and record["artifact"]["afterHash"] == after_hash
                ),
                None,
            )
            if duplicate:
                skipped_duplicates.append({"artifactPath": artifact_path, "recordId": duplicate["recordId"]})
                continue
            record = {
                "schemaVersion": SCHEMA_VERSION,
                "recordId": f"lr-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}",
                "createdAt": utc_now(),
                **normalized,
                "artifact": {
                    "path": artifact_path,
                    "beforeHash": before_hash,
                    "afterHash": after_hash,
                    "changedFiles": [
                        path
                        for path in changed_files
                        if path.startswith(f"{artifact_path}/")
                    ],
                },
                "roleRelease": {
                    "roleBlueprint": manifest["roleBlueprint"],
                    "release": manifest["roleRelease"],
                    "commit": manifest["roleReleaseCommit"],
                },
                "worker": {
                    "workerId": manifest["workerId"],
                    "assignmentScope": manifest["assignmentScope"],
                },
                "privacy": {"redactionStatus": "passed", "warnings": []},
            }
            records_to_append.append(record)
        except LearningRecordError as exc:
            rejected.append({"artifactPath": artifact_path, "reason": str(exc)})

    for artifact_path in sorted(candidates_by_artifact):
        current_hash = _artifact_hash(_artifact_files(after, artifact_path))
        duplicate = next(
            (
                record
                for record in existing_records
                if record["artifact"]["path"] == artifact_path
                and record["artifact"]["afterHash"] == current_hash
            ),
            None,
        )
        if duplicate:
            skipped_duplicates.append(
                {"artifactPath": artifact_path, "recordId": duplicate["recordId"]}
            )
        else:
            rejected.append(
                {
                    "artifactPath": artifact_path,
                    "reason": "Provenance has no matching governed skill change.",
                }
            )

    if rejected and governed_artifacts:
        _restore_governed_files(profile_home, before)
        records_to_append = []
        skipped_duplicates = []

    try:
        validate_skill_namespaces(profile_home)
    except LearningRecordError:
        _restore_skill_files(profile_home, before, ALL_A10_ROOTS)
        pending_path.unlink()
        _release_lease(profile_home, token)
        raise

    if records_to_append:
        transaction = json.loads(pending_path.read_text(encoding="utf-8"))
        transaction["commitRecordIds"] = [record["recordId"] for record in records_to_append]
        transaction["afterGovernedFiles"] = {
            path: base64.b64encode(content).decode("ascii")
            for path, content in after.items()
            if path.startswith("skills/role/") or path.startswith("skills/candidates/")
        }
        _write_json_atomic(pending_path, transaction)
        _append_records_atomic(profile_home, records_to_append)
        accepted = records_to_append
    _update_governed_ledger(profile_home)
    pending_path.unlink()
    _release_lease(profile_home, token)
    return {
        "accepted": accepted,
        "rejected": rejected,
        "skippedDuplicates": skipped_duplicates,
        "privatePlaybooksChanged": private_playbooks,
        "governedArtifactsChanged": governed_artifacts,
        "rolledBack": bool(rejected and governed_artifacts),
    }


def validate_stored_record(record: dict[str, Any]) -> dict[str, Any]:
    required = {
        "schemaVersion",
        "recordId",
        "createdAt",
        "classification",
        "artifactPath",
        "action",
        "title",
        "generalizedLearning",
        "rationale",
        "evidence",
        "confidence",
        "sourceStage",
        "artifact",
        "roleRelease",
        "worker",
        "privacy",
    }
    if set(record) != required:
        raise LearningRecordError("Stored provenance fields do not match schema version 2.0.")
    if record.get("schemaVersion") != SCHEMA_VERSION:
        raise LearningRecordError(f"Unsupported schemaVersion {record.get('schemaVersion')!r}.")
    if not isinstance(record.get("recordId"), str) or not record["recordId"].startswith("lr-"):
        raise LearningRecordError("recordId is invalid.")
    if not isinstance(record.get("createdAt"), str) or not record["createdAt"].endswith("Z"):
        raise LearningRecordError("createdAt must be a UTC timestamp.")
    if record.get("privacy") != {"redactionStatus": "passed", "warnings": []}:
        raise LearningRecordError("privacy must record a successful redaction check.")
    artifact = record.get("artifact")
    if not isinstance(artifact, dict) or set(artifact) != {"path", "beforeHash", "afterHash", "changedFiles"}:
        raise LearningRecordError("artifact metadata is invalid.")
    if artifact.get("path") != record.get("artifactPath"):
        raise LearningRecordError("artifact.path must equal artifactPath.")
    role_release = record.get("roleRelease")
    if not isinstance(role_release, dict) or set(role_release) != {"roleBlueprint", "release", "commit"}:
        raise LearningRecordError("roleRelease metadata is invalid.")
    worker = record.get("worker")
    if not isinstance(worker, dict) or set(worker) != {"workerId", "assignmentScope"}:
        raise LearningRecordError("worker metadata is invalid.")
    _normalized_provenance(
        {
            key: record[key]
            for key in (
                "classification",
                "artifactPath",
                "action",
                "title",
                "generalizedLearning",
                "rationale",
                "evidence",
                "confidence",
                "sourceStage",
            )
        },
        artifact_path=record["artifactPath"],
        action=record["action"],
    )
    return record


def stored_learning_records(profile_home: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return _existing_records(profile_home)


def validate_skill_namespaces(profile_home: Path) -> None:
    names: dict[str, str] = {}
    skills_root = profile_home / "skills"
    if not skills_root.exists():
        return
    for skill_file in sorted(skills_root.rglob("SKILL.md")):
        relative = skill_file.relative_to(profile_home).as_posix()
        artifact = _artifact_root_for_file(relative) or skill_file.parent.relative_to(profile_home).as_posix()
        name = skill_file.parent.name
        previous = names.get(name)
        if previous and previous != artifact:
            raise LearningRecordError(
                f"Skill basename {name!r} collides across namespaces: {previous} and {artifact}."
            )
        names[name] = artifact


def build_learning_status(profile_home: Path) -> dict[str, Any]:
    records, rejected = stored_learning_records(profile_home)
    manifest = _load_worker_manifest(profile_home)
    return {
        "statusVersion": PACKET_VERSION,
        "generatedAt": utc_now(),
        "worker": {
            "workerId": manifest["workerId"],
            "assignmentScope": manifest["assignmentScope"],
        },
        "roleRelease": {
            "roleBlueprint": manifest["roleBlueprint"],
            "release": manifest["roleRelease"],
            "commit": manifest["roleReleaseCommit"],
        },
        "records": records,
        "rejectedRecords": rejected,
        "privatePathsExcluded": PRIVATE_EXCLUSIONS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Hermes Candidate Improvement provenance.")
    parser.add_argument("--profile-home", default=os.getenv("HERMES_HOME", ""))
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("begin")
    abort = subparsers.add_parser("abort")
    abort.add_argument("--token", required=True)
    reconcile = subparsers.add_parser("reconcile")
    reconcile.add_argument("--token", required=True)
    reconcile.add_argument("--provenance", required=True, help="Provenance as one JSON array.")
    subparsers.add_parser("packet")
    args = parser.parse_args()
    if not args.profile_home:
        raise SystemExit("--profile-home or HERMES_HOME is required.")
    profile_home = Path(args.profile_home)
    if args.command == "begin":
        result = begin_learning_turn(profile_home)
    elif args.command == "abort":
        result = abort_learning_turn(profile_home, token=args.token)
    elif args.command == "reconcile":
        provenance = json.loads(args.provenance)
        if not isinstance(provenance, list):
            raise LearningRecordError("--provenance must be a JSON array.")
        result = reconcile_learning_turn(profile_home, token=args.token, provenance=provenance)
    else:
        result = build_learning_status(profile_home)
    print(json.dumps(result, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
