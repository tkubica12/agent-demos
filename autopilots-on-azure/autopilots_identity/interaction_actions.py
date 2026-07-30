from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def receipt_directory(profile_home: Path) -> Path:
    return profile_home / "interactions" / "receipts"


def cleanup_receipts(profile_home: Path) -> int:
    root = receipt_directory(profile_home)
    if not root.is_dir():
        return 0
    removed = 0
    now = time.time()
    for path in root.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            expires_at = float(payload.get("expiresAtUnix") or 0)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            expires_at = 0
        if expires_at <= now:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def claim_interaction(
    profile_home: Path,
    *,
    interaction_id: str,
    choice_id: str,
    expires_at_unix: float,
) -> dict[str, Any]:
    if (
        len(interaction_id) != 32
        or any(character not in "0123456789abcdef" for character in interaction_id)
    ):
        raise ValueError("Invalid interaction ID.")
    if not choice_id or len(choice_id) > 32:
        raise ValueError("Invalid interaction choice.")
    cleanup_receipts(profile_home)
    path = receipt_directory(profile_home) / f"{interaction_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "1.0",
        "interactionId": interaction_id,
        "choiceId": choice_id,
        "claimedAtUnix": time.time(),
        "expiresAtUnix": max(
            expires_at_unix,
            time.time() + 24 * 60 * 60,
        ),
    }
    try:
        descriptor = os.open(
            path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
    except FileExistsError:
        existing = json.loads(path.read_text(encoding="utf-8"))
        return {
            "claimed": False,
            "interactionId": interaction_id,
            "choiceId": existing.get("choiceId"),
        }
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, indent=2)
        handle.write("\n")
    return {
        "claimed": True,
        "interactionId": interaction_id,
        "choiceId": choice_id,
    }

