from __future__ import annotations

import json
import tempfile
from pathlib import Path

from memory_store import FileMemoryStore


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        store = FileMemoryStore(Path(temp_dir) / "memory.json")
        user_id = "smoke-user"
        conversation_id = "smoke-thread"

        proposal = store.propose_profile_patch(
            user_id,
            {"preferences": {"style": "concise"}},
            "smoke",
        )
        if proposal["after"]["preferences"]["style"] != "concise":
            raise RuntimeError("Profile patch proposal did not produce expected profile.")

        profile = store.apply_profile_patch(
            user_id,
            {"preferences": {"style": "concise", "theme": "dark"}},
            "smoke",
        )
        if profile["preferences"]["theme"] != "dark":
            raise RuntimeError("Profile patch was not applied.")

        deleted = store.delete_profile_item(user_id, "preferences.theme", "smoke")
        if not deleted or "theme" in store.get_profile(user_id)["preferences"]:
            raise RuntimeError("Profile delete did not remove nested item.")

        store.add_message(
            user_id,
            conversation_id,
            "user",
            "Remember my project code name is greenfield. Email me at user@example.com",
        )
        store.add_message(
            user_id,
            conversation_id,
            "assistant",
            "I will use greenfield as the project code name.",
        )
        summary = store.summarize_conversation(user_id, conversation_id)
        if "user@example.com" in summary["summary"] or "[REDACTED]" not in summary["summary"]:
            raise RuntimeError("Summary did not redact critical PII.")

        search = store.search_memories(user_id, "greenfield")
        if not search:
            raise RuntimeError("Summary memory was not found by contextual search.")

        conversations = store.list_conversations(user_id)
        if conversations[0]["id"] != conversation_id or conversations[0]["messageCount"] != 2:
            raise RuntimeError("Conversation listing is wrong.")

        remembered = store.create_memory(user_id, "User likes direct answers.", "direct")
        removed = store.forget_memory(user_id, "direct answers")
        if remembered["id"] not in {item["id"] for item in removed}:
            raise RuntimeError("Forget did not remove remembered item.")

        audit = store.list_audit(user_id)
        required_actions = {
            "profile.patch_proposed",
            "profile.patch_applied",
            "profile.item_deleted",
            "conversation.summary_created",
            "memory.created",
            "memory.forgotten",
        }
        actions = {entry["action"] for entry in audit}
        if not required_actions.issubset(actions):
            raise RuntimeError(f"Missing audit actions: {sorted(required_actions - actions)}")

        print(
            json.dumps(
                {
                    "profile": store.get_profile(user_id),
                    "conversationCount": len(conversations),
                    "memorySearchHits": len(search),
                    "auditActions": sorted(actions),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
