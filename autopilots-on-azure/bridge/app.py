from __future__ import annotations

import asyncio
import html
import os
import secrets
import re
import time
import uuid
import unicodedata
from collections import deque
from functools import cache
from typing import Any

from aiohttp import ClientResponseError
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from microsoft_agents.activity import Activity, load_configuration_from_env
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.core import AgentApplication, Authorization, MemoryStorage, TurnContext, TurnState
from microsoft_agents.hosting.fastapi import CloudAdapter, start_agent_process
from pydantic import BaseModel, Field

from bridge.runtime.base import AgentAuthContext, AgentRequest, AgentResponse, DreamRequest
from bridge.runtime.factory import create_runtime_adapter, runtime_kind_from_env


app = FastAPI(title="Autopilot Azure Container Apps bridge")


def agent365_auth_configured() -> bool:
    return (os.getenv("USE_AGENTIC_AUTH") or "").lower() in {"1", "true", "yes"}


def create_agent365_app() -> tuple[AgentApplication[TurnState], CloudAdapter]:
    storage = MemoryStorage()
    if agent365_auth_configured():
        configuration = load_configuration_from_env(os.environ)
        connection_manager = MsalConnectionManager(**configuration)
        adapter = CloudAdapter(connection_manager=connection_manager)
        authorization = Authorization(storage, connection_manager, **configuration)
        agent = AgentApplication[TurnState](storage=storage, adapter=adapter, authorization=authorization, **configuration)
        return agent, adapter

    adapter = CloudAdapter()
    agent = AgentApplication[TurnState](storage=storage, adapter=adapter)
    return agent, adapter


agent365_app, agent365_adapter = create_agent365_app()


class InvokeRequest(BaseModel):
    conversation_id: str = Field(alias="conversationId", min_length=1)
    message: str = Field(min_length=1)


class InvokeResponse(BaseModel):
    conversation_id: str = Field(alias="conversationId")
    sandbox_id: str = Field(alias="sandboxId")
    gateway_url: str = Field(alias="gatewayUrl")
    reused_existing_sandbox: bool = Field(alias="reusedExistingSandbox")
    response: str


class DreamRunRequest(BaseModel):
    focus: str = Field(default="", max_length=2000)
    max_records: int = Field(default=5, alias="maxRecords", ge=1, le=10)


class DreamRunResponse(BaseModel):
    session_id: str = Field(alias="sessionId")
    sandbox_id: str = Field(alias="sandboxId")
    gateway_url: str = Field(alias="gatewayUrl")
    reused_existing_sandbox: bool = Field(alias="reusedExistingSandbox")
    response: str
    learning_status: dict[str, Any] = Field(alias="learningStatus")


class CollectiveLearningApprovalRequest(BaseModel):
    packet_digest: str = Field(alias="packetDigest", min_length=64, max_length=64)
    approved_by: str = Field(alias="approvedBy", min_length=1, max_length=200)


_teams_diag: deque[dict[str, Any]] = deque(maxlen=20)
_teams_memory: dict[str, deque[dict[str, Any]]] = {}
_SUPPORTED_TEAMS_CONVERSATION_TYPES = {"personal", "groupchat", "channel", "team"}
_CHANNEL_TEAMS_CONVERSATION_TYPES = {"channel", "team"}
_TYPING_TEAMS_CONVERSATION_TYPES = {"personal", "groupchat"}
_NO_RESPONSE = "NO_RESPONSE"
_TEAMS_REACTION_PREFIX = "TEAMS_REACTION:"
_TEAMS_REACTION_ALIASES = {
    "eyes": "1f440_eyes",
    "working": "1f440_eyes",
    "watching": "1f440_eyes",
    "like": "like",
    "thanks": "like",
    "thank_you": "like",
    "heart": "heart",
    "love": "heart",
    "praise": "heart",
    "smile": "smile",
    "joke": "smile",
    "surprised": "surprised",
    "surprise": "surprised",
    "shocked": "surprised",
    "check": "2705_whiteheavycheckmark",
    "done": "2705_whiteheavycheckmark",
}


def record_teams_diag(event: dict[str, Any]) -> None:
    _teams_diag.appendleft({"ts": time.time(), **event})


