from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx
from azure.core.credentials import TokenCredential
from azure.identity import DefaultAzureCredential

from bridge.runtime.base import AgentRequest, AgentResponse
from scripts.sandbox_runtime import AgentSandboxConfig, config_from_environment, ensure_agent_sandbox

BRIDGE_INSTRUCTIONS = "You are Hermes behind the Autopilots on Azure bridge. Follow bridge instructions exactly."


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


def hermes_sandbox_config_from_env() -> AgentSandboxConfig:
    _env_required("API_SERVER_KEY", "HERMES_API_SERVER_KEY")
    return config_from_environment(runtime_kind="hermes", api_server_key=_env_required("API_SERVER_KEY", "HERMES_API_SERVER_KEY"))


def _clean_header_part(value: str, *, default: str) -> str:
    cleaned = value.replace("\r", "-").replace("\n", "-").replace("\x00", "-").strip()
    return cleaned or default


def _hermes_session_key(request: AgentRequest) -> str:
    autopilot = _env_optional("AUTOPILOT_INSTANCE_ID", "AUTOPILOT_NAME", "AGENT_RUNTIME", default="autopilot")
    parts = [
        _clean_header_part(autopilot, default="autopilot"),
        _clean_header_part(request.source, default="source"),
        _clean_header_part(request.user_id, default="user"),
    ]
    value = ":".join(parts)
    return value[:256]


def _endpoint_mode() -> str:
    value = _env_optional("HERMES_BRIDGE_ENDPOINT_MODE", default="auto").strip().lower().replace("-", "_")
    allowed = {"auto", "sessions", "responses", "chat_completions"}
    if value not in allowed:
        raise ValueError(f"Unsupported HERMES_BRIDGE_ENDPOINT_MODE '{value}'. Expected one of: {', '.join(sorted(allowed))}.")
    return value


