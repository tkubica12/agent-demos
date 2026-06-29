from __future__ import annotations

import base64
import json
import logging
import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import httpx
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from opentelemetry import trace
from opentelemetry.trace import Span


DEFAULT_FOUNDRY_AGENT_URL = "http://127.0.0.1:8088/invocations"
WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
SERVICE_NAME = "step-08-skill-system-baseline-bff"
_telemetry_configured = False

logging.basicConfig(level=os.getenv("LOG_LEVEL", "WARNING"))
logger = logging.getLogger(SERVICE_NAME)
logger.setLevel(os.getenv("APP_LOG_LEVEL", "INFO"))
tracer = trace.get_tracer(SERVICE_NAME)


@dataclass(frozen=True)
class UserContext:
    user_id: str
    tenant_id: str
    name: str | None
    auth_source: str


def config(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value else default


def create_credential() -> DefaultAzureCredential:
    running_in_azure = any(
        os.getenv(name)
        for name in ("CONTAINER_APP_NAME", "AZURE_CLIENT_ID", "MSI_ENDPOINT", "IDENTITY_ENDPOINT")
    )
    return DefaultAzureCredential(exclude_managed_identity_credential=not running_in_azure)


def log_event(event: str, **fields: Any) -> None:
    logger.info(
        json.dumps(
            {"event": event, "service": SERVICE_NAME, **fields},
            separators=(",", ":"),
            default=str,
        )
    )


def configure_telemetry() -> None:
    global _telemetry_configured
    if _telemetry_configured:
        return

    connection_string = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    project_endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT")
    if not connection_string and project_endpoint:
        try:
            project_client = AIProjectClient(
                endpoint=project_endpoint,
                credential=create_credential(),
            )
            connection_string = project_client.telemetry.get_application_insights_connection_string()
        except Exception as exc:
            log_event("telemetry.project_connection_unavailable", error=str(exc))

    if connection_string:
        os.environ["APPLICATIONINSIGHTS_CONNECTION_STRING"] = connection_string
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(
            connection_string=connection_string,
            service_name=SERVICE_NAME,
        )
        log_event("telemetry.azure_monitor_configured")
    else:
        log_event("telemetry.local_only")

    _telemetry_configured = True


@contextmanager
def span(name: str, correlation_id: str, **attributes: Any):
    with tracer.start_as_current_span(name) as current_span:
        current_span.set_attribute("service.name", SERVICE_NAME)
        current_span.set_attribute("correlation.id", correlation_id)
        for key, value in attributes.items():
            if value is not None:
                current_span.set_attribute(key, value)
        yield current_span


def set_span_result(current_span: Span, **attributes: Any) -> None:
    for key, value in attributes.items():
        if value is not None:
            current_span.set_attribute(key, value)


def sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, separators=(',', ':'))}\n\n"


def latest_user_text(messages: list[dict[str, Any]]) -> str | None:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content
    return None


def _decode_easy_auth_principal(header_value: str) -> dict[str, Any]:
    padded = header_value + "=" * (-len(header_value) % 4)
    decoded = base64.b64decode(padded).decode("utf-8")
    return json.loads(decoded)


def _claim(claims: list[dict[str, Any]], *names: str) -> str | None:
    for claim in claims:
        typ = claim.get("typ")
        if typ in names:
            val = claim.get("val")
            return val if isinstance(val, str) and val else None
    return None


def _authenticate_easy_auth(request: Request) -> UserContext:
    principal_header = request.headers.get("x-ms-client-principal")
    if not principal_header:
        raise HTTPException(status_code=401, detail="Missing Easy Auth principal.")
    try:
        principal = _decode_easy_auth_principal(principal_header)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid Easy Auth principal.") from exc

    claims = principal.get("claims")
    if not isinstance(claims, list):
        raise HTTPException(status_code=401, detail="Easy Auth principal has no claims.")

    oid = _claim(
        claims,
        "oid",
        "http://schemas.microsoft.com/identity/claims/objectidentifier",
    )
    tid = _claim(
        claims,
        "tid",
        "http://schemas.microsoft.com/identity/claims/tenantid",
    )
    name = _claim(claims, "name", "preferred_username", "emails")
    if not oid or not tid:
        raise HTTPException(status_code=401, detail="Easy Auth principal missing oid/tid.")
    return UserContext(user_id=f"{tid}_{oid}", tenant_id=tid, name=name, auth_source="easy-auth")


