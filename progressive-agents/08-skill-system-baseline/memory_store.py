from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_MEMORY_PATH = ".data/memory_store.json"
SENSITIVE_PATTERNS = [
    re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),
    re.compile(r"\b(?:\+?\d[\d .-]{7,}\d)\b"),
    re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def memory_path() -> Path:
    return Path(os.getenv("MEMORY_STORE_PATH", DEFAULT_MEMORY_PATH))


def redact_critical_pii(text: str) -> str:
    redacted = text
    for pattern in SENSITIVE_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def _user_template() -> dict[str, Any]:
    return {
        "profile": {},
        "memories": [],
        "conversations": {},
        "audit": [],
    }


def _tokenize(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]{3,}", value.lower())}


def _deep_merge(target: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    for key, value in patch.items():
        if value is None:
            target.pop(key, None)
        elif isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = deepcopy(value)
    return target


def _delete_path(target: dict[str, Any], path: str) -> bool:
    parts = [part for part in path.split(".") if part]
    if not parts:
        return False
    current: Any = target
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    if not isinstance(current, dict) or parts[-1] not in current:
        return False
    del current[parts[-1]]
    return True


@dataclass(frozen=True)
class MemoryContext:
    profile: dict[str, Any]
    static_memories: list[dict[str, Any]]
    contextual_memories: list[dict[str, Any]]

    def to_prompt(self) -> str:
        parts = ["Persistent user context:"]
        if self.profile:
            parts.append(f"Profile JSON: {json.dumps(self.profile, ensure_ascii=False, sort_keys=True)}")
        if self.static_memories:
            parts.append("Static memories:")
            parts.extend(f"- {item['content']}" for item in self.static_memories)
        if self.contextual_memories:
            parts.append("Relevant prior conversation memories:")
            parts.extend(f"- {item['content']}" for item in self.contextual_memories)
        if len(parts) == 1:
            parts.append("No stored memories yet.")
        parts.append("Use this context quietly. Do not reveal raw memory JSON unless asked.")
        return "\n".join(parts)


class FileMemoryStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or memory_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "users": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            delete=False,
        ) as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False, sort_keys=True)
            temp_name = handle.name
        Path(temp_name).replace(self.path)

    def _user(self, data: dict[str, Any], user_id: str) -> dict[str, Any]:
        users = data.setdefault("users", {})
        return users.setdefault(user_id, _user_template())

    def audit(self, user_id: str, action: str, details: dict[str, Any]) -> dict[str, Any]:
        data = self._load()
        user = self._user(data, user_id)
        entry = {
            "id": str(uuid.uuid4()),
            "action": action,
            "details": details,
            "createdAt": utc_now(),
        }
        user["audit"].append(entry)
        self._save(data)
        return entry

    def get_profile(self, user_id: str) -> dict[str, Any]:
        data = self._load()
        return deepcopy(self._user(data, user_id)["profile"])

    def propose_profile_patch(
        self,
        user_id: str,
        patch: dict[str, Any],
        source: str,
    ) -> dict[str, Any]:
        before = self.get_profile(user_id)
        after = _deep_merge(deepcopy(before), patch)
        proposal = {
            "id": str(uuid.uuid4()),
            "patch": patch,
            "before": before,
            "after": after,
            "source": source,
            "createdAt": utc_now(),
        }
        self.audit(user_id, "profile.patch_proposed", {"proposal": proposal})
        return proposal

    def apply_profile_patch(
        self,
        user_id: str,
        patch: dict[str, Any],
        source: str,
    ) -> dict[str, Any]:
        data = self._load()
        user = self._user(data, user_id)
        before = deepcopy(user["profile"])
        user["profile"] = _deep_merge(user["profile"], patch)
        after = deepcopy(user["profile"])
        user["audit"].append(
            {
                "id": str(uuid.uuid4()),
                "action": "profile.patch_applied",
                "details": {"patch": patch, "before": before, "after": after, "source": source},
                "createdAt": utc_now(),
            }
        )
        self._save(data)
        return after

    def delete_profile_item(self, user_id: str, path: str, source: str) -> bool:
        data = self._load()
        user = self._user(data, user_id)
        before = deepcopy(user["profile"])
        deleted = _delete_path(user["profile"], path)
        user["audit"].append(
            {
                "id": str(uuid.uuid4()),
                "action": "profile.item_deleted",
                "details": {"path": path, "deleted": deleted, "before": before, "source": source},
                "createdAt": utc_now(),
            }
        )
        self._save(data)
        return deleted

    def create_memory(
        self,
        user_id: str,
        content: str,
        kind: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = self._load()
        user = self._user(data, user_id)
        item = {
            "id": str(uuid.uuid4()),
            "kind": kind,
            "content": content,
            "metadata": metadata or {},
            "createdAt": utc_now(),
            "updatedAt": utc_now(),
        }
        user["memories"].append(item)
        user["audit"].append(
            {
                "id": str(uuid.uuid4()),
                "action": "memory.created",
                "details": {"memoryId": item["id"], "kind": kind},
                "createdAt": utc_now(),
            }
        )
        self._save(data)
        return item

    def list_memories(self, user_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        data = self._load()
        items = deepcopy(self._user(data, user_id)["memories"])
        if kind:
            return [item for item in items if item.get("kind") == kind]
        return items

    def forget_memory(self, user_id: str, query: str) -> list[dict[str, Any]]:
        data = self._load()
        user = self._user(data, user_id)
        query_tokens = _tokenize(query)
        removed: list[dict[str, Any]] = []
        kept: list[dict[str, Any]] = []
        for item in user["memories"]:
            content_tokens = _tokenize(item.get("content", ""))
            if query.lower() in item.get("content", "").lower() or query_tokens & content_tokens:
                removed.append(item)
            else:
                kept.append(item)
        user["memories"] = kept
        user["audit"].append(
            {
                "id": str(uuid.uuid4()),
                "action": "memory.forgotten",
                "details": {"query": query, "removedIds": [item["id"] for item in removed]},
                "createdAt": utc_now(),
            }
        )
        self._save(data)
        return removed

    def search_memories(
        self,
        user_id: str,
        query: str | None = None,
        kinds: set[str] | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        items = self.list_memories(user_id)
        if kinds:
            items = [item for item in items if item.get("kind") in kinds]
        if not query:
            return items[:limit]
        query_tokens = _tokenize(query)
        scored = []
        for item in items:
            score = len(query_tokens & _tokenize(item.get("content", "")))
            if score:
                scored.append((score, item))
        scored.sort(key=lambda pair: (-pair[0], pair[1].get("updatedAt", "")))
        return [item for _, item in scored[:limit]]

    def get_memory_context(self, user_id: str, latest_message: str) -> MemoryContext:
        return MemoryContext(
            profile=self.get_profile(user_id),
            static_memories=self.search_memories(user_id, kinds={"user_profile"}, limit=5),
            contextual_memories=self.search_memories(
                user_id,
                latest_message,
                kinds={"chat_summary", "user_profile", "direct"},
                limit=5,
            ),
        )

    def add_message(
        self,
        user_id: str,
        conversation_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = self._load()
        user = self._user(data, user_id)
        conversations = user["conversations"]
        now = utc_now()
        conversation = conversations.setdefault(
            conversation_id,
            {
                "id": conversation_id,
                "title": "",
                "messages": [],
                "createdAt": now,
                "updatedAt": now,
                "summaryMemoryId": None,
            },
        )
        message = {
            "id": str(uuid.uuid4()),
            "role": role,
            "content": content,
            "metadata": metadata or {},
            "createdAt": now,
        }
        conversation["messages"].append(message)
        conversation["updatedAt"] = now
        if not conversation.get("title") and role == "user":
            conversation["title"] = content.strip()[:80]
        self._save(data)
        return message

    def list_conversations(self, user_id: str) -> list[dict[str, Any]]:
        data = self._load()
        conversations = deepcopy(list(self._user(data, user_id)["conversations"].values()))
        conversations.sort(key=lambda item: item.get("updatedAt", ""), reverse=True)
        return [
            {
                "id": item["id"],
                "title": item.get("title") or item["id"],
                "createdAt": item.get("createdAt"),
                "updatedAt": item.get("updatedAt"),
                "messageCount": len(item.get("messages", [])),
                "summaryMemoryId": item.get("summaryMemoryId"),
            }
            for item in conversations
        ]

    def get_conversation(self, user_id: str, conversation_id: str) -> dict[str, Any] | None:
        data = self._load()
        conversation = self._user(data, user_id)["conversations"].get(conversation_id)
        return deepcopy(conversation) if conversation else None

    def summarize_conversation(self, user_id: str, conversation_id: str) -> dict[str, Any]:
        data = self._load()
        user = self._user(data, user_id)
        conversation = user["conversations"].get(conversation_id)
        if not conversation:
            raise KeyError(f"Conversation not found: {conversation_id}")
        messages = conversation.get("messages", [])
        user_messages = [message["content"] for message in messages if message.get("role") == "user"]
        assistant_messages = [
            message["content"] for message in messages if message.get("role") == "assistant"
        ]
        summary_parts = []
        if user_messages:
            summary_parts.append(f"User discussed: {user_messages[0][:240]}")
        if len(user_messages) > 1:
            summary_parts.append(f"Later user context: {user_messages[-1][:240]}")
        if assistant_messages:
            summary_parts.append(f"Assistant outcome: {assistant_messages[-1][:240]}")
        if not summary_parts:
            summary_parts.append("Conversation had no summarizable messages.")
        summary = redact_critical_pii(" ".join(summary_parts))
        memory = {
            "id": str(uuid.uuid4()),
            "kind": "chat_summary",
            "content": summary,
            "metadata": {"conversationId": conversation_id},
            "createdAt": utc_now(),
            "updatedAt": utc_now(),
        }
        user["memories"].append(memory)
        conversation["summaryMemoryId"] = memory["id"]
        conversation["updatedAt"] = utc_now()
        user["audit"].append(
            {
                "id": str(uuid.uuid4()),
                "action": "conversation.summary_created",
                "details": {
                    "conversationId": conversation_id,
                    "memoryId": memory["id"],
                    "redacted": summary != " ".join(summary_parts),
                },
                "createdAt": utc_now(),
            }
        )
        self._save(data)
        return {"summary": summary, "memory": memory}

    def list_audit(self, user_id: str) -> list[dict[str, Any]]:
        data = self._load()
        return deepcopy(self._user(data, user_id)["audit"])


store = FileMemoryStore()
