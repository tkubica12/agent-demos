"""Owner-facing view and decisions over Hermes-native skill learning.

Runs inside the Hermes gateway process, where ``tools.skill_ledger``, ``tools.write_approval`` and
``tools.skill_manager_tool`` come from the sealed Hermes environment. Every public function accepts
those native modules as optional parameters; when omitted they are imported from Hermes.
"""

from __future__ import annotations

import difflib
import hashlib
import importlib
import json
import os
import re
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from blueprint import candidate_improvement_hashes, role_skill_hashes, worker_manifest_path


PRIVATE_SESSION_PREFIX = "private:"
PROTECTED_WRITE_TOOLS = frozenset({"memory", "skill_manage"})
FILE_WRITE_TOOLS = frozenset({"write_file", "patch"})
PROTECTED_STATE_DIRECTORIES = ("skills", "memories")
PRIVATE_TURN_MESSAGE = (
    "Durable memory and skill writes are disabled for this private turn because it contains attachment "
    "or persistence-disabled context. Answer without storing anything."
)
SHARE_CHOICES = ("share", "keep", "discard")
GOVERNED_ARTIFACT = re.compile(r"^skills/(role|candidates)/([a-z0-9][a-z0-9-]{0,62})$")
IDENTIFIER = re.compile(r"^[0-9a-f]{6,64}$")
ORIGINS = {"agent": "foreground", "curator": "background", "user": "owner"}
CLAIM_STALE_SECONDS = 900
DIFF_MAX_LINES = 40
DIFF_MAX_CHARACTERS = 4000
MAX_DIGEST_LIMIT = 50
PLAYBOOK_SUFFIX = "-playbook"
V4A_FILE_HEADER = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)
_state_lock = threading.Lock()


class LearningDecisionError(ValueError):
    """The request is malformed or refers to something this Worker does not have."""