def env_optional(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value and value != "not-configured":
            return value
    return default


def field_value(value: Any, *names: str) -> Any:
    current = value
    for name in names:
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(name)
        else:
            current = getattr(current, name, None)
    return current


def teams_conversation_type(activity: Activity) -> str:
    return str(field_value(activity, "conversation", "conversation_type") or "").lower()


def teams_conversation_id(activity: Activity) -> str:
    return str(field_value(activity, "conversation", "id") or "")


def normalize_teams_text(text: str) -> str:
    text = re.sub(r"</p>\s*<p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?p[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def bot_mention_texts(activity: Activity) -> list[str]:
    recipient_id = field_value(activity, "recipient", "id")
    recipient_name = field_value(activity, "recipient", "name")
    texts: list[str] = []

    for entity in field_value(activity, "entities") or []:
        entity_type = field_value(entity, "type")
        if entity_type != "mention":
            continue

        mentioned_id = field_value(entity, "mentioned", "id")
        mentioned_name = field_value(entity, "mentioned", "name")
        is_recipient = bool(recipient_id and mentioned_id == recipient_id) or bool(recipient_name and mentioned_name == recipient_name)
        if not is_recipient:
            continue

        text = field_value(entity, "text")
        if isinstance(text, str) and text.strip():
            texts.append(text)

    raw_text = field_value(activity, "text")
    if isinstance(raw_text, str) and recipient_name:
        mention_pattern = re.compile(rf"<at>\s*{re.escape(str(recipient_name))}\s*</at>", flags=re.IGNORECASE)
        texts.extend(match.group(0) for match in mention_pattern.finditer(raw_text))

    return list(dict.fromkeys(texts))


def bot_is_mentioned(activity: Activity) -> bool:
    return bool(bot_mention_texts(activity))


def teams_is_targeted(activity: Activity) -> bool:
    return bool(field_value(activity, "recipient", "is_targeted") or field_value(activity, "recipient", "isTargeted"))


def teams_prompt_text(activity: Activity) -> str:
    message = field_value(activity, "text")
    if not isinstance(message, str):
        return ""

    for mention in bot_mention_texts(activity):
        message = message.replace(mention, "")

    return normalize_teams_text(message)


def teams_session_key(activity: Activity) -> str:
    conversation_type = teams_conversation_type(activity) or "unknown"
    conversation_id = teams_conversation_id(activity)
    if conversation_type not in _CHANNEL_TEAMS_CONVERSATION_TYPES:
        return f"teams:{conversation_type}:{conversation_id}"

    thread_id = channel_thread_root_id(activity) or field_value(activity, "reply_to_id") or field_value(activity, "id") or "root"
    team_id = field_value(activity, "channel_data", "team", "id")
    channel_id = field_value(activity, "channel_data", "channel", "id")
    parts = ["teams", conversation_type, conversation_id]
    if team_id:
        parts.extend(["team", str(team_id)])
    if channel_id:
        parts.extend(["channel", str(channel_id)])
    parts.extend(["thread", str(thread_id)])
    return ":".join(parts)


def should_observe_unmentioned_messages() -> bool:
    return env_optional("AUTOPILOT_TEAMS_OBSERVE_UNMENTIONED", "OPENCLAW_TEAMS_OBSERVE_UNMENTIONED", default="true").lower() in {"1", "true", "yes"}


def should_add_processing_reaction() -> bool:
    return env_optional("AUTOPILOT_TEAMS_ADD_REACTIONS", "OPENCLAW_TEAMS_ADD_REACTIONS", default="true").lower() in {"1", "true", "yes"}


def should_quote_group_responses() -> bool:
    return env_optional("AUTOPILOT_TEAMS_QUOTED_REPLIES", "OPENCLAW_TEAMS_QUOTED_REPLIES", default="true").lower() in {"1", "true", "yes"}


def should_add_status_reaction(activity: Activity) -> bool:
    return should_add_processing_reaction() and teams_conversation_type(activity) != "personal" and not teams_is_targeted(activity) and bool(field_value(activity, "id"))


def normalized_ascii(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()


def is_gratitude_message(message: str) -> bool:
    normalized = normalized_ascii(message)
    return bool(re.search(r"\b(thanks|thank you|thx|diky|dik|dekuji|good bot)\b", normalized))


def should_acknowledge_with_reaction(message: str, signal_type: str, session_key: str | None) -> bool:
    if signal_type in {"explicit_bot_mention", "targeted_private_message", "textual_bot_name_mention"}:
        return False
    return is_gratitude_message(message) and memory_has_agent_response(session_key)


def normalize_requested_reaction(value: str) -> str | None:
    key = re.sub(r"[\s-]+", "_", value.strip().lower())
    return _TEAMS_REACTION_ALIASES.get(key)


def split_teams_response_instructions(response: str) -> tuple[str, str | None]:
    visible_lines: list[str] = []
    reaction: str | None = None
    for line in response.splitlines():
        match = re.match(rf"^\s*{re.escape(_TEAMS_REACTION_PREFIX)}\s*(.+?)\s*$", line, flags=re.IGNORECASE)
        if match:
            reaction = reaction or normalize_requested_reaction(match.group(1))
            continue
        visible_lines.append(line)
    return "\n".join(visible_lines).strip(), reaction


def runtime_display_name() -> str:
    configured = env_optional("AUTOPILOT_TEAMS_NAME", "OPENCLAW_TEAMS_NAME")
    if configured:
        return configured
    return {
        "hermes": "Hermes",
        "openclaw": "OpenClaw",
    }.get(runtime_kind_from_env(), "Autopilot")


def env_int(name: str, default: int) -> int:
    try:
        return int(env_optional(name, default=str(default)))
    except ValueError:
        return default


def user_display_name(activity: Activity) -> str:
    return str(field_value(activity, "from_property", "name") or field_value(activity, "from_", "name") or field_value(activity, "from", "name") or "unknown user")


def user_id(activity: Activity) -> str:
    return str(
        field_value(activity, "from_property", "aad_object_id")
        or field_value(activity, "from_property", "aadObjectId")
        or field_value(activity, "from_property", "id")
        or field_value(activity, "from_", "aad_object_id")
        or field_value(activity, "from_", "aadObjectId")
        or field_value(activity, "from_", "id")
        or field_value(activity, "from", "aad_object_id")
        or field_value(activity, "from", "aadObjectId")
        or field_value(activity, "from", "id")
        or "unknown-user"
    )


def teams_runtime_source(activity: Activity) -> str:
    conversation_type = teams_conversation_type(activity)
    if conversation_type == "personal":
        return "teams_personal"
    if conversation_type in _CHANNEL_TEAMS_CONVERSATION_TYPES:
        return "teams_channel"
    return "teams_group"


def teams_conversation_boundary(activity: Activity) -> str:
    if teams_is_targeted(activity):
        return "targeted_private"
    conversation_type = teams_conversation_type(activity)
    if conversation_type == "personal":
        return "one_to_one"
    if conversation_type == "groupchat":
        return "shared_group"
    if conversation_type in _CHANNEL_TEAMS_CONVERSATION_TYPES:
        return "public_channel"
    return "unknown"


def agent_auth_context(activity: Activity) -> AgentAuthContext:
    get_instance_id = getattr(activity, "get_agentic_instance_id", None)
    get_agent_user = getattr(activity, "get_agentic_user", None)
    instance_id = str((get_instance_id() if callable(get_instance_id) else "") or env_optional("AGENT365_AGENT_IDENTITY_CLIENT_ID"))
    agent_user = get_agent_user() if callable(get_agent_user) else None
    agent_user_id = str(
        field_value(agent_user, "id")
        or field_value(agent_user, "aad_object_id")
        or env_optional("AGENT365_AGENT_USER_ID")
    )
    agent_user_principal_name = env_optional("AGENT365_AGENT_USER_PRINCIPAL_NAME")
    modes = []
    if instance_id:
        modes.append("agent_identity")
    if agent_user_id:
        modes.append("agent_user")
    return AgentAuthContext(
        selected_mode="agent_identity" if instance_id else "none",
        available_modes=tuple(modes),
        conversation_boundary=teams_conversation_boundary(activity),
        invoking_user_id=user_id(activity),
        invoking_user_name=user_display_name(activity),
        agent_instance_id=instance_id,
        agent_user_id=agent_user_id,
        agent_user_principal_name=agent_user_principal_name,
        obo_available=False,
    )


def format_auth_boundary(context: AgentAuthContext) -> str:
    available = ", ".join(context.available_modes) or "none"
    return (
        "Authorization boundary:\n"
        f"- Selected default mode: {context.selected_mode}\n"
        f"- Available modes: {available}\n"
        f"- Conversation boundary: {context.conversation_boundary}\n"
        f"- Invoking human: {context.invoking_user_name} ({context.invoking_user_id})\n"
        f"- Agent instance: {context.agent_instance_id or 'unknown'}\n"
        f"- Agent User: {context.agent_user_principal_name or context.agent_user_id or 'not configured'}\n"
        f"- Human OBO available for this turn: {context.obo_available}\n"
        "- Use agent_identity for unattended private services, agent_user only for resources owned by the digital worker, "
        "and never claim user-delegated access when OBO is unavailable.\n"
        "- In shared_group or public_channel conversations, do not reveal private user data in the public response."
    )


def message_mentions_bot_name(activity: Activity, message: str | None = None) -> bool:
    text = normalize_teams_text(message if message is not None else str(field_value(activity, "text") or ""))
    if not text:
        return False
    aliases = {
        runtime_kind_from_env(),
        runtime_display_name().lower(),
        str(field_value(activity, "recipient", "name") or "").lower(),
    }
    normalized = text.lower()
    return any(alias and re.search(rf"\b{re.escape(alias)}\b", normalized) for alias in aliases)


def channel_thread_root_id(activity: Activity) -> str:
    conversation_id = teams_conversation_id(activity)
    match = re.search(r";messageid=([^;]+)", conversation_id)
    return match.group(1) if match else ""


def activity_is_channel_thread_reply(activity: Activity) -> bool:
    root_id = channel_thread_root_id(activity)
    activity_id = str(field_value(activity, "id") or "")
    return bool(root_id and activity_id and activity_id != root_id)


def memory_has_agent_response(session_key: str | None) -> bool:
    if not session_key:
        return False
    return any(event.get("role") == "agent" and not response_should_be_suppressed(str(event.get("text") or "")) for event in teams_memory(session_key))


def memory_has_agent_message_id(session_key: str | None, message_id: str | None) -> bool:
    if not session_key or not message_id:
        return False
    return any(event.get("role") == "agent" and event.get("activityId") == message_id for event in teams_memory(session_key))


def reacted_message_id(activity: MessageReactionActivity) -> str:
    return str(field_value(activity, "reply_to_id") or field_value(activity, "replyToId") or "")


def teams_reaction_path(conversation_id: str, message_id: str, reaction_type: str) -> str:
    if not conversation_id:
        raise ValueError("Teams reaction requires conversation id.")
    if not message_id:
        raise ValueError("Teams reaction requires message id.")
    if not reaction_type:
        raise ValueError("Teams reaction requires reaction type.")
    return f"v3/conversations/{conversation_id}/activities/{message_id}/reactions/{reaction_type}"


def connector_client_from_context(ctx: TurnContext) -> Any:
    connector_client = ctx.turn_state.get("ConnectorClient") if hasattr(ctx, "turn_state") else None
    if not connector_client or not getattr(connector_client, "client", None):
        raise RuntimeError("Teams connector client is not available in turn state.")
    return connector_client


async def apply_message_reaction(ctx: TurnContext, message_id: str, reaction_type: str, *, remove: bool = False) -> None:
    conversation_id = teams_conversation_id(ctx.activity)
    path = teams_reaction_path(conversation_id, message_id, reaction_type)
    connector_client = connector_client_from_context(ctx)
    method = connector_client.client.delete if remove else connector_client.client.put
    async with method(path) as response:
        if response.status >= 300:
            body = await response.text()
            record_teams_diag(
                {
                    "event": "reactionFailed",
                    "messageId": message_id,
                    "reactionType": reaction_type,
                    "operation": "delete" if remove else "add",
                    "statusCode": response.status,
                    "body": truncate_text(body, 500),
                }
            )
            response.raise_for_status()


def teams_signal_type(activity: Activity, *, message: str | None = None, reactions: list[str] | None = None) -> str:
    if reactions:
        return "reaction_to_message"
    if teams_is_targeted(activity):
        return "targeted_private_message"
    if bot_is_mentioned(activity):
        return "explicit_bot_mention"
    if message_mentions_bot_name(activity, message):
        return "textual_bot_name_mention"
    if field_value(activity, "reply_to_id") or activity_is_channel_thread_reply(activity):
        return "reply_in_thread_without_bot_mention"
    return "undirected_message"


def teams_response_contract(activity: Activity, signal_type: str, *, session_key: str | None = None) -> str:
    if signal_type in {"explicit_bot_mention", "targeted_private_message", "textual_bot_name_mention"} or teams_conversation_type(activity) == "personal":
        return "must_answer"
    if signal_type == "reply_in_thread_without_bot_mention" and memory_has_agent_response(session_key):
        return "must_answer"
    return "observe_then_maybe_answer"


def truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 14)].rstrip() + " ...[truncated]"


def teams_memory(session_key: str) -> deque[dict[str, Any]]:
    max_events = env_int("OPENCLAW_TEAMS_MEMORY_MAX_EVENTS", 30)
    memory = _teams_memory.get(session_key)
    if memory is None or memory.maxlen != max_events:
        memory = deque(list(memory or [])[-max_events:], maxlen=max_events)
        _teams_memory[session_key] = memory
    return memory


def remember_teams_event(session_key: str, event: dict[str, Any]) -> None:
    stored = dict(event)
    if isinstance(stored.get("text"), str):
        stored["text"] = truncate_text(stored["text"], env_int("OPENCLAW_TEAMS_MEMORY_EVENT_CHARS", 1200))
    stored.setdefault("ts", time.time())
    teams_memory(session_key).append(stored)


def context_window_size(signal_type: str, response_contract: str) -> int:
    if response_contract == "must_answer":
        return env_int("OPENCLAW_TEAMS_CONTEXT_MUST_ANSWER_EVENTS", 18)
    if signal_type == "reaction_to_message":
        return env_int("OPENCLAW_TEAMS_CONTEXT_REACTION_EVENTS", 6)
    if signal_type == "reply_in_thread_without_bot_mention":
        return env_int("OPENCLAW_TEAMS_CONTEXT_REPLY_EVENTS", 12)
    return env_int("OPENCLAW_TEAMS_CONTEXT_WEAK_SIGNAL_EVENTS", 8)


def render_memory_event(event: dict[str, Any]) -> str:
    role = event.get("role", "event")
    signal_type = event.get("signalType", "")
    sender = event.get("sender", "")
    activity_id = event.get("activityId", "")
    reply_to_id = event.get("replyToId", "")
    reactions = event.get("reactions") or []
    text = event.get("text", "")
    header_parts = [str(role)]
    if signal_type:
        header_parts.append(str(signal_type))
    if sender:
        header_parts.append(f"sender={sender}")
    if activity_id:
        header_parts.append(f"id={activity_id}")
    if reply_to_id:
        header_parts.append(f"replyTo={reply_to_id}")
    if reactions:
        header_parts.append("reactions=" + ",".join(str(reaction) for reaction in reactions))
    return f"- [{' | '.join(header_parts)}] {text}".rstrip()


def format_teams_context(
    session_key: str,
    *,
    signal_type: str,
    response_contract: str,
    reply_to_id: str | None = None,
) -> str:
    memory = list(teams_memory(session_key))
    if not memory:
        return "No prior bridge-observed Teams context for this conversation/thread."

    window_size = context_window_size(signal_type, response_contract)
    selected = memory[-window_size:]

    anchors: list[dict[str, Any]] = []
    if reply_to_id:
        anchors.extend(event for event in memory if event.get("activityId") == reply_to_id)
    anchors.extend(event for event in reversed(memory) if event.get("role") == "agent")

    by_key: dict[str, dict[str, Any]] = {}
    for event in [*anchors[:3], *selected]:
        key = str(event.get("activityId") or event.get("ts") or id(event))
        by_key[key] = event

    rendered_events = [render_memory_event(event) for event in by_key.values()]
    participant_names = sorted({str(event.get("sender")) for event in memory if event.get("sender")})
    max_chars = env_int("OPENCLAW_TEAMS_CONTEXT_MAX_CHARS", 12000)
    context = (
        "Bridge-observed context window:\n"
        f"- Memory policy: bounded local window, reply/reaction anchor if known, latest {runtime_display_name()} answer if known, max {max_chars} chars.\n"
        f"- Stored events for this session/thread: {len(memory)}\n"
        f"- Known participants in stored window: {', '.join(participant_names) if participant_names else 'unknown'}\n"
        + "\n".join(rendered_events)
    )
    return truncate_text(context, max_chars)


def teams_event_memory_record(
    activity: Activity,
    *,
    event: str,
    message: str,
    signal_type: str,
    response_contract: str,
    reactions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "role": "user",
        "event": event,
        "signalType": signal_type,
        "responseContract": response_contract,
        "conversationType": teams_conversation_type(activity),
        "conversationId": teams_conversation_id(activity),
        "activityId": field_value(activity, "id") or "",
        "replyToId": field_value(activity, "reply_to_id") or "",
        "sender": user_display_name(activity),
        "targeted": teams_is_targeted(activity),
        "mentioned": bot_is_mentioned(activity),
        "reactions": reactions or [],
        "text": message,
    }


def agent_memory_record(response: str, activity_id: str | None = None) -> dict[str, Any]:
    return {
        "role": "agent",
        "event": "response",
        "signalType": "agent_response",
        "activityId": activity_id or "",
        "sender": runtime_display_name(),
        "text": response,
    }


def format_teams_event_prompt(
    activity: Activity,
    message: str,
    *,
    event: str,
    context: str = "",
    signal_type: str | None = None,
    response_contract: str | None = None,
    reactions: list[str] | None = None,
) -> str:
    conversation_type = teams_conversation_type(activity) or "unknown"
    signal_type = signal_type or teams_signal_type(activity, message=message, reactions=reactions)
    response_contract = response_contract or teams_response_contract(activity, signal_type)
    sender = user_display_name(activity)
    targeted = teams_is_targeted(activity)
    mention = bot_is_mentioned(activity)
    thread_id = field_value(activity, "reply_to_id") or field_value(activity, "id") or "root"
    auth_context = agent_auth_context(activity)
    reaction_line = f"\nReaction types: {', '.join(reactions)}" if reactions else ""
    return (
        f"You are {runtime_display_name()} participating in a Microsoft Teams conversation.\n"
        f"Event: {event}\n"
        f"Signal type: {signal_type}\n"
        f"Response contract: {response_contract}\n"
        f"Conversation type: {conversation_type}\n"
        f"Conversation id: {teams_conversation_id(activity)}\n"
        f"Activity id: {field_value(activity, 'id') or ''}\n"
        f"Reply-to activity id: {field_value(activity, 'reply_to_id') or ''}\n"
        f"Thread/message id: {thread_id}\n"
        f"Sender: {sender}\n"
        f"Targeted private message to you: {targeted}\n"
        f"Bot explicitly mentioned: {mention}{reaction_line}\n\n"
        f"{format_auth_boundary(auth_context)}\n\n"
        f"Context available to you:\n{context or 'No prior context was provided.'}\n\n"
        f"Message text:\n{message}\n\n"
        "Decision policy:\n"
        "- If response_contract is must_answer, answer normally.\n"
        "- If signal_type is textual_bot_name_mention, answer because the conversation invoked you by name even without an explicit Teams mention.\n"
        "- If signal_type is reply_in_thread_without_bot_mention, treat it as relevant context because it is in a thread where you may have participated. If response_contract is must_answer, answer normally.\n"
        "- If signal_type is reaction_to_message, treat emoji as feedback or status context; answer only when the reaction implies a question, risk, approval flow, or useful follow-up.\n"
        "- If signal_type is undirected_message, treat it as weak signal context. You decide whether to answer or return NO_RESPONSE. Reply only when your input is clearly useful, urgent, corrective, or prevents a material mistake.\n"
        "- In group/chat channel scopes, you may request one Teams reaction on the triggering message by adding a separate control line: TEAMS_REACTION: <name>. Allowed names: eyes, like, heart, smile, surprised, check. Do not use this control line in personal chats.\n"
        "- Good reaction choices: eyes=working or looking into it, like=thanks/simple acknowledgement, heart=user praises you, smile=light related joke, surprised=risky or alarming proposal, check=done/confirmed.\n"
        "- If you use a reaction and no public answer is needed, combine it with NO_RESPONSE.\n"
        f"- If you should not jump in, return exactly {_NO_RESPONSE} and nothing else.\n"
        "- If you reply to an unmentioned discussion, keep it concise and explain why you are jumping in."
    )


@cache
def runtime_adapter():
    return create_runtime_adapter()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/messages")
async def agent365_messages(request: Request):
    return await start_agent_process(request, agent365_app, agent365_adapter)


@app.middleware("http")
async def teams_diagnostics(request: Request, call_next):
    if request.url.path != "/api/messages":
        return await call_next(request)

    body = await request.body()
    record_teams_diag(
        {
            "event": "request",
            "contentLength": len(body),
            "hasAuthorization": bool(request.headers.get("authorization")),
        }
    )

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    replay_request = Request(request.scope, receive)
    try:
        response = await call_next(replay_request)
        record_teams_diag({"event": "response", "statusCode": response.status_code})
        return response
    except Exception as exc:
        record_teams_diag({"event": "exception", "type": exc.__class__.__name__, "message": str(exc)})
        raise


@app.get("/diag/teams")
def teams_diag() -> JSONResponse:
    if os.getenv("OPENCLAW_BRIDGE_DEBUG", "").lower() not in {"1", "true", "yes"}:
        raise HTTPException(status_code=404, detail="Not found.")
    return JSONResponse(
        {
            "events": list(_teams_diag),
            "agent365Endpoint": "/api/messages",
        }
    )


@app.post("/invoke", response_model=InvokeResponse, response_model_by_alias=True)
async def invoke(request: InvokeRequest) -> InvokeResponse:
    try:
        response = await invoke_agent_runtime(
            conversation_id=request.conversation_id,
            session_key=f"bridge:{request.conversation_id}",
            message=request.message,
            source="invoke",
            user_id="invoke",
            must_answer=True,
        )
        return invoke_response_from_agent_response(request.conversation_id, response)
    except Exception as exc:
        detail = {"message": str(exc)}
        sandbox_id = getattr(exc, "sandbox_id", "")
        gateway_url = getattr(exc, "gateway_url", "")
        if sandbox_id:
            detail["sandboxId"] = sandbox_id
        if gateway_url:
            detail["gatewayUrl"] = gateway_url
        if os.getenv("OPENCLAW_BRIDGE_DEBUG", "").lower() in {"1", "true", "yes"}:
            detail["type"] = exc.__class__.__name__
        raise HTTPException(status_code=500, detail=detail) from exc


def require_operator_key(request: Request) -> None:
    expected = os.getenv("API_SERVER_KEY", "")
    supplied = request.headers.get("x-autopilot-key", "")
    if not expected or expected == "not-configured" or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="A valid X-Autopilot-Key header is required.")


