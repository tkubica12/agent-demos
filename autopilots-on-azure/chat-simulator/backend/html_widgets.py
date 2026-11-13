"""Isolated, stored-message-backed MCP UI frames and synchronous Teams tool calls."""

import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from collections import deque
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from . import db, domain as d


MAX_TEXT_BYTES = 100_000
MAX_CALL_BYTES = 16_384
MAX_RESULT_BYTES = 65_536
FRAME_TTL = 60
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
FIELDS = {
    "type", "name", "html", "domain", "securityPolicy", "description", "toolInput",
    "toolOutput", "permissions", "mcpUrl", "correlationId",
}
POLICY_FIELDS = {"connectDomains", "resourceDomains", "frameDomains", "baseUriDomains"}
FRAME_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data:; connect-src 'none'; frame-src 'none'; object-src 'none'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'self'; sandbox allow-scripts"
)


def strict_json(raw):
    def reject_constant(_):
        raise ValueError("Non-finite JSON value")

    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    return json.loads(raw, parse_constant=reject_constant, object_pairs_hook=unique_keys)


def https_url(value):
    if not isinstance(value, str) or not value or len(value) > 2048 or any(
        ord(char) <= 32 or char in "\\'\";" for char in value
    ):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme == "https" and bool(parsed.hostname) and not (
            parsed.username or parsed.password
        ) and (parsed.port is None or 1 <= parsed.port <= 65535)
    except ValueError:
        return False


def valid_payload(value):
    if not isinstance(value, dict) or set(value) - FIELDS or value.get("type") != "widget/mcp-ui":
        return False
    if any(not isinstance(value.get(key), str) or not value[key].strip() or len(value[key]) > limit
           for key, limit in (("name", 200), ("html", MAX_TEXT_BYTES))):
        return False
    if not https_url(value.get("domain")):
        return False
    policy = value.get("securityPolicy", {})
    if not isinstance(policy, dict) or set(policy) - POLICY_FIELDS:
        return False
    for domains in policy.values():
        if not isinstance(domains, list) or len(domains) > 20 or not all(https_url(url) for url in domains):
            return False
    for key in ("description", "correlationId"):
        if key in value and (not isinstance(value[key], str) or len(value[key]) > 2048):
            return False
    if "mcpUrl" in value and not https_url(value["mcpUrl"]):
        return False
    return len(json.dumps(value, ensure_ascii=False).encode()) <= MAX_TEXT_BYTES


def widgets(activity):
    """Recognize only top-level complete code fences, not fences inside other code."""
    if activity.get("type") != "message" or activity.get("textFormat") != "extendedmarkdown":
        return {}
    text = activity.get("text", "")
    if not isinstance(text, str) or len(text.encode()) > MAX_TEXT_BYTES:
        return {}
    result, opened, lines, index = {}, None, [], -1
    for raw_line in text.split("\n"):
        line = raw_line.removesuffix("\r")
        match = FENCE.fullmatch(line)
        if opened is None:
            if match:
                marker, info = match.groups()
                candidate = marker[0] == "`" and info.strip() == "html-widget"
                if candidate:
                    index += 1
                opened = (marker, candidate, index)
                lines = []
        elif match and match[1][0] == opened[0][0] and len(match[1]) >= len(opened[0]) and not match[2].strip():
            if opened[1]:
                try:
                    payload = strict_json("\n".join(lines))
                    if valid_payload(payload):
                        result[opened[2]] = payload
                except (ValueError, UnicodeError, RecursionError):
                    pass  # Invalid fences remain ordinary markdown, never executable content.
            opened = None
        else:
            lines.append(line)
    return result


def validate_tool_result(value):
    if not isinstance(value, dict) or value.get("responseType") != "htmlwidget/calltoolresult":
        raise HTTPException(502, "Invalid htmlwidget/calltool response")
    result = value.get("callToolResult")
    if not isinstance(result, dict) or type(result.get("isError")) is not bool:
        raise HTTPException(502, "Invalid widget tool result")
    content = result.get("content")
    if not isinstance(content, list) or not 1 <= len(content) <= 20 or any(
        not isinstance(item, dict) or item.get("type") != "text" or not isinstance(item.get("text"), str)
        for item in content
    ) or ("structuredContent" in result and not isinstance(result["structuredContent"], dict)):
        raise HTTPException(502, "Invalid widget tool result content")
    return value