class LearningConflictError(RuntimeError):
    """The current native state does not allow the requested decision."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def native_module(module: Any, name: str) -> Any:
    return module if module is not None else importlib.import_module(name)


def receipts_directory(profile_home: Path) -> Path:
    return profile_home / "learning" / "receipts"


def share_choices_path(profile_home: Path) -> Path:
    return profile_home / "learning" / "share-choices.json"


def digest_presented_path(profile_home: Path) -> Path:
    return profile_home / "learning" / "digest-presented.json"


def artifact_hash(file_hashes: dict[str, str]) -> str | None:
    """Content identity of one skill directory: SHA-256 over its sorted relative file hashes."""
    if not file_hashes:
        return None
    return hashlib.sha256(
        json.dumps(file_hashes, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def load_worker_manifest(profile_home: Path) -> dict[str, Any] | None:
    path = worker_manifest_path(profile_home)
    return _read_json(path) or None


def namespace_for(artifact_path: str | None) -> str:
    parts = (artifact_path or "").split("/")
    if len(parts) >= 3 and parts[0] == "skills" and parts[1] in {"role", "candidates", "private"}:
        return parts[1]
    return "local"


def _relative(profile_home: Path, value: str) -> str | None:
    if not value:
        return None
    try:
        relative = Path(os.path.normpath(value)).relative_to(os.path.normpath(str(profile_home)))
    except ValueError:
        return None
    return relative.as_posix()


def _file_sha(profile_home: Path, relative: str) -> str | None:
    path = profile_home.joinpath(*PurePosixPath(relative).parts)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _entry_files(profile_home: Path, entry: dict[str, Any]) -> tuple[dict[str, str], dict[str, str], list[str]]:
    before: dict[str, str] = {}
    after: dict[str, str] = {}
    outside: list[str] = []
    for section, target in (("before", before), ("after", after)):
        for item in entry.get(section) or []:
            raw = str(item.get("path") or "")
            relative = _relative(profile_home, raw)
            if relative is None:
                outside.append(raw)
            elif PurePosixPath(relative).name != ".usage.json":
                target[relative] = str(item.get("sha256") or "")
    return before, after, outside


def _entry_artifact(profile_home: Path, entry: dict[str, Any]) -> str | None:
    before, after, _ = _entry_files(profile_home, entry)
    skill = str(entry.get("skill") or "").replace("\\", "/").rsplit("/", 1)[-1]
    paths = sorted(set(before) | set(after))
    for relative in paths:
        parts = relative.split("/")
        if parts[0] == "skills" and skill in parts[1:-1]:
            return "/".join(parts[: parts.index(skill, 1) + 1])
    for relative in paths:
        parts = relative.split("/")
        if parts[0] == "skills" and len(parts) >= 3:
            return "/".join(parts[:-1])
    return None


def _on_top(state: dict[str, str], before: dict[str, str], after: dict[str, str]) -> bool:
    return all(state.get(path) == sha for path, sha in after.items()) and all(
        path not in state for path in before if path not in after
    )


def _unapply(state: dict[str, str], before: dict[str, str], after: dict[str, str]) -> dict[str, str]:
    result = {path: sha for path, sha in state.items() if path not in after}
    result.update(before)
    return result


def _blob_text(ledger: Any, sha: str | None) -> str | None:
    if not sha:
        return ""
    data = ledger.read_blob(sha)
    if data is None:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _truncate(lines: list[str]) -> tuple[str, bool]:
    kept = lines[:DIFF_MAX_LINES]
    text = "\n".join(kept)
    truncated = len(lines) > DIFF_MAX_LINES or len(text) > DIFF_MAX_CHARACTERS
    return text[:DIFF_MAX_CHARACTERS], truncated


def _entry_diff(before: dict[str, str], after: dict[str, str], ledger: Any) -> tuple[str, bool]:
    lines: list[str] = []
    for path in sorted(set(before) | set(after)):
        old = _blob_text(ledger, before.get(path))
        new = _blob_text(ledger, after.get(path))
        if old is None or new is None:
            lines.append(f"{path}: binary or unavailable content")
            continue
        lines.extend(difflib.unified_diff(
            old.splitlines(), new.splitlines(), f"a/{path}", f"b/{path}", lineterm="", n=1,
        ))
    return _truncate(lines)


def _timestamp(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _read_receipt(profile_home: Path, receipt_id: str) -> dict[str, Any] | None:
    payload = _read_json(receipts_directory(profile_home) / f"{receipt_id}.json")
    return payload or None


def learning_receipts(profile_home: Path) -> list[dict[str, Any]]:
    directory = receipts_directory(profile_home)
    if not directory.is_dir():
        return []
    return [payload for path in sorted(directory.glob("*.json")) if (payload := _read_json(path))]


@contextmanager
def _claim(profile_home: Path, receipt_id: str) -> Iterator[None]:
    path = receipts_directory(profile_home) / f"{receipt_id}.claim"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        age = time.time() - path.stat().st_mtime
    except FileNotFoundError:
        age = None
    if age is not None and age >= CLAIM_STALE_SECONDS:
        path.unlink(missing_ok=True)
    try:
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except FileExistsError:
        raise LearningConflictError("This learning decision is already in progress.") from None
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def _finish(profile_home: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    write_json_atomic(receipts_directory(profile_home) / f"{receipt['receiptId']}.json", receipt)
    return {**receipt, "duplicate": False}


@contextmanager
def _owner_actor(ledger: Any) -> Iterator[None]:
    setter = getattr(ledger, "set_ledger_actor", None)
    token = setter("user") if callable(setter) else None
    try:
        yield
    finally:
        if token is not None:
            ledger.reset_ledger_actor(token)


def _undo_blocker(profile_home: Path, entry: dict[str, Any], ledger: Any) -> str | None:
    if _read_receipt(profile_home, f"undo-{entry.get('id')}"):
        return "Already undone."
    before, after, outside = _entry_files(profile_home, entry)
    if outside:
        return "This change touched files outside the Worker profile."
    if not before and not after:
        return "No file change was recorded."
    if any(ledger.read_blob(sha) is None for sha in before.values()):
        return "The recorded content needed for undo is no longer available."
    state = {
        path: sha for path in set(before) | set(after)
        if (sha := _file_sha(profile_home, path)) is not None
    }
    if not _on_top(state, before, after):
        return "The skill changed again later; undo the newer change first."
    return None


def _skill_location(profile_home: Path, name: str) -> str | None:
    skills = profile_home / "skills"
    if not name or not skills.is_dir():
        return None
    if "/" in name and (skills / name / "SKILL.md").is_file():
        return f"skills/{name}"
    base = name.rsplit("/", 1)[-1]
    for skill_md in sorted(skills.rglob("SKILL.md")):
        relative = skill_md.parent.relative_to(profile_home).as_posix()
        if skill_md.parent.name == base and not any(part.startswith(".") for part in relative.split("/")):
            return relative
    return None


def _grouped(hashes: dict[str, str]) -> dict[str, dict[str, str]]:
    groups: dict[str, dict[str, str]] = {}
    for path, sha in hashes.items():
        groups.setdefault("/".join(path.split("/")[:3]), {})[path] = sha
    return groups


def governed_changes(profile_home: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Role Skill and Candidate Improvement directories whose files differ from the Role Release baseline."""
    baseline = manifest.get("roleSkillBaseline")
    if not isinstance(baseline, dict):
        raise LearningConflictError("Worker manifest roleSkillBaseline must be an object.")
    role_now = _grouped(role_skill_hashes(profile_home))
    role_baseline = _grouped({str(path): str(sha) for path, sha in baseline.items()})
    changes: list[dict[str, Any]] = []

    def change(root: str, classification: str, files: dict[str, str], base: dict[str, str]) -> dict[str, Any]:
        return {
            "artifactPath": root,
            "classification": classification,
            "namespace": namespace_for(root),
            "skill": root.rsplit("/", 1)[-1],
            "fileHashes": files,
            "baselineFileHashes": base,
            "artifactHash": artifact_hash(files),
        }

    for root in sorted(set(role_now) | set(role_baseline)):
        if role_now.get(root, {}) != role_baseline.get(root, {}):
            changes.append(change(root, "role_skill_improvement", role_now.get(root, {}), role_baseline.get(root, {})))
    for root, files in sorted(_grouped(candidate_improvement_hashes(profile_home)).items()):
        changes.append(change(root, "candidate_improvement", files, {}))
    return changes