@app.post("/internal/dream", response_model=DreamRunResponse, response_model_by_alias=True)
async def dream(request: DreamRunRequest, http_request: Request) -> DreamRunResponse:
    require_operator_key(http_request)
    adapter = runtime_adapter()
    if adapter.runtime_kind != "hermes":
        raise HTTPException(status_code=409, detail="Dream runs are supported only by the Hermes runtime.")
    worker_id = os.getenv("WORKER_ID", os.getenv("AUTOPILOT_NAME", "hermes"))
    session_id = f"dream:{worker_id}:{uuid.uuid4().hex}"
    try:
        result = await adapter.dream(
            DreamRequest(
                session_id=session_id,
                focus=request.focus,
                max_records=request.max_records,
            )
        )
    except Exception as exc:
        detail = {"message": str(exc)}
        sandbox_id = getattr(exc, "sandbox_id", "")
        gateway_url = getattr(exc, "gateway_url", "")
        if sandbox_id:
            detail["sandboxId"] = sandbox_id
        if gateway_url:
            detail["gatewayUrl"] = gateway_url
        raise HTTPException(status_code=500, detail=detail) from exc
    return DreamRunResponse(
        sessionId=session_id,
        sandboxId=str(result.agent.raw.get("sandboxId") or ""),
        gatewayUrl=str(result.agent.raw.get("gatewayUrl") or ""),
        reusedExistingSandbox=bool(result.agent.raw.get("reusedExistingSandbox")),
        response=result.agent.text,
        learningStatus=result.learning_status,
    )