def register_widget_routes(app, channel, session_dependency, current_user):
    secret = secrets.token_bytes(32)
    grants, in_flight, rates = {}, set(), {}
    session_dep, user_dep = Depends(session_dependency), Depends(current_user)

    def target(session, message_id, index, user_id):
        message = d.require_message(session, message_id, user_id)
        if message.deleted:
            raise HTTPException(409, "Deleted messages cannot host widgets")
        agent = session.scalar(select(db.Agent).where(db.Agent.user_id == message.author_id, db.Agent.enabled.is_(True)))
        if agent is None:
            raise HTTPException(422, "Widgets require an enabled agent-authored message")
        conversation = d.require_conversation(session, message.conversation_id, agent.user_id)
        payload = widgets(message.activity).get(index)
        if payload is None:
            raise HTTPException(422, "Widget does not exist in the stored message")
        return message, agent, conversation, payload

    def cleanup():
        now = time.time()
        for nonce, grant in list(grants.items()):
            if grant["expires"] <= now:
                del grants[nonce]
        for key, entries in list(rates.items()):
            while entries and entries[0] <= now - 60:
                entries.popleft()
            if not entries:
                del rates[key]

    def limit(key, maximum):
        entries = rates.setdefault(key, deque())
        if len(entries) >= maximum:
            raise HTTPException(429, "Widget request rate limit reached")
        entries.append(time.time())

    @app.post("/api/messages/{message_id}/widgets/{index}/frame")
    async def mint_frame(message_id: str, index: int, user=user_dep, session=session_dep):
        _, _, _, payload = target(session, message_id, index, user.id)
        cleanup()
        limit(("frames", user.id), 60)
        if len(grants) >= 2000:
            raise HTTPException(503, "Widget frame capacity reached")
        nonce = secrets.token_urlsafe(24)
        grant = {"message": message_id, "index": index, "user": user.id,
                 "digest": hashlib.sha256(payload["html"].encode()).hexdigest(),
                 "expires": int(time.time()) + FRAME_TTL}
        grants[nonce] = grant
        signature = base64.urlsafe_b64encode(hmac.digest(secret, nonce.encode(), "sha256")).decode().rstrip("=")
        return {"url": f"/api/widget-frames/{nonce}.{signature}", "expiresAt": grant["expires"]}

    @app.get("/api/widget-frames/{capability}")
    async def frame(capability: str, request: Request, session=session_dep):
        if request.headers.get("sec-fetch-dest") != "iframe" or request.headers.get("sec-fetch-site") != "same-origin":
            raise HTTPException(403, "Widget frames require same-origin iframe navigation")
        nonce, _, signature = capability.partition(".")
        expected = base64.urlsafe_b64encode(hmac.digest(secret, nonce.encode(), "sha256")).decode().rstrip("=")
        cleanup()
        grant = grants.get(nonce)
        if not grant or len(capability) > 128 or not hmac.compare_digest(signature, expected):
            raise HTTPException(403, "Widget frame capability is invalid or expired")
        _, _, _, payload = target(session, grant["message"], grant["index"], grant["user"])
        if hashlib.sha256(payload["html"].encode()).hexdigest() != grant["digest"]:
            raise HTTPException(409, "Widget content changed")
        del grants[nonce]
        return HTMLResponse(payload["html"], headers={
            "Content-Security-Policy": FRAME_CSP, "X-Frame-Options": "SAMEORIGIN",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=(), clipboard-read=(), clipboard-write=()",
            "X-DNS-Prefetch-Control": "off",
        })

    @app.post("/api/messages/{message_id}/widgets/{index}/tools")
    async def call_tool(message_id: str, index: int, request: Request, user=user_dep, session=session_dep):
        from .channel import bounded_json

        body = await bounded_json(request, MAX_CALL_BYTES)
        try:
            strict_json(json.dumps(body))
        except (ValueError, RecursionError):
            raise HTTPException(422, "Widget arguments must be finite JSON") from None
        if set(body) != {"name", "arguments"} or body.get("name") != "autopilot.widget.submit" or not isinstance(
            body.get("arguments"), dict
        ):
            raise HTTPException(422, "Unsupported widget tool or invalid arguments")
        message, agent, conversation, payload = target(session, message_id, index, user.id)
        cleanup()
        limit(("tools", user.id), 30)
        limit(("widget", message_id, index), 6)
        key = (message_id, index)
        if key in in_flight:
            raise HTTPException(409, "A widget tool call is already pending")
        in_flight.add(key)
        delivery_id = None
        try:
            arguments = {**body["arguments"], "__msteams__": {
                field: payload[field] for field in ("mcpUrl", "correlationId") if field in payload
            }}
            root = (message.reply_to_id or message.id) if conversation.kind == "channel" else None
            activity = d.make_activity(session, channel.settings, conversation, user, db.new_id(), "", "plain", root)
            activity.update({"type": "invoke", "name": "htmlwidget/calltool", "replyToId": message.id,
                             "value": {"name": body["name"], "arguments": arguments},
                             "recipient": {"id": f"28:{agent.client_id}"}})
            ids = channel.queue(session, conversation, user, activity, [agent])
            delivery_id = ids[0]
            async with asyncio.timeout(15):
                return await channel.deliver(session, ids[0])
        except TimeoutError:
            session.rollback()
            if delivery_id:
                delivery = session.get(db.AgentDelivery, delivery_id)
                delivery.status = "failed"
                delivery.error = "Widget tool timed out; gateway outcome unknown. Retry the same capability."
                session.commit()
            raise HTTPException(504, "Widget tool call timed out; gateway outcome unknown. Retry the same selection.") from None
        finally:
            in_flight.discard(key)
