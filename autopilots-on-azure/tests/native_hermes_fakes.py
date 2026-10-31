"""Small in-process stand-ins for the Hermes 0.22.5 native learning modules.

They follow the verified contracts of ``tools.skill_ledger`` (newest-first entries, content-addressed
blobs, fail-closed ``rollback_entry`` with a ``pre-rollback`` safety entry), ``tools.write_approval``
and ``tools.skill_manager_tool.apply_skill_pending``. Tests inject them as function parameters.
"""

from __future__ import annotations

import base64
import difflib
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class FakeSkillLedger:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.entries: list[dict[str, Any]] = []
        self.blobs: dict[str, bytes] = {}
        self.actor: str | None = None

    def _store(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        self.blobs[digest] = data
        return digest

    def _snapshot(self, root: Path) -> list[dict[str, str]]:
        if not root.exists():
            return []
        return [
            {"path": str(path), "sha256": self._store(path.read_bytes())}
            for path in sorted(root.rglob("*")) if path.is_file()
        ]

    def _append(self, action: str, skill: str, before: list, after: list, evidence: dict | None = None) -> str:
        if action != "pre-rollback":
            before_map = {item["path"]: item["sha256"] for item in before}
            after_map = {item["path"]: item["sha256"] for item in after}
            same = {path for path, sha in before_map.items() if after_map.get(path) == sha}
            before = [item for item in before if item["path"] not in same]
            after = [item for item in after if item["path"] not in same]
        entry = {
            "id": uuid.uuid4().hex[:12],
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": self.actor or "agent",
            "action": action,
            "skill": skill,
            "evidence": evidence or {},
            "before": before,
            "after": after,
        }
        self.entries.append(entry)
        return entry["id"]

    def record(self, action: str, skill: str, root: Path, mutate: Callable[[], None], *, actor: str = "agent") -> str:
        before = self._snapshot(root)
        mutate()
        previous, self.actor = self.actor, self.actor or actor
        try:
            return self._append(action, skill, before, self._snapshot(root), {"session_id": "native-session"})
        finally:
            self.actor = previous

    def list_entries(self, skill: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        rows = [entry for entry in reversed(self.entries) if not skill or entry["skill"] == skill]
        return rows[:limit] if limit is not None else rows

    def read_blob(self, sha256: str) -> bytes | None:
        return self.blobs.get(sha256)

    def set_ledger_actor(self, actor: str) -> str | None:
        previous, self.actor = self.actor, actor
        return previous

    def reset_ledger_actor(self, token: str | None) -> None:
        self.actor = token

    def rollback_entry(self, entry_id: str) -> tuple[bool, str]:
        entry = next((item for item in self.entries if item["id"] == entry_id), None)
        if entry is None:
            return False, f"no ledger entry with id '{entry_id}'"
        if any(self.read_blob(item["sha256"]) is None for item in entry["before"]):
            return False, "missing blob; rollback aborted, nothing was changed"
        touched = {item["path"] for item in entry["before"] + entry["after"]}
        safety = [
            {"path": path, "sha256": self._store(Path(path).read_bytes())}
            for path in sorted(touched) if Path(path).is_file()
        ]
        self._append("pre-rollback", entry["skill"], safety, safety, {"rollback_target": entry_id})
        before_paths = {item["path"] for item in entry["before"]}
        for item in entry["before"]:
            Path(item["path"]).parent.mkdir(parents=True, exist_ok=True)
            Path(item["path"]).write_bytes(self.blobs[item["sha256"]])
        for item in entry["after"]:
            if item["path"] not in before_paths and Path(item["path"]).is_file():
                Path(item["path"]).unlink()
        self._append("rollback", entry["skill"], safety, entry["before"], {"rollback_target": entry_id})
        return True, f"rolled back entry {entry_id}"


class FakeSkillManager:
    def __init__(self, home: Path, ledger: FakeSkillLedger) -> None:
        self.home = home
        self.ledger = ledger
        self.applied: list[dict[str, Any]] = []

    def _find(self, name: str) -> Path | None:
        return next((path.parent for path in sorted((self.home / "skills").rglob("SKILL.md"))
                     if path.parent.name == name), None)

    def apply_skill_pending(self, payload: dict[str, Any]) -> str:
        self.applied.append(payload)
        action, name = payload["action"], payload["name"]
        if action == "create":
            root = self.home / "skills" / (payload.get("category") or "") / name
            if root.exists():
                return json.dumps({"success": False, "error": f"Skill '{name}' already exists."})
            mutate = lambda: (root.mkdir(parents=True), (root / "SKILL.md").write_text(payload["content"], encoding="utf-8"))
        else:
            root = self._find(name)
            if root is None:
                return json.dumps({"success": False, "error": f"Skill '{name}' not found."})
            if action == "patch" and payload.get("content"):
                mutate = lambda: (root / "SKILL.md").write_text(payload["content"], encoding="utf-8")
            elif action == "patch":
                text = (root / "SKILL.md").read_text(encoding="utf-8")
                if payload["old_string"] not in text:
                    return json.dumps({"success": False, "error": "Could not find a match for old_string"})
                mutate = lambda: (root / "SKILL.md").write_text(
                    text.replace(payload["old_string"], payload["new_string"], 1), encoding="utf-8",
                )
            elif action == "write_file":
                target = root / payload["file_path"]
                mutate = lambda: (target.parent.mkdir(parents=True, exist_ok=True),
                                  target.write_text(payload["file_content"], encoding="utf-8"))
            else:
                return json.dumps({"success": False, "error": f"Unsupported action {action}"})
        self.ledger.record(action, name, root, mutate)
        return json.dumps({"success": True, "message": f"{action} {name}"})


class FakeWriteApproval:
    def __init__(self, *, strict: bool = True) -> None:
        self.strict = strict
        self.records: dict[str, dict[str, Any]] = {}

    def stage(self, payload: dict[str, Any], *, summary: str, origin: str = "background_review") -> str:
        pending_id = uuid.uuid4().hex[:8]
        self.records[pending_id] = {
            "id": pending_id, "subsystem": "skills", "action": payload["action"], "summary": summary,
            "origin": origin, "created_at": datetime.now(timezone.utc).timestamp(), "payload": payload,
        }
        return pending_id

    def write_approval_enabled(self, subsystem: str) -> bool:
        return self.strict and subsystem == "skills"

    def list_pending(self, subsystem: str) -> list[dict[str, Any]]:
        return sorted(self.records.values(), key=lambda record: record["created_at"]) if subsystem == "skills" else []

    def get_pending(self, subsystem: str, pending_id: str) -> dict[str, Any] | None:
        return self.records.get(pending_id) if subsystem == "skills" else None

    def discard_pending(self, subsystem: str, pending_id: str) -> bool:
        return self.records.pop(pending_id, None) is not None

    def skill_pending_diff(self, record: dict[str, Any]) -> str:
        payload = record["payload"]
        if payload["action"] == "create":
            return payload["content"]
        return "".join(difflib.unified_diff(
            [payload.get("old_string", "") + "\n"], [payload.get("new_string", "") + "\n"], "a/SKILL.md", "b/SKILL.md",
        ))


def skill_text(name: str, body: str) -> str:
    return f"---\nname: {name}\ndescription: {body.splitlines()[0]}\n---\n\n# {name}\n\n{body}\n"


def write_profile(profile: Path, role_skills: dict[str, str]) -> dict[str, Any]:
    """A Role Release-shaped profile with its Worker manifest and Role Skill baseline."""
    from blueprint import role_skill_hashes

    for name, body in role_skills.items():
        path = profile / "skills" / "role" / name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(skill_text(name, body), encoding="utf-8")
    manifest = {
        "roleBlueprint": "junior-project-manager",
        "roleBlueprintSource": "https://example.com/roles.git",
        "roleBlueprintPath": "roles/junior-project-manager",
        "roleRelease": "3.0.0",
        "roleReleaseCommit": "a" * 40,
        "workerId": "worker-1",
        "assignmentScope": "team-alpha",
        "profileName": "junior-project-manager",
        "distributionOwned": ["skills/role"],
        "roleSkillBaseline": role_skill_hashes(profile),
        "refreshedAt": "2026-10-30T08:00:00Z",
    }
    worker = profile / "local" / "worker.json"
    worker.parent.mkdir(parents=True, exist_ok=True)
    worker.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


class ApprovalKey:
    """Operator Ed25519 key that signs Learning Packet and reject-and-refresh receipts."""

    def __init__(self, seed: bytes = b"\x07" * 32) -> None:
        self.private_key = Ed25519PrivateKey.from_private_bytes(seed)
        self.public_key = base64.b64encode(self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw,
        )).decode("ascii")

    def sign(self, receipt: dict[str, Any]) -> dict[str, Any]:
        unsigned = {key: value for key, value in receipt.items() if key != "signature"}
        return {**unsigned, "signature": base64.b64encode(self.private_key.sign(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        )).decode("ascii")}