@app.post("/internal/collective-learning/prepare")
async def prepare_collective_learning(http_request: Request) -> dict[str, Any]:
    require_operator_key(http_request)
    adapter = runtime_adapter()
    if adapter.runtime_kind != "hermes":
        raise HTTPException(status_code=409, detail="Collective Learning Review is supported only by Hermes.")
    try:
        return await adapter.prepare_collective_learning()
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"type": exc.__class__.__name__, "message": str(exc)}) from exc


@app.post("/internal/collective-learning/approve")
async def approve_collective_learning(
    request: CollectiveLearningApprovalRequest,
    http_request: Request,
) -> dict[str, Any]:
    require_operator_key(http_request)
    adapter = runtime_adapter()
    if adapter.runtime_kind != "hermes":
        raise HTTPException(status_code=409, detail="Collective Learning Review is supported only by Hermes.")
    try:
        return await adapter.approve_collective_learning(
            packet_digest=request.packet_digest,
            approved_by=request.approved_by,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"type": exc.__class__.__name__, "message": str(exc)}) from exc


@app.get("/internal/collective-learning/export")
async def export_collective_learning(http_request: Request) -> dict[str, Any]:
    require_operator_key(http_request)
    adapter = runtime_adapter()
    if adapter.runtime_kind != "hermes":
        raise HTTPException(status_code=409, detail="Collective Learning Review is supported only by Hermes.")
    try:
        return await adapter.export_collective_learning()
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"type": exc.__class__.__name__, "message": str(exc)}) from exc