def _revert_plan(
    profile_home: Path, change: dict[str, Any], ledger: Any, entries: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    prefix = f"{change['artifactPath']}/"
    state = dict(change["fileHashes"])
    target = dict(change["baselineFileHashes"])
    plan: list[dict[str, Any]] = []
    for entry in ledger.list_entries() if entries is None else entries:
        if state == target:
            break
        if entry.get("action") == "pre-rollback":
            continue
        before, after, outside = _entry_files(profile_home, entry)
        touched = set(before) | set(after)
        if not any(path.startswith(prefix) for path in touched):
            continue
        if outside or any(not path.startswith(prefix) for path in touched):
            raise LearningConflictError(
                f"Ledger entry {entry.get('id')} also changed files outside {change['artifactPath']}."
            )
        if not _on_top(state, before, after):
            raise LearningConflictError(
                f"{change['artifactPath']} changed without a matching native ledger entry."
            )
        if any(ledger.read_blob(sha) is None for sha in before.values()):
            raise LearningConflictError(
                f"Ledger entry {entry.get('id')} for {change['artifactPath']} lost the content needed for rollback."
            )
        state = _unapply(state, before, after)
        plan.append(entry)
    if state != target:
        raise LearningConflictError(
            f"The native ledger does not trace {change['artifactPath']} back to the Role Release baseline."
        )
    return plan


def ledger_chain(
    profile_home: Path, change: dict[str, Any], *, ledger: Any = None, entries: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Native ledger entries, oldest first, that lead from the Role Release baseline to the current files."""
    return list(reversed(_revert_plan(profile_home, change, native_module(ledger, "tools.skill_ledger"), entries)))


def share_choices(profile_home: Path) -> dict[str, dict[str, Any]]:
    payload = _read_json(share_choices_path(profile_home))
    return {key: value for key, value in payload.items() if isinstance(value, dict)}


def current_share_choice(
    profile_home: Path, change: dict[str, Any], manifest: dict[str, Any],
) -> dict[str, Any] | None:
    choice = share_choices(profile_home).get(change["artifactPath"])
    if not choice or choice.get("roleReleaseCommit") != manifest.get("roleReleaseCommit"):
        return None
    return {**choice, "current": choice.get("artifactHash") == change["artifactHash"]}


def _change_item(profile_home: Path, entry: dict[str, Any], ledger: Any, presented_after: float) -> dict[str, Any]:
    before, after, _ = _entry_files(profile_home, entry)
    artifact = _entry_artifact(profile_home, entry)
    diff, truncated = _entry_diff(before, after, ledger)
    blocker = _undo_blocker(profile_home, entry, ledger)
    actor = str(entry.get("actor") or "")
    return {
        "entryId": str(entry.get("id") or ""),
        "ts": str(entry.get("ts") or ""),
        "skill": str(entry.get("skill") or ""),
        "artifactPath": artifact,
        "namespace": namespace_for(artifact),
        "action": str(entry.get("action") or ""),
        "actor": actor,
        "origin": ORIGINS.get(actor, actor or "unknown"),
        "diff": diff,
        "diffTruncated": truncated,
        "undoable": blocker is None,
        "undoBlockedReason": blocker,
        "undoReceipt": _read_receipt(profile_home, f"undo-{entry.get('id')}"),
        "new": _timestamp(entry.get("ts")) > presented_after,
    }


def _pending_item(profile_home: Path, record: dict[str, Any], approvals: Any, presented: set[str]) -> dict[str, Any]:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    name = str(payload.get("name") or "")
    action = str(record.get("action") or payload.get("action") or "")
    category = str(payload.get("category") or "")
    artifact = f"skills/{category}/{name}" if action == "create" and category else _skill_location(profile_home, name)
    try:
        diff, truncated = _truncate(str(approvals.skill_pending_diff(record)).splitlines())
    except Exception as exc:
        diff, truncated = f"Diff unavailable: {exc}", False
    created = record.get("created_at")
    return {
        "pendingId": str(record.get("id") or ""),
        "action": action,
        "skill": name,
        "artifactPath": artifact,
        "namespace": namespace_for(artifact),
        "summary": str(record.get("summary") or ""),
        "origin": str(record.get("origin") or "foreground"),
        "createdAt": (
            datetime.fromtimestamp(float(created), tz=timezone.utc).isoformat().replace("+00:00", "Z")
            if isinstance(created, (int, float)) else ""
        ),
        "diff": diff,
        "diffTruncated": truncated,
        "new": str(record.get("id") or "") not in presented,
    }


def learning_digest(
    profile_home: Path,
    *,
    limit: int = 20,
    mark_presented: bool = False,
    ledger: Any = None,
    approvals: Any = None,
) -> dict[str, Any]:
    """D2 digest: recent native skill changes with Undo, staged writes, and sharing choices for governed changes."""
    if not 1 <= limit <= MAX_DIGEST_LIMIT:
        raise LearningDecisionError(f"limit must be between 1 and {MAX_DIGEST_LIMIT}.")
    ledger = native_module(ledger, "tools.skill_ledger")
    approvals = native_module(approvals, "tools.write_approval")
    presented = _read_json(digest_presented_path(profile_home))
    presented_after = _timestamp(presented.get("ledgerTs")) if presented.get("ledgerTs") else 0.0
    all_entries = ledger.list_entries()
    entries = [entry for entry in all_entries if entry.get("action") != "pre-rollback"]
    changes = [_change_item(profile_home, entry, ledger, presented_after) for entry in entries[:limit]]
    presented_pending = {str(value) for value in presented.get("pendingIds") or []}
    pending = [
        _pending_item(profile_home, record, approvals, presented_pending)
        for record in approvals.list_pending("skills")
    ]
    strict = getattr(approvals, "write_approval_enabled", None)
    manifest = load_worker_manifest(profile_home)
    governed: list[dict[str, Any]] = []
    if manifest:
        presented_governed = presented.get("governed") if isinstance(presented.get("governed"), dict) else {}
        for change in governed_changes(profile_home, manifest):
            try:
                _revert_plan(profile_home, change, ledger, all_entries)
                trace_reason = None
            except LearningConflictError as exc:
                trace_reason = str(exc)
            governed.append({
                **{key: change[key] for key in ("artifactPath", "classification", "namespace", "skill", "artifactHash")},
                "files": sorted(change["fileHashes"]),
                "choice": current_share_choice(profile_home, change, manifest),
                "traceComplete": trace_reason is None,
                "traceReason": trace_reason,
                "new": change["artifactPath"] not in presented_governed
                or presented_governed[change["artifactPath"]] != change["artifactHash"],
            })
    digest = {
        "digestVersion": "1.0",
        "generatedAt": utc_now(),
        "worker": {
            "workerId": manifest.get("workerId"),
            "roleRelease": manifest.get("roleRelease"),
            "roleReleaseCommit": manifest.get("roleReleaseCommit"),
        } if manifest else None,
        "strictMode": bool(strict("skills")) if callable(strict) else None,
        "changes": changes,
        "pending": pending,
        "governed": governed,
        "newCount": sum(item["new"] for item in (*changes, *pending, *governed)),
    }
    if mark_presented:
        with _state_lock:
            write_json_atomic(digest_presented_path(profile_home), {
                "presentedAt": digest["generatedAt"],
                "ledgerTs": str(entries[0].get("ts") or "") if entries else presented.get("ledgerTs"),
                "pendingIds": [item["pendingId"] for item in pending],
                "governed": {item["artifactPath"]: item["artifactHash"] for item in governed},
            })
    return digest


def _required(value: str, name: str) -> str:
    value = value.strip()
    if not value or len(value) > 200:
        raise LearningDecisionError(f"{name} is required and must be at most 200 characters.")
    return value


def undo_entry(profile_home: Path, *, entry_id: str, decided_by: str, ledger: Any = None) -> dict[str, Any]:
    """Undo one native skill change with ``rollback_entry`` and store a durable, idempotent receipt."""
    if not IDENTIFIER.fullmatch(entry_id):
        raise LearningDecisionError("entryId must be a native ledger entry ID.")
    decided_by = _required(decided_by, "decidedBy")
    ledger = native_module(ledger, "tools.skill_ledger")
    receipt_id = f"undo-{entry_id}"
    if existing := _read_receipt(profile_home, receipt_id):
        return {**existing, "duplicate": True}
    with _claim(profile_home, receipt_id):
        entry = next((item for item in ledger.list_entries() if item.get("id") == entry_id), None)
        if entry is None or entry.get("action") == "pre-rollback":
            raise LearningDecisionError(f"No undoable native ledger entry has ID {entry_id}.")
        if blocker := _undo_blocker(profile_home, entry, ledger):
            raise LearningConflictError(blocker)
        before, after, _ = _entry_files(profile_home, entry)
        with _owner_actor(ledger):
            ok, message = ledger.rollback_entry(entry_id)
        if not ok:
            raise LearningConflictError(f"Native rollback failed: {message}")
        artifact = _entry_artifact(profile_home, entry)
        return _finish(profile_home, {
            "receiptVersion": "1.0",
            "receiptId": receipt_id,
            "kind": "undo",
            "entryId": entry_id,
            "skill": str(entry.get("skill") or ""),
            "artifactPath": artifact,
            "namespace": namespace_for(artifact),
            "action": str(entry.get("action") or ""),
            "actor": str(entry.get("actor") or ""),
            "restoredFileHashes": before,
            "revertedFileHashes": after,
            "message": message,
            "decidedBy": decided_by,
            "decidedAt": utc_now(),
        })


def _ledger_ids(ledger: Any) -> set[str]:
    return {str(entry.get("id")) for entry in ledger.list_entries(limit=50)}


def _apply_native_write(skill_manager: Any, payload: dict[str, Any]) -> dict[str, Any]:
    raw = skill_manager.apply_skill_pending(payload)
    result = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(result, dict) or not result.get("success"):
        detail = result.get("error") if isinstance(result, dict) else result
        raise LearningConflictError(f"Hermes rejected {payload.get('action')} of {payload.get('name')}: {detail}")
    return result


def decide_pending(
    profile_home: Path,
    *,
    pending_id: str,
    approve: bool,
    decided_by: str,
    approvals: Any = None,
    skill_manager: Any = None,
    ledger: Any = None,
) -> dict[str, Any]:
    """Approve (``apply_skill_pending``) or reject (``discard_pending``) one staged skill write."""
    if not IDENTIFIER.fullmatch(pending_id):
        raise LearningDecisionError("pendingId must be a native pending write ID.")
    decided_by = _required(decided_by, "decidedBy")
    approvals = native_module(approvals, "tools.write_approval")
    ledger = native_module(ledger, "tools.skill_ledger")
    receipt_id = f"pending-{pending_id}"
    if existing := _read_receipt(profile_home, receipt_id):
        return {**existing, "duplicate": True}
    with _claim(profile_home, receipt_id):
        record = approvals.get_pending("skills", pending_id)
        if not isinstance(record, dict):
            raise LearningDecisionError(f"No staged skill write has ID {pending_id}; it may have been decided elsewhere.")
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        ledger_entries: list[str] = []
        if approve:
            known = _ledger_ids(ledger)
            with _owner_actor(ledger):
                result = _apply_native_write(native_module(skill_manager, "tools.skill_manager_tool"), payload)
            approvals.discard_pending("skills", pending_id)
            ledger_entries = sorted(_ledger_ids(ledger) - known)
            message = str(result.get("message") or "Applied.")
        elif approvals.discard_pending("skills", pending_id):
            message = "Discarded."
        else:
            raise LearningConflictError(f"Staged skill write {pending_id} disappeared before it could be rejected.")
        name = str(payload.get("name") or "")
        return _finish(profile_home, {
            "receiptVersion": "1.0",
            "receiptId": receipt_id,
            "kind": "pending",
            "pendingId": pending_id,
            "approved": approve,
            "action": str(record.get("action") or payload.get("action") or ""),
            "skill": name,
            "summary": str(record.get("summary") or ""),
            "origin": str(record.get("origin") or "foreground"),
            "payloadHash": hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            ).hexdigest(),
            "ledgerEntryIds": ledger_entries,
            "message": message,
            "decidedBy": decided_by,
            "decidedAt": utc_now(),
        })


def _rename_skill(text: str, name: str) -> str:
    match = re.match(r"\A---\r?\n(.*?)\r?\n---", text, re.DOTALL)
    if not match or not re.search(r"(?m)^name:\s*\S", match.group(1)):
        raise LearningConflictError("SKILL.md frontmatter has no name, so it cannot become a Private Playbook.")
    frontmatter = re.sub(r"(?m)^name:\s*.*$", f"name: {name}", match.group(1), count=1)
    return text[: match.start(1)] + frontmatter + text[match.end(1):]


def private_playbook_name(skill: str) -> str:
    return f"{skill[: 63 - len(PLAYBOOK_SUFFIX)]}{PLAYBOOK_SUFFIX}"


def _artifact_texts(profile_home: Path, change: dict[str, Any]) -> dict[str, str]:
    texts: dict[str, str] = {}
    for relative in sorted(change["fileHashes"]):
        try:
            texts[relative] = profile_home.joinpath(*relative.split("/")).read_bytes().decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LearningConflictError(f"{relative} is not UTF-8 text and cannot become a Private Playbook.") from exc
    return texts


def _copy_to_private(profile_home: Path, change: dict[str, Any], skill_manager: Any) -> str:
    texts = _artifact_texts(profile_home, change)
    skill_md = f"{change['artifactPath']}/SKILL.md"
    if skill_md not in texts:
        raise LearningConflictError("Nothing to keep: the governed skill has no SKILL.md. Use Discard instead.")
    name = private_playbook_name(change["skill"])
    content = _rename_skill(texts.pop(skill_md), name)
    exists = (profile_home / "skills" / "private" / name / "SKILL.md").is_file()
    operations = [
        {"action": "patch", "name": name, "content": content} if exists
        else {"action": "create", "name": name, "category": "private", "content": content}
    ]
    operations.extend(
        {
            "action": "write_file", "name": name,
            "file_path": relative.removeprefix(f"{change['artifactPath']}/"), "file_content": text,
        }
        for relative, text in texts.items()
    )
    for operation in operations:
        _apply_native_write(skill_manager, operation)
    if not (profile_home / "skills" / "private" / name / "SKILL.md").is_file():
        raise LearningConflictError(f"Hermes did not create skills/private/{name}; check skills.create_dir.")
    return f"skills/private/{name}"


def _governed_file_hashes(profile_home: Path, artifact_path: str) -> dict[str, str]:
    hashes = role_skill_hashes(profile_home) if namespace_for(artifact_path) == "role" else candidate_improvement_hashes(
        profile_home
    )
    return {path: sha for path, sha in hashes.items() if path.startswith(f"{artifact_path}/")}


def _restore_baseline(profile_home: Path, change: dict[str, Any], plan: list[dict[str, Any]], ledger: Any) -> list[str]:
    rolled_back: list[str] = []
    with _owner_actor(ledger):
        for entry in plan:
            ok, message = ledger.rollback_entry(str(entry["id"]))
            if not ok:
                suffix = f" after rolling back {', '.join(rolled_back)}" if rolled_back else ""
                raise LearningConflictError(f"Native rollback of ledger entry {entry['id']} failed{suffix}: {message}")
            rolled_back.append(str(entry["id"]))
    if _governed_file_hashes(profile_home, change["artifactPath"]) != change["baselineFileHashes"]:
        raise LearningConflictError(
            f"{change['artifactPath']} does not match the Role Release baseline after native rollback."
        )
    directory = profile_home.joinpath(*change["artifactPath"].split("/"))
    if not change["baselineFileHashes"] and directory.is_dir():
        for path in sorted((item for item in directory.rglob("*") if item.is_dir()), reverse=True):
            if not any(path.iterdir()):
                path.rmdir()
        if not any(directory.iterdir()):
            directory.rmdir()
    return rolled_back


def _update_share_choices(profile_home: Path, artifact_path: str, value: dict[str, Any] | None) -> None:
    with _state_lock:
        choices = share_choices(profile_home)
        if value is None:
            choices.pop(artifact_path, None)
        else:
            choices[artifact_path] = value
        write_json_atomic(share_choices_path(profile_home), choices)


def record_share_choice(
    profile_home: Path,
    *,
    skill: str,
    choice: str,
    decided_by: str,
    artifact_hash: str | None = None,
    ledger: Any = None,
    skill_manager: Any = None,
) -> dict[str, Any]:
    """Record the owner's export choice for one governed change.

    ``share`` marks the current content for the next Learning Packet. ``keep`` copies it to the private
    namespace as ``<skill>-playbook`` (survives Worker Refresh, never exported) and restores the Role
    Release baseline through native rollback. ``discard`` restores the baseline through native rollback.
    """
    match = GOVERNED_ARTIFACT.fullmatch(skill)
    if not match:
        raise LearningDecisionError("skill must be skills/role/<name> or skills/candidates/<name>.")
    if choice not in SHARE_CHOICES:
        raise LearningDecisionError(f"choice must be one of: {', '.join(SHARE_CHOICES)}.")
    if artifact_hash is not None and not re.fullmatch(r"[0-9a-f]{64}", artifact_hash):
        raise LearningDecisionError("artifactHash must be a SHA-256 hash.")
    decided_by = _required(decided_by, "decidedBy")
    manifest = load_worker_manifest(profile_home)
    if not manifest:
        raise LearningConflictError("Export choices require an installed Role Release.")
    ledger = native_module(ledger, "tools.skill_ledger")
    change = next((item for item in governed_changes(profile_home, manifest) if item["artifactPath"] == skill), None)
    expected = artifact_hash or (change or {}).get("artifactHash") or "deleted"
    receipt_id = f"share-{match.group(1)}-{match.group(2)}-{expected[:16]}-{choice}"
    if existing := _read_receipt(profile_home, receipt_id):
        return {**existing, "duplicate": True}
    if change is None:
        raise LearningConflictError(f"{skill} has no change from the Role Release baseline.")
    if artifact_hash is not None and artifact_hash != change["artifactHash"]:
        raise LearningConflictError(f"{skill} changed after the digest was shown; review the new digest.")
    with _claim(profile_home, receipt_id):
        plan = _revert_plan(profile_home, change, ledger)
        receipt: dict[str, Any] = {
            "receiptVersion": "1.0",
            "receiptId": receipt_id,
            "kind": "share",
            "choice": choice,
            "artifactPath": skill,
            "classification": change["classification"],
            "artifactHash": change["artifactHash"],
            "fileHashes": change["fileHashes"],
            "baselineFileHashes": change["baselineFileHashes"],
            "roleReleaseCommit": manifest.get("roleReleaseCommit"),
            "ledgerEntryIds": [str(entry.get("id")) for entry in reversed(plan)],
            "decidedBy": decided_by,
            "decidedAt": utc_now(),
        }
        if choice == "share":
            _update_share_choices(profile_home, skill, {
                key: receipt[key] for key in ("choice", "artifactHash", "roleReleaseCommit", "receiptId", "decidedBy", "decidedAt")
            })
            return _finish(profile_home, receipt)
        if choice == "keep":
            receipt["privatePlaybook"] = _copy_to_private(
                profile_home, change, native_module(skill_manager, "tools.skill_manager_tool"),
            )
        receipt["rolledBackEntryIds"] = _restore_baseline(profile_home, change, plan, ledger)
        _update_share_choices(profile_home, skill, None)
        return _finish(profile_home, receipt)


def private_session(session_id: str) -> bool:
    return str(session_id or "").startswith(PRIVATE_SESSION_PREFIX)


def _protected_target(value: str, hermes_home: Path) -> bool:
    candidate = Path(value.strip()).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    resolved = Path(os.path.normpath(str(candidate)))
    for directory in PROTECTED_STATE_DIRECTORIES:
        root = Path(os.path.normpath(str(hermes_home / directory)))
        if resolved == root or root in resolved.parents:
            return True
    return False


def private_turn_directive(
    *,
    tool_name: str = "",
    args: dict[str, Any] | None = None,
    session_id: str = "",
    hermes_home: Path | None = None,
    **_: Any,
) -> dict[str, str] | None:
    """``pre_tool_call`` veto for durable memory and skill writes in bridge-marked private sessions."""
    if not private_session(session_id):
        return None
    blocked = tool_name in PROTECTED_WRITE_TOOLS
    if tool_name in FILE_WRITE_TOOLS:
        home = hermes_home or Path(os.environ.get("HERMES_HOME", "."))
        arguments = args if isinstance(args, dict) else {}
        targets = [str(arguments.get("path") or ""), *V4A_FILE_HEADER.findall(str(arguments.get("patch") or ""))]
        blocked = any(target.strip() and _protected_target(target, home) for target in targets)
    return {"action": "block", "message": PRIVATE_TURN_MESSAGE} if blocked else None


def register_attachment_privacy(ctx: Any) -> None:
    """Veto durable writes for sessions the bridge marks with ``PRIVATE_SESSION_PREFIX``.

    The bridge sends attachment and persistence-disabled turns to a separate native session whose ID
    starts with ``private:``. ``pre_tool_call`` receives that session ID for foreground tools and for the
    native background review fork, which shares its parent's session ID, so ``memory``, ``skill_manage``
    and file writes into ``skills/`` or ``memories/`` are blocked in both. Hermes 0.22.5 has no per-request
    switch that stops the review fork from running (the API server never sets ``skip_background_review``),
    so the fork may still spend tokens; it just cannot persist anything.
    """
    ctx.register_hook("pre_tool_call", private_turn_directive)
