"""Owner-only Teams digest of what the Worker learned, with typed, expiring, owner-bound card actions."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from microsoft_agents.activity import Activity, Attachment

from bridge.interactions import CARD_CONTENT_TYPE


LEARNING_ACTION_TOKEN = "learningActionToken"
LEARNING_ACTION = "learningAction"
LEARNING_ACTIONS = frozenset({"undo", "approve", "reject", "share", "keep", "discard"})
LEARNING_COMMANDS = frozenset({"/learning", "/learned"})
ACTION_TTL_SECONDS = 3 * 24 * 60 * 60
MAX_CHANGES = 5
MAX_PENDING = 3
MAX_GOVERNED = 3
DIFF_LINE_BUDGETS = (12, 6, 0)
MAX_DIFF_LINE_CHARACTERS = 160
MAX_CARD_BYTES = 27_000
ORIGIN_LABELS = {
    "foreground": "during a conversation",
    "background": "background review or Curator",
    "owner": "your decision",
    "background_review": "background review",
}
NAMESPACE_LABELS = {
    "private": "Private Playbook",
    "role": "Role Skill",
    "candidates": "Candidate Improvement",
    "local": "Local skill",
}


def worker_owner_id() -> str:
    """Entra object ID of the Worker owner; the only person who may review or change what it learned."""
    return os.getenv("WORKER_OWNER_ID", "").strip().lower()


def is_worker_owner(user_id: str) -> bool:
    owner = worker_owner_id()
    return bool(owner) and user_id.strip().lower() == owner


def learning_command(message: str) -> bool:
    return message.strip().lower() in LEARNING_COMMANDS


def _binding(user_id: str, conversation_id: str) -> str:
    return hashlib.sha256(f"{user_id.strip().lower()}\n{conversation_id}".encode("utf-8")).hexdigest()[:32]


def _cipher(secret: str | None = None) -> Fernet:
    value = secret or os.getenv("API_SERVER_KEY", "")
    if not value:
        raise RuntimeError("Learning action tokens require API_SERVER_KEY.")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(f"learning:{value}".encode("utf-8")).digest()))


def create_learning_action_token(
    *,
    action: str,
    target: str,
    user_id: str,
    conversation_id: str,
    artifact_hash: str = "",
    expires_at_unix: float | None = None,
    secret: str | None = None,
) -> str:
    """Compact encrypted action; Teams cards carry many of them within the card size limit."""
    if action not in LEARNING_ACTIONS:
        raise ValueError(f"Unsupported learning action {action!r}.")
    payload = {
        "v": 1,
        "a": action,
        "t": target,
        "h": artifact_hash,
        "b": _binding(user_id, conversation_id),
        "w": os.getenv("WORKER_ID", ""),
        "e": int(expires_at_unix if expires_at_unix is not None else time.time() + ACTION_TTL_SECONDS),
    }
    return _cipher(secret).encrypt(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


def decode_learning_action_token(
    token: str,
    *,
    user_id: str,
    conversation_id: str,
    secret: str | None = None,
) -> dict[str, Any]:
    try:
        payload = json.loads(_cipher(secret).decrypt(token.encode("ascii")))
    except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("The learning action token is invalid.") from exc
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise ValueError("The learning action token is invalid.")
    if float(payload.get("e") or 0) <= time.time():
        raise ValueError("This learning card has expired. Send /learning for a fresh digest.")
    if not is_worker_owner(user_id):
        raise ValueError("Only the Worker owner can change what the Worker learned.")
    if payload.get("b") != _binding(user_id, conversation_id):
        raise ValueError("The learning action belongs to another conversation.")
    if payload.get("w") not in {"", os.getenv("WORKER_ID", "")}:
        raise ValueError("The learning action belongs to another Worker.")
    if payload.get("a") not in LEARNING_ACTIONS or not str(payload.get("t") or ""):
        raise ValueError("The learning action is invalid.")
    return {
        "action": payload["a"],
        "target": str(payload["t"]),
        "artifactHash": str(payload.get("h") or ""),
        "expiresAtUnix": payload["e"],
    }


def learning_action_data(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    action = value.get("action")
    if isinstance(action, dict) and isinstance(action.get("data"), dict):
        value = action["data"]
    token = value.get(LEARNING_ACTION_TOKEN)
    name = value.get(LEARNING_ACTION)
    if not isinstance(token, str) or not isinstance(name, str):
        return None
    return {"token": token, "action": name}


def _action(
    title: str, action: str, target: str, *, user_id: str, conversation_id: str,
    artifact_hash: str = "", style: str | None = None,
) -> dict[str, Any]:
    data = {
        LEARNING_ACTION_TOKEN: create_learning_action_token(
            action=action, target=target, artifact_hash=artifact_hash,
            user_id=user_id, conversation_id=conversation_id,
        ),
        LEARNING_ACTION: action,
    }
    result: dict[str, Any] = {
        "type": "Action.Execute",
        "title": title,
        "verb": "learningAction",
        "data": data,
        "fallback": {"type": "Action.Submit", "title": title, "data": data},
    }
    if style:
        result["style"] = style
        result["fallback"]["style"] = style
    return result


def _text(text: str, **properties: Any) -> dict[str, Any]:
    return {"type": "TextBlock", "text": text, "wrap": True, **properties}


def _diff_blocks(diff: str, truncated: bool, max_lines: int) -> list[dict[str, Any]]:
    lines = diff.splitlines()
    if lines and not max_lines:
        return [_text("Diff omitted to fit the Teams card. Send /learning for fewer items.", isSubtle=True, size="Small")]
    blocks = []
    for line in lines[:max_lines]:
        color = "Good" if line.startswith("+") and not line.startswith("+++") else (
            "Attention" if line.startswith("-") and not line.startswith("---") else "Default"
        )
        blocks.append({
            "type": "RichTextBlock",
            "spacing": "None",
            "inlines": [{
                "type": "TextRun", "text": line[:MAX_DIFF_LINE_CHARACTERS] or " ",
                "fontType": "Monospace", "size": "Small", "color": color,
            }],
        })
    if truncated or len(lines) > max_lines:
        blocks.append(_text("Diff shortened.", isSubtle=True, size="Small", spacing="None"))
    return blocks


def _skill_label(item: dict[str, Any]) -> str:
    namespace = NAMESPACE_LABELS.get(str(item.get("namespace") or ""), "Skill")
    return f"**{item.get('skill') or item.get('artifactPath') or 'skill'}** · {namespace}"


def _change_container(item: dict[str, Any], *, user_id: str, conversation_id: str, diff_lines: int) -> dict[str, Any]:
    origin = ORIGIN_LABELS.get(str(item.get("origin") or ""), str(item.get("origin") or "unknown"))
    items: list[dict[str, Any]] = [
        _text(_skill_label(item)),
        _text(f"{item.get('action')} · {origin} · {item.get('ts')}", isSubtle=True, size="Small", spacing="None"),
        *_diff_blocks(str(item.get("diff") or ""), bool(item.get("diffTruncated")), diff_lines),
    ]
    if item.get("undoable"):
        items.append({"type": "ActionSet", "actions": [_action(
            "Undo", "undo", str(item["entryId"]), user_id=user_id, conversation_id=conversation_id,
        )]})
    else:
        items.append(_text(f"Undo unavailable: {item.get('undoBlockedReason')}", isSubtle=True, size="Small"))
    return {"type": "Container", "separator": True, "items": items}


def _pending_container(item: dict[str, Any], *, user_id: str, conversation_id: str, diff_lines: int) -> dict[str, Any]:
    origin = ORIGIN_LABELS.get(str(item.get("origin") or ""), str(item.get("origin") or "unknown"))
    return {"type": "Container", "separator": True, "items": [
        _text(_skill_label(item)),
        _text(f"{item.get('summary')} · {origin}", isSubtle=True, size="Small", spacing="None"),
        *_diff_blocks(str(item.get("diff") or ""), bool(item.get("diffTruncated")), diff_lines),
        {"type": "ActionSet", "actions": [
            _action("Approve", "approve", str(item["pendingId"]), user_id=user_id, conversation_id=conversation_id,
                    style="positive"),
            _action("Reject", "reject", str(item["pendingId"]), user_id=user_id, conversation_id=conversation_id,
                    style="destructive"),
        ]},
    ]}


def _governed_container(item: dict[str, Any], *, user_id: str, conversation_id: str, diff_lines: int) -> dict[str, Any]:
    choice = item.get("choice") or {}
    if choice.get("choice") == "share" and choice.get("current"):
        state = "Marked for sharing in the next Learning Packet."
    elif choice.get("choice") == "share":
        state = "Changed after you chose Share; choose again."
    else:
        state = "Not shared. Nothing leaves this Worker without your choice."
    items: list[dict[str, Any]] = [
        _text(_skill_label(item)),
        _text(state, isSubtle=True, size="Small", spacing="None"),
    ]
    if not item.get("traceComplete"):
        items.append(_text(f"Native trace incomplete: {item.get('traceReason')}", color="Warning", size="Small"))
    target = str(item["artifactPath"])
    artifact_hash = str(item.get("artifactHash") or "")
    actions = [
        _action(label, choice_name, target, user_id=user_id, conversation_id=conversation_id,
                artifact_hash=artifact_hash, style=style)
        for label, choice_name, style in (
            ("Share", "share", "positive"),
            ("Keep only for me", "keep", None),
            ("Discard", "discard", "destructive"),
        )
        if item.get("traceComplete") or choice_name != "share"
    ]
    items.append({"type": "ActionSet", "actions": actions})
    return {"type": "Container", "separator": True, "items": items}


def digest_has_news(digest: dict[str, Any]) -> bool:
    return int(digest.get("newCount") or 0) > 0


def learning_digest_card(
    digest: dict[str, Any], *, user_id: str, conversation_id: str, only_new: bool = False,
) -> dict[str, Any]:
    """Teams card within the Adaptive Card size limit; diffs shrink before any decision is dropped."""
    for diff_lines in DIFF_LINE_BUDGETS:
        card = _digest_card(
            digest, user_id=user_id, conversation_id=conversation_id, only_new=only_new, diff_lines=diff_lines,
        )
        if len(json.dumps(card).encode("utf-8")) <= MAX_CARD_BYTES:
            return card
    raise ValueError("The learning digest does not fit in one Teams card.")


def _digest_card(
    digest: dict[str, Any], *, user_id: str, conversation_id: str, only_new: bool, diff_lines: int,
) -> dict[str, Any]:
    def selected(name: str, limit: int) -> tuple[list[dict[str, Any]], int]:
        items = [item for item in digest.get(name) or [] if not only_new or item.get("new")]
        return items[:limit], max(0, len(items) - limit)

    changes, more_changes = selected("changes", MAX_CHANGES)
    pending, more_pending = selected("pending", MAX_PENDING)
    governed, more_governed = selected("governed", MAX_GOVERNED)
    mode = "Strict approval is on: skill changes wait for you." if digest.get("strictMode") else (
        "Changes apply immediately; you can undo each one."
    )
    body: list[dict[str, Any]] = [
        _text("What I learned", weight="Bolder", size="Medium"),
        _text(mode, isSubtle=True),
    ]
    sections = (
        ("Skill changes", changes, more_changes, _change_container),
        ("Waiting for your approval", pending, more_pending, _pending_container),
        ("Share with the role?", governed, more_governed, _governed_container),
    )
    for title, items, more, render in sections:
        if not items:
            continue
        body.append(_text(title, weight="Bolder", separator=True))
        body.extend(
            render(item, user_id=user_id, conversation_id=conversation_id, diff_lines=diff_lines) for item in items
        )
        if more:
            body.append(_text(f"{more} more. Send /learning after deciding these.", isSubtle=True, size="Small"))
    if not (changes or pending or governed):
        body.append(_text("Nothing new since the last digest."))
    body.append(_text(
        "Memory updates stay autonomous. Private Playbooks never leave this Worker.", isSubtle=True, size="Small",
    ))
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
    }


def learning_digest_activity(
    digest: dict[str, Any], *, user_id: str, conversation_id: str, only_new: bool = False, text: str = "",
) -> Activity:
    return Activity(
        type="message",
        text=text or "What I learned",
        attachments=[Attachment(
            content_type=CARD_CONTENT_TYPE,
            content=learning_digest_card(
                digest, user_id=user_id, conversation_id=conversation_id, only_new=only_new,
            ),
        )],
    )


def learning_action_accepted_card(message: str) -> dict[str, Any]:
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": [
            _text("Learning decision received", weight="Bolder", size="Medium"),
            _text(message, color="Good"),
            _text("A refreshed digest follows in this chat.", isSubtle=True),
        ],
    }


def learning_action_label(token: dict[str, Any]) -> str:
    target = str(token.get("target") or "")
    skill = target.rsplit("/", 1)[-1]
    return {
        "undo": f"Undo change {target}",
        "approve": f"Approve staged change {target}",
        "reject": f"Reject staged change {target}",
        "share": f"Share {skill} with the role",
        "keep": f"Keep {skill} only for you",
        "discard": f"Discard the change to {skill}",
    }[str(token["action"])]


def learning_result_message(token: dict[str, Any], receipt: dict[str, Any]) -> str:
    skill = str(receipt.get("artifactPath") or receipt.get("skill") or token.get("target") or "the skill")
    action = str(token["action"])
    message = {
        "undo": f"Undone: {receipt.get('action')} of {skill}.",
        "approve": f"Approved and applied the staged change to {skill}.",
        "reject": f"Rejected the staged change to {skill}.",
        "share": f"{skill} is marked for the next Learning Packet. Nothing is sent until an operator approves it.",
        "keep": (
            f"Kept your version as Private Playbook {receipt.get('privatePlaybook')}; "
            f"{skill} is back to the Role Release baseline."
        ),
        "discard": f"Discarded the change; {skill} is back to the Role Release baseline.",
    }[action]
    return f"Already done earlier. {message}" if receipt.get("duplicate") else message