@agent365_app.activity("message")
async def handle_teams_message(ctx: TurnContext, _state: TurnState) -> None:
    conversation_type = teams_conversation_type(ctx.activity)
    conversation_id = teams_conversation_id(ctx.activity)
    mentioned = bot_is_mentioned(ctx.activity)
    targeted = teams_is_targeted(ctx.activity)
    record_teams_diag(
        {
            "event": "handler",
            "activityId": ctx.activity.id,
            "conversationType": conversation_type,
            "conversationId": conversation_id,
            "replyToId": ctx.activity.reply_to_id,
            "mentioned": mentioned,
            "targeted": targeted,
            "textLength": len(ctx.activity.text or ""),
        }
    )
    if conversation_type not in _SUPPORTED_TEAMS_CONVERSATION_TYPES:
        await ctx.send_activity(f"{runtime_display_name()} does not support Teams conversation type '{conversation_type or 'unknown'}' yet.")
        return

    if conversation_type != "personal" and not mentioned and not targeted and not should_observe_unmentioned_messages():
        record_teams_diag({"event": "ignoredUnmentionedMessage", "conversationId": conversation_id, "conversationType": conversation_type})
        return

    message = teams_prompt_text(ctx.activity)
    if not message:
        if conversation_type == "personal":
            await ctx.send_activity(f"Send a text prompt for {runtime_display_name()}.")
        else:
            name = runtime_display_name()
            await ctx.send_activity(f"Mention {name} with a text prompt, for example: @{name} list services from private incidents MCP.")
        return

    session_key = teams_session_key(ctx.activity)
    signal_type = teams_signal_type(ctx.activity, message=message)
    response_contract = teams_response_contract(ctx.activity, signal_type, session_key=session_key)
    must_answer = response_contract == "must_answer"
    if should_add_processing_reaction() and should_acknowledge_with_reaction(message, signal_type, session_key):
        remember_teams_event(
            session_key,
            teams_event_memory_record(
                ctx.activity,
                event="message",
                message=message,
                signal_type=signal_type,
                response_contract="emoji_acknowledgement",
            ),
        )
        await send_message_reaction(ctx, ctx.activity.id, "like")
        record_teams_diag({"event": "responseSuppressed", "reason": "emojiAcknowledgement", "conversationId": conversation_id})
        return
    context = format_teams_context(
        session_key,
        signal_type=signal_type,
        response_contract=response_contract,
        reply_to_id=field_value(ctx.activity, "reply_to_id"),
    )
    prompt = format_teams_event_prompt(
        ctx.activity,
        message,
        event="message",
        context=context,
        signal_type=signal_type,
        response_contract=response_contract,
    )
    remember_teams_event(
        session_key,
        teams_event_memory_record(
            ctx.activity,
            event="message",
            message=message,
            signal_type=signal_type,
            response_contract=response_contract,
        ),
    )
    status_reaction_message_id = field_value(ctx.activity, "id") if should_add_status_reaction(ctx.activity) else None
    if status_reaction_message_id:
        await send_message_reaction(ctx, status_reaction_message_id, "1f440_eyes")
    await run_agent_runtime_for_teams(
        ctx,
        conversation_id=conversation_id,
        session_key=session_key,
        message=prompt,
        targeted_response=targeted,
        memory_session_key=session_key,
        suppress_no_response=not must_answer,
        status_reaction_message_id=status_reaction_message_id,
    )


