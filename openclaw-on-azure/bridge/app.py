from __future__ import annotations

import asyncio
import os

from azure.identity import ClientSecretCredential, DefaultAzureCredential
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from bridge.gateway_client import OpenClawGatewayClient, gateway_http_url_to_ws
from scripts.sandbox_gateway import GatewaySandboxConfig, ensure_gateway_sandbox


app = FastAPI(title="OpenClaw ACA Express bridge")


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
    tenant_id = env_optional("AZURE_TENANT_ID")
    client_id = env_optional("OPENCLAW_BRIDGE_AZURE_CLIENT_ID")
    client_secret = env_optional("OPENCLAW_BRIDGE_AZURE_CLIENT_SECRET")
    if tenant_id and client_id and client_secret:
        return ClientSecretCredential(tenant_id=tenant_id, client_id=client_id, client_secret=client_secret)
    return DefaultAzureCredential()


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


@app.post("/invoke", response_model=InvokeResponse, response_model_by_alias=True)
async def invoke(request: InvokeRequest) -> InvokeResponse:
    try:
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
            message=request.message,
            session_key=f"bridge:{request.conversation_id}",
            agent_id=env_optional("OPENCLAW_BRIDGE_AGENT_ID") or None,
        )
        return InvokeResponse(
            conversationId=request.conversation_id,
            sandboxId=sandbox.sandbox_id,
            gatewayUrl=sandbox.gateway_url,
            reusedExistingSandbox=sandbox.reused_existing_sandbox,
            response=result,
        )
    except Exception as exc:
        detail = {"message": str(exc)}
        if os.getenv("OPENCLAW_BRIDGE_DEBUG", "").lower() in {"1", "true", "yes"}:
            detail["type"] = exc.__class__.__name__
        raise HTTPException(status_code=500, detail=detail) from exc
