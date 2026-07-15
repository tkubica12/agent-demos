from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any

from azure.core.credentials import TokenCredential
from azure.identity import DefaultAzureCredential

from bridge.gateway_client import OpenClawGatewayClient, OpenClawGatewayError, gateway_http_url_to_ws
from bridge.runtime.base import AgentRequest, AgentResponse, DreamRequest, DreamResponse
from scripts.sandbox_runtime import AgentSandboxConfig, config_from_environment, ensure_agent_sandbox


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


def openclaw_sandbox_config_from_env() -> AgentSandboxConfig:
    _env_required("OPENCLAW_GATEWAY_TOKEN")
    return config_from_environment(runtime_kind="openclaw")


class OpenClawRuntimeAdapter:
    def __init__(
        self,
        *,
        sandbox_lock: asyncio.Lock | None = None,
        credential_factory: Callable[[], TokenCredential] = DefaultAzureCredential,
        sandbox_config_factory: Callable[[], AgentSandboxConfig] = openclaw_sandbox_config_from_env,
        ensure_sandbox: Callable[..., Any] = ensure_agent_sandbox,
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
        try:
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
        except OpenClawGatewayError as exc:
            exc.sandbox_id = sandbox.sandbox_id
            exc.gateway_url = sandbox.gateway_url
            raise
        return AgentResponse(
            text=result,
            raw={
                "sandboxId": sandbox.sandbox_id,
                "gatewayUrl": sandbox.gateway_url,
                "reusedExistingSandbox": sandbox.reused_existing_sandbox,
                "dataVolume": sandbox.data_volume,
            },
        )

    async def dream(self, request: DreamRequest) -> DreamResponse:
        raise RuntimeError("Dream runs are supported only by the Hermes runtime.")
