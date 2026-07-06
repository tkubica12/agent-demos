from __future__ import annotations

import asyncio
import html
import os
import re
import time
import unicodedata
from collections import deque
from functools import cache
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from microsoft_teams.api import MessageActivity, MessageActivityInput, MessageReactionActivity, TypingActivityInput
from microsoft_teams.apps import ActivityContext, App, FastAPIAdapter
from pydantic import BaseModel, Field

from bridge.runtime.base import AgentRequest, AgentResponse
from bridge.runtime.factory import create_runtime_adapter


def configured_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name, "").strip()
        if value and value != "not-configured":
            return value
    return None


app = FastAPI(title="Autopilot Azure Container Apps bridge")
teams_adapter = FastAPIAdapter(app=app)
teams_app = App(
    http_server_adapter=teams_adapter,
    client_id=configured_env("OPENCLAW_TEAMS_BOT_ID", "CLIENT_ID"),
    client_secret=configured_env("OPENCLAW_TEAMS_BOT_SECRET", "CLIENT_SECRET"),
    tenant_id=configured_env("OPENCLAW_TEAMS_BOT_TENANT_ID", "TENANT_ID"),
    skip_auth=(os.getenv("OPENCLAW_TEAMS_SKIP_AUTH") or "").lower() in {"1", "true", "yes"},
)


class InvokeRequest(BaseModel):
    conversation_id: str = Field(alias="conversationId", min_length=1)
    message: str = Field(min_length=1)


class InvokeResponse(BaseModel):
    conversation_id: str = Field(alias="conversationId")
    sandbox_id: str = Field(alias="sandboxId")
    gateway_url: str = Field(alias="gatewayUrl")
    reused_existing_sandbox: bool = Field(alias="reusedExistingSandbox")
    response: str


_teams_diag: deque[dict[str, Any]] = deque(maxlen=20)
_teams_memory: dict[str, deque[dict[str, Any]]] = {}
_SUPPORTED_TEAMS_CONVERSATION_TYPES = {"personal", "groupchat", "channel", "team"}
_CHANNEL_TEAMS_CONVERSATION_TYPES = {"channel", "team"}
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


def teams_conversation_type(activity: MessageActivity) -> str:
    return str(field_value(activity, "conversation", "conversation_type") or "").lower()


def teams_conversation_id(activity: MessageActivity) -> str:
    return str(field_value(activity, "conversation", "id") or "")


