from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from typing import Any

from azure.identity import DefaultAzureCredential
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from microsoft_teams.api import MessageActivity
from microsoft_teams.apps import ActivityContext, App, FastAPIAdapter
from pydantic import BaseModel, Field

from bridge.gateway_client import OpenClawGatewayClient, gateway_http_url_to_ws
from scripts.sandbox_gateway import GatewaySandboxConfig, ensure_gateway_sandbox


def configured_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name, "").strip()
        if value and value != "not-configured":
            return value
    return None


app = FastAPI(title="OpenClaw Azure Container Apps bridge")
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


_sandbox_lock = asyncio.Lock()
_teams_diag: deque[dict[str, Any]] = deque(maxlen=20)


def record_teams_diag(event: dict[str, Any]) -> None:
    _teams_diag.appendleft({"ts": time.time(), **event})


def env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or value == "not-configured":
        raise RuntimeError(f"{name} is required.")
    return value


def env_optional(name: str, default: str = "") -> str:
    value = os.getenv(name, "").strip()
    if value == "not-configured":
        return default
    return value or default


def azure_credential():
    return DefaultAzureCredential()


@app.on_event("startup")
async def initialize_teams_app() -> None:
    await teams_app.initialize()


def sandbox_config() -> GatewaySandboxConfig:
    return GatewaySandboxConfig(
        subscription_id=env_required("AZURE_SUBSCRIPTION_ID"),
        resource_group=env_required("AZURE_RESOURCE_GROUP"),
        sandbox_group=env_required("AZURE_SANDBOX_GROUP"),
        region=env_required("AZURE_REGION"),
        foundry_openai_base_url=env_required("FOUNDRY_OPENAI_BASE_URL"),
        model_deployment=env_optional("OPENCLAW_MODEL_ID", "gpt-5-4-mini"),
        image_name=env_required("OPENCLAW_IMAGE"),
        customer_vnet_connection_name=env_optional("SANDBOX_VNET_CONNECTION_NAME"),
        private_incidents_mcp_url=env_optional("PRIVATE_INCIDENTS_MCP_URL"),
        private_incidents_mcp_static_key=env_optional("PRIVATE_INCIDENTS_MCP_STATIC_KEY", "demo-static-key"),
        registry_username=env_optional("OPENCLAW_REGISTRY_USERNAME"),
        registry_password=env_optional("OPENCLAW_REGISTRY_PASSWORD"),
        acr_name=env_optional("ACR_NAME"),
        disk_image_id=env_optional("OPENCLAW_DISK_IMAGE_ID"),
        disk_image_name=env_optional("OPENCLAW_DISK_IMAGE_NAME", "openclaw-gateway-image-with-private-mcp"),
        data_volume_name=env_optional("OPENCLAW_DATA_VOLUME_NAME", "openclaw-data"),
        data_volume_size=env_optional("OPENCLAW_DATA_VOLUME_SIZE", "20Gi"),
        cpu=env_optional("OPENCLAW_SANDBOX_CPU", "2000m"),
        memory=env_optional("OPENCLAW_SANDBOX_MEMORY", "2048Mi"),
        root_disk_size=env_optional("OPENCLAW_SANDBOX_ROOT_DISK_SIZE", "20Gi"),
        gateway_token=env_required("OPENCLAW_GATEWAY_TOKEN"),
    )


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
        return await invoke_openclaw(
            conversation_id=request.conversation_id,
            session_key=f"bridge:{request.conversation_id}",
            message=request.message,
        )
    except Exception as exc:
        detail = {"message": str(exc)}
        if os.getenv("OPENCLAW_BRIDGE_DEBUG", "").lower() in {"1", "true", "yes"}:
            detail["type"] = exc.__class__.__name__
        raise HTTPException(status_code=500, detail=detail) from exc


@teams_app.on_message
async def handle_teams_message(ctx: ActivityContext[MessageActivity]) -> None:
    conversation = ctx.activity.conversation
    record_teams_diag(
        {
            "event": "handler",
            "activityId": ctx.activity.id,
            "conversationType": conversation.conversation_type,
            "conversationId": conversation.id,
            "textLength": len(ctx.activity.text or ""),
        }
    )
    if conversation.conversation_type != "personal":
        await ctx.send("OpenClaw Teams support is currently enabled for 1:1 chats only.")
        return

    message = (ctx.activity.text or "").strip()
    if not message:
        await ctx.send("Send a text prompt for OpenClaw.")
        return

    asyncio.create_task(run_openclaw_for_teams(ctx, conversation.id, message))


async def run_openclaw_for_teams(ctx: ActivityContext[MessageActivity], conversation_id: str, message: str) -> None:
    done = asyncio.Event()
    progress_task = asyncio.create_task(send_stream_progress_updates(ctx, conversation_id, done))
    try:
        record_teams_diag({"event": "backgroundStart", "conversationId": conversation_id})
        result = await invoke_openclaw(
            conversation_id=conversation_id,
            session_key=f"teams:{conversation_id}",
            message=message,
        )
        ctx.stream.emit(result.response)
        await ctx.stream.close()
        record_teams_diag({"event": "streamFinalSent", "conversationId": conversation_id})
    except Exception as exc:
        record_teams_diag(
            {
                "event": "backgroundException",
                "conversationId": conversation_id,
                "type": exc.__class__.__name__,
                "message": str(exc),
            }
        )
        try:
            ctx.stream.emit(f"OpenClaw could not complete this request: {exc}")
            await ctx.stream.close()
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
        progress_task.cancel()
        await asyncio.gather(progress_task, return_exceptions=True)


async def send_stream_progress_updates(ctx: ActivityContext[MessageActivity], conversation_id: str, done: asyncio.Event) -> None:
    try:
        ctx.stream.update("Waking OpenClaw sandbox...")
        record_teams_diag({"event": "streamInformativeQueued", "conversationId": conversation_id, "message": "Waking OpenClaw sandbox..."})
        await asyncio.wait_for(done.wait(), timeout=int(env_optional("OPENCLAW_TEAMS_PROGRESS_DELAY_SECONDS", "10")))
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


async def invoke_openclaw(*, conversation_id: str, session_key: str, message: str) -> InvokeResponse:
    config = sandbox_config()
    credential = azure_credential()
    async with _sandbox_lock:
        sandbox = await asyncio.to_thread(ensure_gateway_sandbox, config, credential=credential)
    if not sandbox.gateway_url:
        raise RuntimeError(f"Sandbox {sandbox.sandbox_id} does not expose the OpenClaw Gateway port.")

    ws_url = gateway_http_url_to_ws(sandbox.gateway_url)
    gateway = OpenClawGatewayClient(
        url=ws_url,
        token=config.gateway_token,
        timeout_seconds=int(env_optional("OPENCLAW_BRIDGE_GATEWAY_TIMEOUT_SECONDS", "600")),
        device_token=env_optional("OPENCLAW_BRIDGE_DEVICE_TOKEN") or None,
        device_private_key_pem=env_optional("OPENCLAW_BRIDGE_DEVICE_PRIVATE_KEY_PEM") or None,
    )
    result = await gateway.invoke_agent(
        message=message,
        session_key=session_key,
        agent_id=env_optional("OPENCLAW_BRIDGE_AGENT_ID") or None,
    )
    return InvokeResponse(
        conversationId=conversation_id,
        sandboxId=sandbox.sandbox_id,
        gatewayUrl=sandbox.gateway_url,
        reusedExistingSandbox=sandbox.reused_existing_sandbox,
        response=result,
    )
