from __future__ import annotations

import hashlib
import os
from typing import Any

from microsoft_agents.activity import Activity
from microsoft_agents.hosting.core import TurnContext
from microsoft_agents.hosting.core.app.proactive import Conversation


SUPPORTED_BOUNDARIES = {"one_to_one", "shared_group", "public_channel"}


def delivery_reference_key(
    *,
    worker_id: str,
    conversation_id: str,
    boundary: str,
) -> str:
    digest = hashlib.sha256(
        f"{worker_id}\n{conversation_id}\n{boundary}".encode("utf-8")
    ).hexdigest()
    return f"teams-{digest[:32]}"


def delivery_reference_metadata(
    context: TurnContext,
    *,
    worker_id: str,
    boundary: str,
) -> dict[str, Any] | None:
    if boundary not in SUPPORTED_BOUNDARIES:
        return None
    conversation = Conversation.from_turn_context(context)
    conversation.validate()
    conversation_id = str(context.activity.conversation.id)
    return {
        "referenceKey": delivery_reference_key(
            worker_id=worker_id,
            conversation_id=conversation_id,
            boundary=boundary,
        ),
        "boundary": boundary,
        "conversation": conversation.store_item_to_json(),
    }


async def send_proactive_activity(
    adapter: Any,
    stored: dict[str, Any],
    text: str,
) -> dict[str, str]:
    if not text.strip():
        return
    boundary = str(stored.get("boundary") or "")
    if boundary not in SUPPORTED_BOUNDARIES:
        raise RuntimeError(f"Unsupported proactive delivery boundary: {boundary!r}.")
    conversation_payload = stored.get("conversation")
    if not isinstance(conversation_payload, dict):
        raise RuntimeError("Stored proactive conversation is invalid.")
    conversation = Conversation.from_json_to_store_item(conversation_payload)
    conversation.validate()
    continuation = conversation.conversation_reference.get_continuation_activity()

    activity_id = ""

    async def callback(turn_context: TurnContext) -> None:
        nonlocal activity_id
        response = await turn_context.send_activity(Activity(type="message", text=text))
        raw_activity_id = (
            response.get("id")
            if isinstance(response, dict)
            else getattr(response, "id", "")
        )
        activity_id = (
            str(raw_activity_id).strip()
            if raw_activity_id not in (None, "")
            else ""
        )

    if conversation.claims:
        await adapter.continue_conversation_with_claims(
            Conversation.identity_from_claims(conversation.claims),
            continuation,
            callback,
        )
    else:
        app_id = (
            os.getenv("AGENT365_BLUEPRINT_CLIENT_ID", "").strip()
            or os.getenv(
                "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID",
                "",
            ).strip()
        )
        if not app_id:
            raise RuntimeError(
                "Agent 365 application ID is required for proactive delivery."
            )
        await adapter.continue_conversation(
            app_id,
            continuation,
            callback,
        )
    if not activity_id:
        raise RuntimeError("Teams proactive send returned no activity ID.")
    return {"activityId": activity_id}