@agent365_app.activity("messageReaction")
async def handle_teams_message_reaction(ctx: TurnContext, _state: TurnState) -> None:
    conversation_id = teams_conversation_id(ctx.activity)
    added = [reaction.type for reaction in (ctx.activity.reactions_added or [])]
    removed = [reaction.type for reaction in (ctx.activity.reactions_removed or [])]
    reacted_to_id = reacted_message_id(ctx.activity)
    record_teams_diag(
        {
            "event": "messageReaction",
            "activityId": ctx.activity.id,
            "conversationType": teams_conversation_type(ctx.activity),
            "conversationId": conversation_id,
            "replyToId": ctx.activity.reply_to_id,
            "reactedToId": reacted_to_id,
            "added": added,
            "removed": removed,
        }
    )
    if not should_observe_unmentioned_messages() or not added:
        return

    session_key = teams_session_key(ctx.activity)
    if not memory_has_agent_message_id(session_key, reacted_to_id):
        record_teams_diag({"event": "ignoredReactionToNonAgentMessage", "conversationId": conversation_id, "reactedToId": reacted_to_id})
        return
    signal_type = teams_signal_type(ctx.activity, reactions=added)
    response_contract = teams_response_contract(ctx.activity, signal_type, session_key=session_key)
    context = format_teams_context(
        session_key,
        signal_type=signal_type,
        response_contract=response_contract,
        reply_to_id=field_value(ctx.activity, "reply_to_id"),
    )
    prompt = format_teams_event_prompt(
        ctx.activity,
        "",
        event="messageReaction",
        context=context,
        signal_type=signal_type,
        response_contract=response_contract,
        reactions=added,
    )
    remember_teams_event(
        session_key,
        teams_event_memory_record(
            ctx.activity,
            event="messageReaction",
            message="",
            signal_type=signal_type,
            response_contract=response_contract,
            reactions=added,
        ),
    )
    await run_agent_runtime_for_teams(
        ctx,
        conversation_id=conversation_id,
        session_key=session_key,
        message=prompt,
        memory_session_key=session_key,
        suppress_no_response=True,
    )


