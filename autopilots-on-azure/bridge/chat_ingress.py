"""Entra admission for a custom chat front end on the gateway messaging endpoint.

A Worker with ``CHAT_CLIENT_IDS`` accepts ``POST /api/messages`` only with a delegated user
token that the chat application obtained for the Worker's blueprint. The sender of the
activity is replaced by the identity in that token, and replies may only go to the
configured chat service.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit

import jwt
from starlette.responses import JSONResponse


MESSAGES_PATH = "/api/messages"
DEFAULT_SCOPE = "access_agent"


class ChatIngressError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value and value != "not-configured":
            return value
    return ""


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class ChatIngressSettings:
    client_ids: frozenset[str]
    service_urls: tuple[str, ...]
    tenant_id: str
    audience: str
    scope: str

    @classmethod
    def from_environment(cls) -> "ChatIngressSettings":
        return cls(
            client_ids=frozenset(item.lower() for item in _csv(_env("CHAT_CLIENT_IDS"))),
            service_urls=tuple(item.rstrip("/") for item in _csv(_env("CHAT_SERVICE_URLS"))),
            tenant_id=_env("AGENT365_TENANT_ID", "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID"),
            audience=_env("AGENT365_BLUEPRINT_CLIENT_ID", "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID"),
            scope=_env("CHAT_REQUIRED_SCOPE") or DEFAULT_SCOPE,
        )

    @property
    def enabled(self) -> bool:
        return bool(self.client_ids)

    def validate(self) -> None:
        try:
            uuid.UUID(self.tenant_id)
            uuid.UUID(self.audience)
        except ValueError as exc:
            raise ChatIngressError(503, "Chat admission is not configured.") from exc
        if not self.service_urls or any(urlsplit(url).scheme != "https" for url in self.service_urls):
            raise ChatIngressError(503, "Chat admission is not configured.")


@dataclass(frozen=True)
class ChatUser:
    object_id: str
    tenant_id: str
    name: str


@lru_cache(maxsize=4)
def _signing_keys(tenant_id: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(
        f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys",
        timeout=10,
    )


def verify_chat_token(token: str, settings: ChatIngressSettings) -> ChatUser:
    tenant = settings.tenant_id.lower()
    try:
        key = _signing_keys(tenant).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            audience=[settings.audience, f"api://{settings.audience}"],
            options={"require": ["exp", "iat", "nbf", "iss", "aud", "tid"]},
        )
    except jwt.PyJWKClientConnectionError as exc:
        raise ChatIngressError(503, "Entra signing keys are unavailable.") from exc
    except jwt.PyJWTError as exc:
        raise ChatIngressError(401, "A valid Entra chat access token is required.") from exc
    issuers = {f"https://login.microsoftonline.com/{tenant}/v2.0", f"https://sts.windows.net/{tenant}/"}
    if str(claims["iss"]).lower() not in issuers or str(claims["tid"]).lower() != tenant:
        raise ChatIngressError(401, "A valid Entra chat access token is required.")
    scopes = claims.get("scp")
    client_id = str(claims.get("azp") or claims.get("appid") or "").lower()
    object_id = str(claims.get("oid") or "")
    if (
        claims.get("idtyp") == "app"
        or not isinstance(scopes, str)
        or settings.scope not in scopes.split()
        or client_id not in settings.client_ids
        or not object_id
    ):
        raise ChatIngressError(403, "This token may not message the Worker.")
    return ChatUser(object_id=object_id, tenant_id=tenant, name=str(claims.get("name") or ""))


def require_chat_service_url(value: object, settings: ChatIngressSettings) -> None:
    url = str(value or "").rstrip("/")
    if not any(url == allowed or url.startswith(allowed + "/") for allowed in settings.service_urls):
        raise ChatIngressError(403, "Replies may only go to the configured chat service.")


def bind_activity(payload: dict, user: ChatUser) -> dict:
    sender = payload.get("from")
    name = user.name or (sender.get("name") if isinstance(sender, dict) else "") or ""
    channel_data = payload.get("channelData")
    channel_data = dict(channel_data) if isinstance(channel_data, dict) else {}
    channel_data["tenant"] = {"id": user.tenant_id}
    return {
        **payload,
        "from": {"id": user.object_id, "aadObjectId": user.object_id, "name": name},
        "channelData": channel_data,
    }


async def _read_body(receive) -> bytes:
    body = b""
    while True:
        message = await receive()
        if message["type"] != "http.request":
            raise ChatIngressError(400, "The request was interrupted.")
        body += message.get("body", b"")
        if not message.get("more_body"):
            return body


class ChatIngressMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != MESSAGES_PATH or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        settings = ChatIngressSettings.from_environment()
        if not settings.enabled:
            await self.app(scope, receive, send)
            return
        try:
            settings.validate()
            headers = dict(scope.get("headers") or [])
            scheme, _, token = headers.get(b"authorization", b"").decode("latin-1").partition(" ")
            if scheme.lower() != "bearer" or not token.strip():
                raise ChatIngressError(401, "A valid Entra chat access token is required.")
            user = await asyncio.to_thread(verify_chat_token, token.strip(), settings)
            try:
                payload = json.loads(await _read_body(receive))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ChatIngressError(400, "The activity must be a JSON object.") from exc
            if not isinstance(payload, dict):
                raise ChatIngressError(400, "The activity must be a JSON object.")
            require_chat_service_url(payload.get("serviceUrl"), settings)
        except ChatIngressError as exc:
            await JSONResponse({"detail": exc.detail}, status_code=exc.status)(scope, receive, send)
            return
        body = json.dumps(bind_activity(payload, user), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = [(name, value) for name, value in scope.get("headers") or [] if name != b"content-length"]
        headers.append((b"content-length", str(len(body)).encode()))
        delivered = False

        async def replay():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app({**scope, "headers": headers}, replay, send)
