from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any

from azure.core.credentials import TokenCredential
from azure.identity import DefaultAzureCredential

from bridge.gateway_client import OpenClawGatewayClient, gateway_http_url_to_ws
from bridge.runtime.base import AgentRequest, AgentResponse
from scripts.sandbox_runtime import GatewaySandboxConfig, ensure_gateway_sandbox


def _configured_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name, "").strip()
        if value and value != "not-configured":
            return value
    return None


def _env_required(*names: str) -> str:
    value = _configured_env(*names)
    if not value:
        raise RuntimeError(f"{' or '.join(names)} is required.")
    return value


def _env_optional(*names: str, default: str = "") -> str:
    value = _configured_env(*names)
    return value if value is not None else default


def openclaw_sandbox_config_from_env() -> GatewaySandboxConfig:
    return GatewaySandboxConfig(
        subscription_id=_env_required("AZURE_SUBSCRIPTION_ID"),
        resource_group=_env_required("AZURE_RESOURCE_GROUP"),
        sandbox_group=_env_required("AZURE_SANDBOX_GROUP"),
        region=_env_required("AZURE_REGION"),
        foundry_openai_base_url=_env_required("FOUNDRY_OPENAI_BASE_URL"),
        model_deployment=_env_optional("OPENCLAW_MODEL_ID", default="gpt-5-4-mini"),
        image_name=_env_required("AGENT_RUNTIME_IMAGE", "OPENCLAW_IMAGE"),
        customer_vnet_connection_name=_env_optional("SANDBOX_VNET_CONNECTION_NAME"),
        private_incidents_mcp_url=_env_optional("PRIVATE_INCIDENTS_MCP_URL"),
        private_incidents_mcp_static_key=_env_optional("PRIVATE_INCIDENTS_MCP_STATIC_KEY", default="demo-static-key"),
        registry_username=_env_optional("AGENT_RUNTIME_REGISTRY_USERNAME", "OPENCLAW_REGISTRY_USERNAME"),
        registry_password=_env_optional("AGENT_RUNTIME_REGISTRY_PASSWORD", "OPENCLAW_REGISTRY_PASSWORD"),
        acr_name=_env_optional("ACR_NAME"),
        disk_image_id=_env_optional("AGENT_RUNTIME_DISK_IMAGE_ID", "OPENCLAW_DISK_IMAGE_ID"),
        disk_image_name=_env_optional(
            "AGENT_RUNTIME_DISK_IMAGE_NAME",
            "OPENCLAW_DISK_IMAGE_NAME",
            default="openclaw-gateway-image-with-private-mcp",
        ),
        data_volume_name=_env_optional("AGENT_RUNTIME_DATA_VOLUME_NAME", "OPENCLAW_DATA_VOLUME_NAME", default="openclaw-data"),
        data_volume_size=_env_optional("AGENT_RUNTIME_DATA_VOLUME_SIZE", "OPENCLAW_DATA_VOLUME_SIZE", default="20Gi"),
        cpu=_env_optional("AGENT_RUNTIME_SANDBOX_CPU", "OPENCLAW_SANDBOX_CPU", default="2000m"),
        memory=_env_optional("AGENT_RUNTIME_SANDBOX_MEMORY", "OPENCLAW_SANDBOX_MEMORY", default="2048Mi"),
        root_disk_size=_env_optional("AGENT_RUNTIME_SANDBOX_ROOT_DISK_SIZE", "OPENCLAW_SANDBOX_ROOT_DISK_SIZE", default="20Gi"),
        gateway_token=_env_required("OPENCLAW_GATEWAY_TOKEN"),
    )


class OpenClawRuntimeAdapter:
    def __init__(
        self,
        *,
        sandbox_lock: asyncio.Lock | None = None,
        credential_factory: Callable[[], TokenCredential] = DefaultAzureCredential,
        sandbox_config_factory: Callable[[], GatewaySandboxConfig] = openclaw_sandbox_config_from_env,
        ensure_sandbox: Callable[..., Any] = ensure_gateway_sandbox,
    ) -> None:
        self._sandbox_lock = sandbox_lock or asyncio.Lock()
        self._credential_factory = credential_factory
        self._sandbox_config_factory = sandbox_config_factory
        self._ensure_sandbox = ensure_sandbox

    @property
    def runtime_kind(self) -> str:
        return "openclaw"

    async def invoke(self, request: AgentRequest) -> AgentResponse:
        config = self._sandbox_config_factory()
        credential = self._credential_factory()
        async with self._sandbox_lock:
            sandbox = await asyncio.to_thread(self._ensure_sandbox, config, credential=credential)
        if not sandbox.gateway_url:
            raise RuntimeError(f"Sandbox {sandbox.sandbox_id} does not expose the OpenClaw Gateway port.")

        gateway = OpenClawGatewayClient(
            url=gateway_http_url_to_ws(sandbox.gateway_url),
            token=config.gateway_token,
            timeout_seconds=int(_env_optional("OPENCLAW_BRIDGE_GATEWAY_TIMEOUT_SECONDS", default="600")),
            device_token=_env_optional("OPENCLAW_BRIDGE_DEVICE_TOKEN") or None,
            device_private_key_pem=_env_optional("OPENCLAW_BRIDGE_DEVICE_PRIVATE_KEY_PEM") or None,
        )
        agent_id = _env_optional("OPENCLAW_BRIDGE_AGENT_ID") or None
        if request.on_delta:
            result = await gateway.invoke_agent_streaming(
                message=request.prompt,
                session_key=request.conversation_id,
                agent_id=agent_id,
                on_delta=request.on_delta,
            )
        else:
            result = await gateway.invoke_agent(
                message=request.prompt,
                session_key=request.conversation_id,
                agent_id=agent_id,
            )
        return AgentResponse(
            text=result,
            raw={
                "sandboxId": sandbox.sandbox_id,
                "gatewayUrl": sandbox.gateway_url,
                "reusedExistingSandbox": sandbox.reused_existing_sandbox,
                "dataVolume": sandbox.data_volume,
            },
        )