@agent365_app.activity("messageUpdate")
async def handle_teams_message_update(ctx: TurnContext, _state: TurnState) -> None:
    record_teams_diag(
        {
            "event": "messageUpdate",
            "activityId": ctx.activity.id,
            "conversationType": teams_conversation_type(ctx.activity),
            "conversationId": teams_conversation_id(ctx.activity),
            "replyToId": ctx.activity.reply_to_id,
            "textLength": len(ctx.activity.text or ""),
        }
    )


def supports_typing_indicators(ctx: TurnContext) -> bool:
    return teams_conversation_type(ctx.activity) in _TYPING_TEAMS_CONVERSATION_TYPES


async def send_typing_indicators(ctx: TurnContext, conversation_id: str, done: asyncio.Event) -> None:
    conversation_type = teams_conversation_type(ctx.activity)
    if not supports_typing_indicators(ctx):
        record_teams_diag(
            {
                "event": "typingSkipped",
                "conversationId": conversation_id,
                "conversationType": conversation_type,
                "reason": "unsupportedConversationType",
            }
        )
        return

    try:
        while not done.is_set():
            await ctx.send_activity(Activity(type="typing"))
            record_teams_diag(
                {
                    "event": "typingSent",
                    "conversationId": conversation_id,
                    "conversationType": conversation_type,
                }
            )
            try:
                await asyncio.wait_for(done.wait(), timeout=4)
            except asyncio.TimeoutError:
                continue
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        record_teams_diag(
            {
                "event": "typingFailed",
                "conversationId": conversation_id,
                "conversationType": conversation_type,
                "type": exc.__class__.__name__,
                "message": str(exc),
            }
        )


async def send_message_reaction(ctx: TurnContext, message_id: str | None, reaction_type: str) -> None:
    if not message_id:
        return
    try:
        await apply_message_reaction(ctx, message_id, reaction_type)
        record_teams_diag({"event": "reactionSent", "messageId": message_id, "reactionType": reaction_type})
    except (ClientResponseError, TimeoutError, OSError, RuntimeError, ValueError) as exc:
        record_teams_diag(
            {
                "event": "reactionFailed",
                "messageId": message_id,
                "reactionType": reaction_type,
                "operation": "add",
                "type": exc.__class__.__name__,
                "message": str(exc),
            }
        )


async def delete_message_reaction(ctx: TurnContext, message_id: str | None, reaction_type: str) -> None:
    if not message_id:
        return
    try:
        await apply_message_reaction(ctx, message_id, reaction_type, remove=True)
        record_teams_diag({"event": "reactionDeleted", "messageId": message_id, "reactionType": reaction_type})
    except (ClientResponseError, TimeoutError, OSError, RuntimeError, ValueError) as exc:
        record_teams_diag(
            {
                "event": "reactionFailed",
                "messageId": message_id,
                "reactionType": reaction_type,
                "operation": "delete",
                "type": exc.__class__.__name__,
                "message": str(exc),
            }
        )


def response_should_be_suppressed(response: str) -> bool:
    visible_response, _reaction = split_teams_response_instructions(response)
    return visible_response.strip().upper() == _NO_RESPONSE


def response_has_visible_text(response: str) -> bool:
    visible_response, _reaction = split_teams_response_instructions(response)
    return bool(visible_response and visible_response.strip().upper() != _NO_RESPONSE)


async def send_teams_response(ctx: TurnContext, response: str, *, targeted_response: bool = False) -> str | None:
    sent = await ctx.send_activity(response)
    record_teams_diag({"event": "responseSent", "method": "agent365SendActivity", "conversationType": teams_conversation_type(ctx.activity)})
    return field_value(sent, "id")


def supports_streaming_response(ctx: TurnContext) -> bool:
    return False