class HermesRuntimeAdapter:
    def __init__(
        self,
        *,
        sandbox_lock: asyncio.Lock | None = None,
        credential_factory: Callable[[], TokenCredential] = DefaultAzureCredential,
        sandbox_config_factory: Callable[[], AgentSandboxConfig] = hermes_sandbox_config_from_env,
        ensure_sandbox: Callable[..., Any] = ensure_agent_sandbox,
        client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
    ) -> None:
        self._sandbox_lock = sandbox_lock or asyncio.Lock()
        self._credential_factory = credential_factory
        self._sandbox_config_factory = sandbox_config_factory
        self._ensure_sandbox = ensure_sandbox
        self._client_factory = client_factory

    @property
    def runtime_kind(self) -> str:
        return "hermes"

    async def invoke(self, request: AgentRequest) -> AgentResponse:
        config = self._sandbox_config_factory()
        credential = self._credential_factory()
        async with self._sandbox_lock:
            sandbox = await asyncio.to_thread(self._ensure_sandbox, config, credential=credential)
        if not sandbox.endpoint_url:
            raise RuntimeError(f"Sandbox {sandbox.sandbox_id} does not expose the Hermes API port.")

        base_url = sandbox.endpoint_url.rstrip("/")
        api_key = _env_required("API_SERVER_KEY", "HERMES_API_SERVER_KEY")
        await self._wait_for_health(base_url)
        endpoint, payload = await self._invoke_hermes(base_url, api_key, request)
        return AgentResponse(
            text=self._response_text(payload),
            raw={
                "sandboxId": sandbox.sandbox_id,
                "gatewayUrl": sandbox.endpoint_url,
                "reusedExistingSandbox": sandbox.reused_existing_sandbox,
                "dataVolume": sandbox.data_volume,
                "hermesEndpoint": endpoint,
                "payload": payload,
            },
        )

    async def _wait_for_health(self, base_url: str) -> None:
        deadline = time.time() + int(_env_optional("HERMES_HEALTH_TIMEOUT_SECONDS", default="120"))
        async with self._client_factory(timeout=10) as client:
            last_error: Exception | None = None
            while time.time() < deadline:
                try:
                    response = await client.get(f"{base_url}/health")
                    if response.status_code == 200:
                        return
                except Exception as exc:
                    last_error = exc
                await asyncio.sleep(2)
        raise TimeoutError(f"Timed out waiting for Hermes health at {base_url}/health: {last_error}")

    def _headers(self, api_key: str, request: AgentRequest) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key}",
            "X-Hermes-Session-Id": request.conversation_id,
            "X-Hermes-Session-Key": _hermes_session_key(request),
        }

    async def _invoke_hermes(self, base_url: str, api_key: str, request: AgentRequest) -> tuple[str, dict[str, Any]]:
        mode = _endpoint_mode()
        attempts: list[tuple[str, Callable[[], Any]]] = []
        if mode in {"auto", "sessions"}:
            attempts.append(("sessions", lambda: self._session_chat(base_url, api_key, request)))
        if mode in {"auto", "responses"}:
            attempts.append(("responses", lambda: self._responses_api(base_url, api_key, request)))
        if mode in {"auto", "chat_completions"}:
            attempts.append(("chat_completions", lambda: self._chat_completion(base_url, api_key, request)))

        last_error: httpx.HTTPStatusError | None = None
        for endpoint, call in attempts:
            try:
                return endpoint, await call()
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if mode != "auto" or exc.response.status_code not in {404, 405}:
                    raise
        if last_error:
            raise last_error
        raise RuntimeError("No Hermes endpoint attempts were configured.")

    async def _session_chat(self, base_url: str, api_key: str, request: AgentRequest) -> dict[str, Any]:
        session_id = quote(request.conversation_id, safe="")
        body = {
            "input": request.prompt,
            "instructions": BRIDGE_INSTRUCTIONS,
        }
        async with self._client_factory(timeout=int(_env_optional("HERMES_BRIDGE_TIMEOUT_SECONDS", default="600"))) as client:
            response = await client.post(f"{base_url}/api/sessions/{session_id}/chat", headers=self._headers(api_key, request), json=body)
            response.raise_for_status()
            return response.json()

    async def _responses_api(self, base_url: str, api_key: str, request: AgentRequest) -> dict[str, Any]:
        body = {
            "model": _env_optional("HERMES_MODEL", "OPENCLAW_MODEL_ID", default="gpt-5-4-mini"),
            "input": request.prompt,
            "instructions": BRIDGE_INSTRUCTIONS,
            "conversation": request.conversation_id,
        }
        async with self._client_factory(timeout=int(_env_optional("HERMES_BRIDGE_TIMEOUT_SECONDS", default="600"))) as client:
            response = await client.post(f"{base_url}/v1/responses", headers=self._headers(api_key, request), json=body)
            response.raise_for_status()
            return response.json()

    async def _chat_completion(self, base_url: str, api_key: str, request: AgentRequest) -> dict[str, Any]:
        messages = [
            {
                "role": "system",
                "content": BRIDGE_INSTRUCTIONS,
            },
            {"role": "user", "content": request.prompt},
        ]
        body = {
            "model": _env_optional("HERMES_MODEL", "OPENCLAW_MODEL_ID", default="gpt-5-4-mini"),
            "messages": messages,
        }
        async with self._client_factory(timeout=int(_env_optional("HERMES_BRIDGE_TIMEOUT_SECONDS", default="600"))) as client:
            response = await client.post(f"{base_url}/v1/chat/completions", headers=self._headers(api_key, request), json=body)
            response.raise_for_status()
            return response.json()

    @staticmethod
    def _response_text(payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    return message["content"].strip()
                if isinstance(first.get("text"), str):
                    return first["text"].strip()
        output = payload.get("output_text")
        if isinstance(output, str) and output.strip():
            return output.strip()
        output = payload.get("output")
        if isinstance(output, str) and output.strip():
            return output.strip()
        if isinstance(output, list):
            text_parts: list[str] = []
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and isinstance(part.get("text"), str):
                            text_parts.append(part["text"])
                if isinstance(item.get("text"), str):
                    text_parts.append(item["text"])
            text = "\n".join(part.strip() for part in text_parts if part.strip()).strip()
            if text:
                return text
        for key in ("final_response", "response", "content"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return "No reply from Hermes."
