from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import jwt
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from jwt import PyJWKClient
from opentelemetry import propagate, trace

from feedback import record_feedback


WEB_DIR = Path(__file__).with_name("web")
FOUNDRY_SCOPE = "https://ai.azure.com/.default"
tracer = trace.get_tracer("foundry-showcase-agui-bff")


@dataclass(frozen=True)
class UserContext:
    object_id: str
    tenant_id: str
    name: str | None
    source: str

    @property
    def user_id(self) -> str:
        return f"{self.tenant_id}_{self.object_id}"


def required_config(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required configuration: {name}")
    return value


def sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, separators=(',', ':'))}\n\n"


def latest_user_text(messages: Any) -> str:
    if not isinstance(messages, list):
        raise HTTPException(status_code=400, detail="AG-UI messages must be a list.")
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    raise HTTPException(status_code=400, detail="Missing user message in AG-UI messages.")


@lru_cache(maxsize=4)
def jwks_client(tenant_id: str) -> PyJWKClient:
    return PyJWKClient(
        f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
    )


def decode_bearer_token(token: str) -> dict[str, Any]:
    tenant_id = required_config("BFF_ENTRA_TENANT_ID")
    audience = required_config("BFF_ENTRA_AUDIENCE")
    signing_key = jwks_client(tenant_id).get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=audience,
        issuer=f"https://login.microsoftonline.com/{tenant_id}/v2.0",
    )


def authenticate(request: Request) -> tuple[UserContext, str | None]:
    if os.getenv("BFF_AUTH_MODE", "jwt") == "disabled":
        return (
            UserContext(
                object_id=os.getenv("BFF_LOCAL_OBJECT_ID", "local-user"),
                tenant_id=os.getenv("BFF_LOCAL_TENANT_ID", "local-tenant"),
                name="Local Developer",
                source="disabled",
            ),
            None,
        )

    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    try:
        payload = decode_bearer_token(token)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid bearer token.") from exc

    object_id = payload.get("oid")
    tenant_id = payload.get("tid")
    if not isinstance(object_id, str) or not isinstance(tenant_id, str):
        raise HTTPException(status_code=401, detail="Bearer token missing oid/tid.")
    required_scope = os.getenv("BFF_REQUIRED_SCOPE", "Agui.Access")
    scopes = str(payload.get("scp", "")).split()
    if required_scope not in scopes:
        raise HTTPException(status_code=403, detail="Bearer token missing required scope.")
    name = payload.get("preferred_username") or payload.get("name")
    return (
        UserContext(
            object_id=object_id,
            tenant_id=tenant_id,
            name=name if isinstance(name, str) else None,
            source="entra-jwt",
        ),
        token,
    )


async def foundry_headers(
    user: UserContext,
    correlation_id: str,
    user_assertion: str | None,
) -> dict[str, str]:
    def acquire_token():
        from azure.identity import (
            DefaultAzureCredential,
            ManagedIdentityCredential,
            OnBehalfOfCredential,
        )

        if user_assertion is None:
            credential = DefaultAzureCredential(
                managed_identity_client_id=os.getenv("AZURE_CLIENT_ID")
            )
            try:
                return credential.get_token(FOUNDRY_SCOPE)
            finally:
                credential.close()

        managed_identity = ManagedIdentityCredential(
            client_id=required_config("AZURE_CLIENT_ID")
        )
        credential = OnBehalfOfCredential(
            tenant_id=required_config("BFF_ENTRA_TENANT_ID"),
            client_id=required_config("BFF_ENTRA_CLIENT_ID"),
            client_assertion_func=lambda: managed_identity.get_token(
                "api://AzureADTokenExchange/.default"
            ).token,
            user_assertion=user_assertion,
        )
        try:
            return credential.get_token(FOUNDRY_SCOPE)
        finally:
            credential.close()
            managed_identity.close()

    access_token = await asyncio.to_thread(acquire_token)
    headers = {
        "Accept": "text/event-stream",
        "Authorization": f"Bearer {access_token.token}",
        "Content-Type": "application/json",
        "x-correlation-id": correlation_id,
        "x-memory-user-id": user.user_id,
        "x-tenant-id": user.tenant_id,
        "x-user-id": user.user_id,
    }
    propagate.inject(headers)
    return headers


SERVICE_NAME = "foundry-showcase-agui-bff"


def configure_telemetry() -> None:
    connection_string = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not connection_string:
        return
    from azure.monitor.opentelemetry import configure_azure_monitor
    from opentelemetry.sdk.resources import SERVICE_NAME as SERVICE_NAME_KEY
    from opentelemetry.sdk.resources import Resource

    configure_azure_monitor(
        connection_string=connection_string,
        resource=Resource.create({SERVICE_NAME_KEY: SERVICE_NAME}),
    )