def _authenticate_jwt(request: Request) -> UserContext:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Missing bearer token.")

    tenant_id = config("BFF_ENTRA_TENANT_ID")
    audience = config("BFF_ENTRA_AUDIENCE")
    if not tenant_id or not audience:
        raise HTTPException(
            status_code=500,
            detail="BFF_ENTRA_TENANT_ID and BFF_ENTRA_AUDIENCE must be set for jwt auth.",
        )

    try:
        import jwt
        from jwt import PyJWKClient

        jwks = PyJWKClient(f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys")
        signing_key = jwks.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience,
            issuer=f"https://login.microsoftonline.com/{tenant_id}/v2.0",
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid bearer token.") from exc

    oid = payload.get("oid")
    tid = payload.get("tid", tenant_id)
    name = payload.get("preferred_username") or payload.get("name")
    if not isinstance(oid, str) or not isinstance(tid, str):
        raise HTTPException(status_code=401, detail="Bearer token missing oid/tid.")
    return UserContext(user_id=f"{tid}_{oid}", tenant_id=tid, name=name, auth_source="jwt")


def authenticate(request: Request) -> UserContext:
    mode = config("BFF_AUTH_MODE", "disabled")
    if mode == "disabled":
        return UserContext(
            user_id=config("BFF_LOCAL_USER_ID", "local-user") or "local-user",
            tenant_id=config("BFF_LOCAL_TENANT_ID", "local-tenant") or "local-tenant",
            name="Local Developer",
            auth_source="disabled",
        )
    if mode == "easy-auth":
        return _authenticate_easy_auth(request)
    if mode == "jwt":
        return _authenticate_jwt(request)
    raise HTTPException(status_code=500, detail=f"Unsupported BFF_AUTH_MODE: {mode}")


async def foundry_headers(user: UserContext, correlation_id: str) -> dict[str, str]:
    headers = {
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        "x-correlation-id": correlation_id,
        "x-user-id": user.user_id,
        "x-tenant-id": user.tenant_id,
        "x-memory-user-id": user.user_id,
    }
    bearer_token = config("BFF_FOUNDRY_BEARER_TOKEN")
    target = config("FOUNDRY_AGENT_INVOCATIONS_URL", DEFAULT_FOUNDRY_AGENT_URL) or ""
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    elif target.startswith("https://"):
        from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential

        credential = AsyncDefaultAzureCredential()
        try:
            token = await credential.get_token("https://ai.azure.com/.default")
        finally:
            await credential.close()
        headers["Authorization"] = f"Bearer {token.token}"
    return headers


async def call_agent_action(
    user: UserContext,
    correlation_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    target = config("FOUNDRY_AGENT_INVOCATIONS_URL", DEFAULT_FOUNDRY_AGENT_URL)
    if not target:
        raise HTTPException(status_code=500, detail="FOUNDRY_AGENT_INVOCATIONS_URL is empty.")
    headers = await foundry_headers(user, correlation_id)
    headers["Accept"] = "application/json"
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        response = await client.post(target, headers=headers, json=payload)
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return response.json()


def create_app() -> FastAPI:
    configure_telemetry()
    app = FastAPI(title="Step 08 skill system AG-UI gateway")
    origins = [origin.strip() for origin in (config("BFF_CORS_ORIGINS", "*") or "*").split(",")]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept", "x-correlation-id"],
    )

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(os.path.join(WEB_DIR, "index.html"))

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/profile")
    async def get_profile(request: Request) -> JSONResponse:
        user = authenticate(request)
        correlation_id = request.headers.get("x-correlation-id") or str(uuid.uuid4())
        result = await call_agent_action(
            user,
            correlation_id,
            {"action": "get_profile", "correlationId": correlation_id},
        )
        return JSONResponse(result)

    @app.patch("/api/profile")
    async def patch_profile(request: Request) -> JSONResponse:
        user = authenticate(request)
        data = await request.json()
        patch = data.get("patch")
        if not isinstance(patch, dict):
            raise HTTPException(status_code=400, detail="patch must be an object.")
        correlation_id = request.headers.get("x-correlation-id") or str(uuid.uuid4())
        result = await call_agent_action(
            user,
            correlation_id,
            {
                "action": "apply_profile_patch",
                "patch": patch,
                "source": "bff-api",
                "correlationId": correlation_id,
            },
        )
        return JSONResponse(result)

    @app.delete("/api/profile/{path:path}")
    async def delete_profile_item(path: str, request: Request) -> JSONResponse:
        user = authenticate(request)
        correlation_id = request.headers.get("x-correlation-id") or str(uuid.uuid4())
        result = await call_agent_action(
            user,
            correlation_id,
            {
                "action": "delete_profile_item",
                "path": path,
                "source": "bff-api",
                "correlationId": correlation_id,
            },
        )
        return JSONResponse(result)

    @app.get("/api/conversations")
    async def list_conversations(request: Request) -> JSONResponse:
        user = authenticate(request)
        correlation_id = request.headers.get("x-correlation-id") or str(uuid.uuid4())
        result = await call_agent_action(
            user,
            correlation_id,
            {"action": "list_conversations", "correlationId": correlation_id},
        )
        return JSONResponse(result)

    @app.get("/api/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str, request: Request) -> JSONResponse:
        user = authenticate(request)
        correlation_id = request.headers.get("x-correlation-id") or str(uuid.uuid4())
        result = await call_agent_action(
            user,
            correlation_id,
            {
                "action": "get_conversation",
                "conversationId": conversation_id,
                "correlationId": correlation_id,
            },
        )
        return JSONResponse(result)

    @app.post("/api/conversations/{conversation_id}/summarize")
    async def summarize_conversation(conversation_id: str, request: Request) -> JSONResponse:
        user = authenticate(request)
        correlation_id = request.headers.get("x-correlation-id") or str(uuid.uuid4())
        result = await call_agent_action(
            user,
            correlation_id,
            {
                "action": "summarize_conversation",
                "conversationId": conversation_id,
                "correlationId": correlation_id,
            },
        )
        return JSONResponse(result)

    @app.post("/api/memories/search")
    async def search_memories(request: Request) -> JSONResponse:
        user = authenticate(request)
        data = await request.json()
        correlation_id = request.headers.get("x-correlation-id") or str(uuid.uuid4())
        result = await call_agent_action(
            user,
            correlation_id,
            {
                "action": "search_memories",
                "query": data.get("query"),
                "limit": data.get("limit", 5),
                "correlationId": correlation_id,
            },
        )
        return JSONResponse(result)

    @app.get("/api/audit")
    async def list_audit(request: Request) -> JSONResponse:
        user = authenticate(request)
        correlation_id = request.headers.get("x-correlation-id") or str(uuid.uuid4())
        result = await call_agent_action(
            user,
            correlation_id,
            {"action": "list_audit", "correlationId": correlation_id},
        )
        return JSONResponse(result)

    @app.post("/agui")
    async def agui(request: Request) -> StreamingResponse:
        data = await request.json()
        correlation_id = request.headers.get("x-correlation-id") or data.get("correlationId")
        if not isinstance(correlation_id, str) or not correlation_id.strip():
            correlation_id = str(uuid.uuid4())

        with span("bff.request.received", correlation_id, path="/agui"):
            user = authenticate(request)
            log_event(
                "bff.request.received",
                correlation_id=correlation_id,
                user_id=user.user_id,
                auth_source=user.auth_source,
            )

        messages = data.get("messages")
        if not isinstance(messages, list):
            raise HTTPException(status_code=400, detail="AG-UI messages must be a list.")
        user_message = latest_user_text(messages)
        if user_message is None:
            raise HTTPException(status_code=400, detail="Missing user message in AG-UI messages.")

        thread_id = data.get("threadId")
        if not isinstance(thread_id, str) or not thread_id.strip():
            thread_id = str(uuid.uuid4())
        run_id = data.get("runId")
        if not isinstance(run_id, str) or not run_id.strip():
            run_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())
        target = config("FOUNDRY_AGENT_INVOCATIONS_URL", DEFAULT_FOUNDRY_AGENT_URL)
        if not target:
            raise HTTPException(status_code=500, detail="FOUNDRY_AGENT_INVOCATIONS_URL is empty.")

        async def stream() -> AsyncGenerator[str]:
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
                    "correlationId": correlation_id,
                }
            )

            assistant_text = ""
            try:
                with span(
                    "bff.foundry.invoke",
                    correlation_id,
                    thread_id=thread_id,
                    user_id=user.user_id,
                ) as invoke_span:
                    headers = await foundry_headers(user, correlation_id)
                    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
                        async with client.stream(
                            "POST",
                            target,
                            headers=headers,
                            json={
                                "message": user_message,
                                "stream": True,
                                "threadId": thread_id,
                                "runId": run_id,
                                "correlationId": correlation_id,
                                "user": {
                                    "id": user.user_id,
                                    "tenantId": user.tenant_id,
                                    "name": user.name,
                                    "authSource": user.auth_source,
                                },
                            },
                        ) as response:
                            set_span_result(invoke_span, foundry_status_code=response.status_code)
                            if response.status_code >= 400:
                                body = await response.aread()
                                raise RuntimeError(
                                    f"Foundry agent call failed: {response.status_code} "
                                    f"{body.decode('utf-8', errors='replace')}"
                                )
                            async for chunk in response.aiter_text():
                                if chunk:
                                    assistant_text += chunk
                                    yield sse(
                                        {
                                            "type": "TEXT_MESSAGE_CONTENT",
                                            "messageId": message_id,
                                            "delta": chunk,
                                            "correlationId": correlation_id,
                                        }
                                    )
                    set_span_result(invoke_span, response_length=len(assistant_text))
            except Exception as exc:
                log_event("bff.run_error", correlation_id=correlation_id, error=str(exc))
                yield sse(
                    {
                        "type": "RUN_ERROR",
                        "message": str(exc),
                        "correlationId": correlation_id,
                    }
                )
                return

            yield sse(
                {
                    "type": "TEXT_MESSAGE_END",
                    "messageId": message_id,
                    "correlationId": correlation_id,
                }
            )
            yield sse(
                {
                    "type": "RUN_FINISHED",
                    "threadId": thread_id,
                    "runId": run_id,
                    "correlationId": correlation_id,
                }
            )
            log_event(
                "bff.run_finished",
                correlation_id=correlation_id,
                thread_id=thread_id,
                run_id=run_id,
                response_length=len(assistant_text),
            )

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "x-correlation-id": correlation_id,
            },
        )

    app.mount("/web", StaticFiles(directory=WEB_DIR), name="web")
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    port = int(config("PORT", "8095") or "8095")
    uvicorn.run(app, host="127.0.0.1", port=port)