def normalize_teams_text(text: str) -> str:
    text = re.sub(r"</p>\s*<p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?p[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def bot_mention_texts(activity: MessageActivity) -> list[str]:
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


def bot_is_mentioned(activity: MessageActivity) -> bool:
    return bool(bot_mention_texts(activity))


def teams_is_targeted(activity: MessageActivity) -> bool:
    return bool(field_value(activity, "recipient", "is_targeted") or field_value(activity, "recipient", "isTargeted"))


def teams_prompt_text(activity: MessageActivity) -> str:
    message = field_value(activity, "text")
    if not isinstance(message, str):
        return ""

    for mention in bot_mention_texts(activity):
        message = message.replace(mention, "")

    return normalize_teams_text(message)


def teams_session_key(activity: MessageActivity) -> str:
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


def should_add_status_reaction(activity: MessageActivity) -> bool:
    return should_add_processing_reaction() and teams_conversation_type(activity) != "personal" and not teams_is_targeted(activity) and bool(field_value(activity, "id"))


def normalized_ascii(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()


def is_gratitude_message(message: str) -> bool:
    normalized = normalized_ascii(message)
    return bool(re.search(r"\b(thanks|thank you|thx|diky|dik|dekuji|good bot)\b", normalized))


def should_acknowledge_with_reaction(message: str, signal_type: str, session_key: str | None) -> bool:
    if signal_type in {"explicit_bot_mention", "targeted_private_message", "textual_bot_name_mention"}:
        return False
    return is_gratitude_message(message) and memory_has_openclaw_response(session_key)


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


def env_int(name: str, default: int) -> int:
    try:
        return int(env_optional(name, default=str(default)))
    except ValueError:
        return default


def user_display_name(activity: MessageActivity) -> str:
    return str(field_value(activity, "from_", "name") or field_value(activity, "from", "name") or "unknown user")


def user_id(activity: MessageActivity) -> str:
    return str(
        field_value(activity, "from_", "aad_object_id")
        or field_value(activity, "from_", "aadObjectId")
        or field_value(activity, "from_", "id")
        or field_value(activity, "from", "aad_object_id")
        or field_value(activity, "from", "aadObjectId")
        or field_value(activity, "from", "id")
        or "unknown-user"
    )


def teams_runtime_source(activity: MessageActivity) -> str:
    conversation_type = teams_conversation_type(activity)
    if conversation_type == "personal":
        return "teams_personal"
    if conversation_type in _CHANNEL_TEAMS_CONVERSATION_TYPES:
        return "teams_channel"
    return "teams_group"


def message_mentions_bot_name(activity: MessageActivity, message: str | None = None) -> bool:
    text = normalize_teams_text(message if message is not None else str(field_value(activity, "text") or ""))
    if not text:
        return False
    aliases = {
        "openclaw",
        str(field_value(activity, "recipient", "name") or "").lower(),
        env_optional("AUTOPILOT_TEAMS_NAME", "OPENCLAW_TEAMS_NAME", default="OpenClaw").lower(),
    }
    normalized = text.lower()
    return any(alias and re.search(rf"\b{re.escape(alias)}\b", normalized) for alias in aliases)


def channel_thread_root_id(activity: MessageActivity) -> str:
    conversation_id = teams_conversation_id(activity)
    match = re.search(r";messageid=([^;]+)", conversation_id)
    return match.group(1) if match else ""


def activity_is_channel_thread_reply(activity: MessageActivity) -> bool:
    root_id = channel_thread_root_id(activity)
    activity_id = str(field_value(activity, "id") or "")
    return bool(root_id and activity_id and activity_id != root_id)


def memory_has_openclaw_response(session_key: str | None) -> bool:
    if not session_key:
        return False
    return any(event.get("role") == "openclaw" and not response_should_be_suppressed(str(event.get("text") or "")) for event in teams_memory(session_key))


def memory_has_openclaw_message_id(session_key: str | None, message_id: str | None) -> bool:
    if not session_key or not message_id:
        return False
    return any(event.get("role") == "openclaw" and event.get("activityId") == message_id for event in teams_memory(session_key))


def reacted_message_id(activity: MessageReactionActivity) -> str:
    return str(field_value(activity, "reply_to_id") or field_value(activity, "replyToId") or "")


def teams_signal_type(activity: MessageActivity, *, message: str | None = None, reactions: list[str] | None = None) -> str:
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


def teams_response_contract(activity: MessageActivity, signal_type: str, *, session_key: str | None = None) -> str:
    if signal_type in {"explicit_bot_mention", "targeted_private_message", "textual_bot_name_mention"} or teams_conversation_type(activity) == "personal":
        return "must_answer"
    if signal_type == "reply_in_thread_without_bot_mention" and memory_has_openclaw_response(session_key):
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
    anchors.extend(event for event in reversed(memory) if event.get("role") == "openclaw")  # latest OpenClaw response first

    by_key: dict[str, dict[str, Any]] = {}
    for event in [*anchors[:3], *selected]:
        key = str(event.get("activityId") or event.get("ts") or id(event))
        by_key[key] = event

    rendered_events = [render_memory_event(event) for event in by_key.values()]
    participant_names = sorted({str(event.get("sender")) for event in memory if event.get("sender")})
    max_chars = env_int("OPENCLAW_TEAMS_CONTEXT_MAX_CHARS", 12000)
    context = (
        "Bridge-observed context window:\n"
        f"- Memory policy: bounded local window, reply/reaction anchor if known, latest OpenClaw answer if known, max {max_chars} chars.\n"
        f"- Stored events for this session/thread: {len(memory)}\n"
        f"- Known participants in stored window: {', '.join(participant_names) if participant_names else 'unknown'}\n"
        + "\n".join(rendered_events)
    )
    return truncate_text(context, max_chars)


def teams_event_memory_record(
    activity: MessageActivity,
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


def openclaw_memory_record(response: str, activity_id: str | None = None) -> dict[str, Any]:
    return {
        "role": "openclaw",
        "event": "response",
        "signalType": "agent_response",
        "activityId": activity_id or "",
        "sender": "OpenClaw",
        "text": response,
    }


def format_teams_event_prompt(
    activity: MessageActivity,
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
    reaction_line = f"\nReaction types: {', '.join(reactions)}" if reactions else ""
    return (
        "You are OpenClaw participating in a Microsoft Teams group conversation.\n"
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


@app.on_event("startup")
async def initialize_teams_app() -> None:
    await teams_app.initialize()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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
            "teamsConfigured": bool(configured_env("OPENCLAW_TEAMS_BOT_ID", "CLIENT_ID")),
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


@teams_app.on_message
async def handle_teams_message(ctx: ActivityContext[MessageActivity]) -> None:
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
        await ctx.send(f"OpenClaw does not support Teams conversation type '{conversation_type or 'unknown'}' yet.")
        return

    if conversation_type != "personal" and not mentioned and not targeted and not should_observe_unmentioned_messages():
        record_teams_diag({"event": "ignoredUnmentionedMessage", "conversationId": conversation_id, "conversationType": conversation_type})
        return

    message = teams_prompt_text(ctx.activity)
    if not message:
        if conversation_type == "personal":
            await ctx.send("Send a text prompt for OpenClaw.")
        else:
            await ctx.send("Mention OpenClaw with a text prompt, for example: @OpenClaw list services from private incidents MCP.")
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
    await send_typing_indicator(ctx, conversation_id)
    asyncio.create_task(
        run_agent_runtime_for_teams(
            ctx,
            conversation_id=conversation_id,
            session_key=session_key,
            message=prompt,
            targeted_response=targeted,
            memory_session_key=session_key,
            suppress_no_response=not must_answer,
            status_reaction_message_id=status_reaction_message_id,
        )
    )


@teams_app.on_message_reaction
async def handle_teams_message_reaction(ctx: ActivityContext[MessageReactionActivity]) -> None:
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
    if not memory_has_openclaw_message_id(session_key, reacted_to_id):
        record_teams_diag({"event": "ignoredReactionToNonOpenClawMessage", "conversationId": conversation_id, "reactedToId": reacted_to_id})
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
    asyncio.create_task(
        run_agent_runtime_for_teams(
            ctx,
            conversation_id=conversation_id,
            session_key=session_key,
            message=prompt,
            memory_session_key=session_key,
            suppress_no_response=True,
        )
    )


@teams_app.on_message_update
async def handle_teams_message_update(ctx: ActivityContext[MessageActivity]) -> None:
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


async def send_typing_indicator(ctx: ActivityContext[MessageActivity], conversation_id: str) -> None:
    try:
        await ctx.send(TypingActivityInput())
        record_teams_diag({"event": "typingSent", "conversationId": conversation_id})
    except Exception as exc:
        record_teams_diag(
            {
                "event": "typingFailed",
                "conversationId": conversation_id,
                "type": exc.__class__.__name__,
                "message": str(exc),
            }
        )


async def send_message_reaction(ctx: ActivityContext, message_id: str | None, reaction_type: str) -> None:
    if not message_id:
        return
    try:
        await ctx.api.reactions.add(teams_conversation_id(ctx.activity), message_id, reaction_type)
        record_teams_diag({"event": "reactionSent", "method": "api.reactions.add", "messageId": message_id, "reactionType": reaction_type})
    except Exception as exc:
        record_teams_diag(
            {
                "event": "reactionSendFailed",
                "messageId": message_id,
                "reactionType": reaction_type,
                "type": exc.__class__.__name__,
                "message": str(exc),
            }
        )


async def delete_message_reaction(ctx: ActivityContext, message_id: str | None, reaction_type: str) -> None:
    if not message_id:
        return
    try:
        await ctx.api.reactions.delete(teams_conversation_id(ctx.activity), message_id, reaction_type)
        record_teams_diag({"event": "reactionDeleted", "method": "api.reactions.delete", "messageId": message_id, "reactionType": reaction_type})
    except Exception as exc:
        record_teams_diag(
            {
                "event": "reactionDeleteFailed",
                "messageId": message_id,
                "reactionType": reaction_type,
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


async def send_teams_response(ctx: ActivityContext, response: str, *, targeted_response: bool = False) -> str | None:
    if targeted_response and field_value(ctx.activity, "from_"):
        sent = await ctx.send(MessageActivityInput(text=response).with_recipient(ctx.activity.from_, is_targeted=True))
        record_teams_diag({"event": "responseSent", "method": "targeted"})
        return field_value(sent, "id")
    if teams_conversation_type(ctx.activity) != "personal":
        if should_quote_group_responses():
            sent = await ctx.reply(response)
            record_teams_diag({"event": "responseSent", "method": "reply", "conversationType": teams_conversation_type(ctx.activity)})
        else:
            sent = await ctx.send(response)
            record_teams_diag({"event": "responseSent", "method": "send", "conversationType": teams_conversation_type(ctx.activity)})
        return field_value(sent, "id")
    ctx.stream.emit(response)
    await ctx.stream.close()
    record_teams_diag({"event": "responseSent", "method": "stream", "conversationType": teams_conversation_type(ctx.activity)})
    return None


def supports_streaming_response(ctx: ActivityContext) -> bool:
    return teams_conversation_type(ctx.activity) == "personal"


async def run_agent_runtime_for_teams(
    ctx: ActivityContext,
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
    streamed = False

    async def emit_delta(delta: str) -> None:
        nonlocal streamed
        if not delta:
            return
        streamed = True
        ctx.stream.emit(delta)
        record_teams_diag({"event": "streamDeltaQueued", "conversationId": conversation_id, "length": len(delta)})

    try:
        record_teams_diag({"event": "backgroundStart", "conversationId": conversation_id})
        result = await invoke_agent_runtime(
            conversation_id=conversation_id,
            session_key=session_key,
            message=message,
            source=teams_runtime_source(ctx.activity),
            user_id=user_id(ctx.activity),
            must_answer=not suppress_no_response,
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
                remember_teams_event(memory_session_key, openclaw_memory_record(visible_response or _NO_RESPONSE))
        elif not streamed:
            sent_activity_id = await send_teams_response(ctx, visible_response, targeted_response=targeted_response)
            if memory_session_key:
                remember_teams_event(memory_session_key, openclaw_memory_record(visible_response, sent_activity_id))
        else:
            await ctx.stream.close()
            if memory_session_key:
                remember_teams_event(memory_session_key, openclaw_memory_record(visible_response or _NO_RESPONSE))
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
            if supports_streaming_response(ctx):
                ctx.stream.emit(f"OpenClaw could not complete this request: {exc}")
                await ctx.stream.close()
            else:
                await ctx.send(f"OpenClaw could not complete this request: {exc}")
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
        if progress_task:
            progress_task.cancel()
            await asyncio.gather(progress_task, return_exceptions=True)


async def send_stream_progress_updates(ctx: ActivityContext[MessageActivity], conversation_id: str, done: asyncio.Event) -> None:
    try:
        ctx.stream.update("Waking OpenClaw sandbox...")
        record_teams_diag({"event": "streamInformativeQueued", "conversationId": conversation_id, "message": "Waking OpenClaw sandbox..."})
        await asyncio.wait_for(done.wait(), timeout=int(env_optional("AUTOPILOT_TEAMS_PROGRESS_DELAY_SECONDS", "OPENCLAW_TEAMS_PROGRESS_DELAY_SECONDS", default="10")))
    except asyncio.TimeoutError:
        ctx.stream.update("OpenClaw is still working...")
        record_teams_diag({"event": "streamInformativeQueued", "conversationId": conversation_id, "message": "OpenClaw is still working..."})
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
    on_delta=None,
) -> AgentResponse:
    request = AgentRequest(
        prompt=message,
        conversation_id=session_key,
        user_id=user_id,
        source=source,
        must_answer=must_answer,
        context=context,
        metadata={"conversationId": conversation_id, **(metadata or {})},
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