def create_app(
    client_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> FastAPI:
    configure_telemetry()
    app = FastAPI(title="Foundry Showcase AG-UI BFF")
    app.state.client_factory = client_factory or (
        lambda: httpx.AsyncClient(timeout=httpx.Timeout(180.0))
    )
    origins = [
        origin.strip()
        for origin in os.getenv("BFF_CORS_ORIGINS", "").split(",")
        if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Accept",
            "traceparent",
            "x-correlation-id",
        ],
    )

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/vendor/msal-browser.min.js")
    async def msal_browser() -> FileResponse:
        return FileResponse(
            WEB_DIR / "vendor" / "msal-browser.min.js",
            media_type="text/javascript",
        )

    @app.get("/config")
    async def public_config() -> dict[str, str]:
        return {
            "clientId": required_config("BFF_ENTRA_CLIENT_ID"),
            "tenantId": required_config("BFF_ENTRA_TENANT_ID"),
            "scope": required_config("BFF_ENTRA_SCOPE"),
        }

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/agui")
    async def agui(request: Request) -> StreamingResponse:
        user, user_assertion = authenticate(request)
        payload = await request.json()
        message = latest_user_text(payload.get("messages"))
        thread_id = payload.get("threadId")
        if not isinstance(thread_id, str) or not thread_id.strip():
            thread_id = str(uuid.uuid4())
        run_id = payload.get("runId")
        if not isinstance(run_id, str) or not run_id.strip():
            run_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())
        correlation_id = request.headers.get("x-correlation-id") or str(uuid.uuid4())
        target = required_config("FOUNDRY_AGENT_INVOCATIONS_URL")

        async def stream() -> AsyncGenerator[str]:
            trace_id = ""
            span_id = ""
            yield sse(
                {
                    "type": "RUN_STARTED",
                    "threadId": thread_id,
                    "runId": run_id,
                    "correlationId": correlation_id,
                }
            )
            yield sse(
                {
                    "type": "TEXT_MESSAGE_START",
                    "messageId": message_id,
                    "role": "assistant",
                }
            )
            try:
                headers = await foundry_headers(user, correlation_id, user_assertion)
                with tracer.start_as_current_span("agui.foundry_invocation") as current:
                    current.set_attribute("correlation.id", correlation_id)
                    current.set_attribute("user.tenant_id", user.tenant_id)
                    span_context = current.get_span_context()
                    if span_context.is_valid:
                        trace_id = format(span_context.trace_id, "032x")
                        span_id = format(span_context.span_id, "016x")
                    async with app.state.client_factory() as client:
                        response = await client.post(
                            target,
                            headers=headers,
                            json={
                                "message": message,
                                "stream": False,
                                "threadId": thread_id,
                                "runId": run_id,
                                "user": {
                                    "id": user.user_id,
                                    "tenantId": user.tenant_id,
                                    "name": user.name,
                                    "authSource": user.source,
                                },
                            },
                        )
                        if response.status_code >= 400:
                            raise RuntimeError(
                                f"Foundry invocation failed with status "
                                f"{response.status_code}: {response.text}"
                            )
                        foundry_payload = response.json()
                        assistant_text = foundry_payload.get("response")
                        if not isinstance(assistant_text, str):
                            raise RuntimeError(
                                "Foundry invocation response is missing text."
                            )
                        yield sse(
                            {
                                "type": "TEXT_MESSAGE_CONTENT",
                                "messageId": message_id,
                                "delta": assistant_text,
                            }
                        )
            except Exception as exc:
                yield sse(
                    {
                        "type": "RUN_ERROR",
                        "message": str(exc),
                        "correlationId": correlation_id,
                    }
                )
                return
            yield sse({"type": "TEXT_MESSAGE_END", "messageId": message_id})
            yield sse(
                {
                    "type": "RUN_FINISHED",
                    "threadId": thread_id,
                    "runId": run_id,
                    "correlationId": correlation_id,
                    "traceId": trace_id,
                    "spanId": span_id,
                }
            )

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    @app.post("/feedback")
    async def feedback(request: Request) -> dict[str, Any]:
        user, _ = authenticate(request)
        payload = await request.json()
        if not isinstance(payload.get("passed"), bool):
            raise HTTPException(status_code=400, detail="passed must be a boolean.")
        comment = payload.get("comment")
        if comment is not None and not isinstance(comment, str):
            raise HTTPException(status_code=400, detail="comment must be a string.")
        try:
            return record_feedback(
                trace_id=payload.get("traceId"),
                span_id=payload.get("spanId"),
                passed=payload["passed"],
                comment=(comment or "").strip() or None,
                reviewer=user.name,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app


app = create_app()