async def run_agent_runtime_for_teams(
    ctx: TurnContext,
    *,
    conversation_id: str,
    session_key: str,
    message: str,
    targeted_response: bool = False,
    memory_session_key: str | None = None,
    suppress_no_response: bool = False,
    status_reaction_message_id: str | None = None,
) -> None:
    done = asyncio.Event()
    show_public_progress = supports_streaming_response(ctx) and not targeted_response and not suppress_no_response
    progress_task = asyncio.create_task(send_stream_progress_updates(ctx, conversation_id, done)) if show_public_progress else None
    typing_task = asyncio.create_task(send_typing_indicators(ctx, conversation_id, done))
    streamed = False

    async def emit_delta(delta: str) -> None:
        nonlocal streamed
        if not delta:
            return
        streamed = True
        record_teams_diag({"event": "streamDeltaSkipped", "conversationId": conversation_id, "length": len(delta), "reason": "agent365"})

    try:
        auth_context = agent_auth_context(ctx.activity)
        record_teams_diag({"event": "backgroundStart", "conversationId": conversation_id})
        record_teams_diag(
            {
                "event": "authBoundary",
                "conversationId": conversation_id,
                "selectedAuthMode": auth_context.selected_mode,
                "availableAuthModes": list(auth_context.available_modes),
                "conversationBoundary": auth_context.conversation_boundary,
                "oboAvailable": auth_context.obo_available,
            }
        )
        result = await invoke_agent_runtime(
            conversation_id=conversation_id,
            session_key=session_key,
            message=message,
            source=teams_runtime_source(ctx.activity),
            user_id=user_id(ctx.activity),
            must_answer=not suppress_no_response,
            auth_context=auth_context,
            on_delta=emit_delta if show_public_progress else None,
        )
        visible_response, requested_reaction = split_teams_response_instructions(result.text)
        if status_reaction_message_id:
            await delete_message_reaction(ctx, status_reaction_message_id, "1f440_eyes")
        if requested_reaction and should_add_processing_reaction():
            await send_message_reaction(ctx, field_value(ctx.activity, "id"), requested_reaction)
        if response_should_be_suppressed(result.text) or (requested_reaction and not response_has_visible_text(result.text)):
            record_teams_diag({"event": "responseSuppressed", "conversationId": conversation_id})
            if memory_session_key:
                remember_teams_event(memory_session_key, agent_memory_record(visible_response or _NO_RESPONSE))
        elif not streamed:
            sent_activity_id = await send_teams_response(ctx, visible_response, targeted_response=targeted_response)
            if memory_session_key:
                remember_teams_event(memory_session_key, agent_memory_record(visible_response, sent_activity_id))
        else:
            record_teams_diag({"event": "streamCloseSkipped", "conversationId": conversation_id, "reason": "agent365"})
            if memory_session_key:
                remember_teams_event(memory_session_key, agent_memory_record(visible_response or _NO_RESPONSE))
        record_teams_diag({"event": "streamFinalSent", "conversationId": conversation_id})
    except Exception as exc:
        if status_reaction_message_id:
            await delete_message_reaction(ctx, status_reaction_message_id, "1f440_eyes")
        record_teams_diag(
            {
                "event": "backgroundException",
                "conversationId": conversation_id,
                "type": exc.__class__.__name__,
                "message": str(exc),
            }
        )
        try:
            await ctx.send_activity(f"{runtime_display_name()} could not complete this request: {exc}")
        except Exception as send_exc:
            record_teams_diag(
                {
                    "event": "streamErrorSendFailed",
                    "conversationId": conversation_id,
                    "type": send_exc.__class__.__name__,
                    "message": str(send_exc),
                }
            )
    finally:
        done.set()
        background_tasks = [task for task in (progress_task, typing_task) if task]
        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)


async def send_stream_progress_updates(ctx: TurnContext, conversation_id: str, done: asyncio.Event) -> None:
    try:
        record_teams_diag({"event": "streamInformativeSkipped", "conversationId": conversation_id, "message": f"Waking {runtime_display_name()} Sandbox..."})
        await asyncio.wait_for(done.wait(), timeout=int(env_optional("AUTOPILOT_TEAMS_PROGRESS_DELAY_SECONDS", "OPENCLAW_TEAMS_PROGRESS_DELAY_SECONDS", default="10")))
    except asyncio.TimeoutError:
        record_teams_diag({"event": "streamInformativeSkipped", "conversationId": conversation_id, "message": f"{runtime_display_name()} is still working..."})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        record_teams_diag(
            {
                "event": "streamInformativeFailed",
                "conversationId": conversation_id,
                "type": exc.__class__.__name__,
                "message": str(exc),
            }
        )


async def invoke_agent_runtime(
    *,
    conversation_id: str,
    session_key: str,
    message: str,
    source: str,
    user_id: str,
    must_answer: bool,
    context: str = "",
    metadata: dict[str, Any] | None = None,
    auth_context: AgentAuthContext | None = None,
    on_delta=None,
) -> AgentResponse:
    request = AgentRequest(
        prompt=message,
        conversation_id=session_key,
        user_id=user_id,
        source=source,
        must_answer=must_answer,
        context=context,
        metadata={
            "conversationId": conversation_id,
            **(auth_context.as_metadata() if auth_context else {}),
            **(metadata or {}),
        },
        auth=auth_context,
        on_delta=on_delta,
    )
    return await runtime_adapter().invoke(request)


def invoke_response_from_agent_response(conversation_id: str, response: AgentResponse) -> InvokeResponse:
    return InvokeResponse(
        conversationId=conversation_id,
        sandboxId=str(response.raw.get("sandboxId") or ""),
        gatewayUrl=str(response.raw.get("gatewayUrl") or ""),
        reusedExistingSandbox=bool(response.raw.get("reusedExistingSandbox")),
        response=response.text,
    )
