from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import cache
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .tokens import AgentIdentityTokenProvider


_HOP_BY_HOP_HEADERS = {
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


@dataclass(frozen=True)
class McpServerConfig:
    upstream_url: str
    scope: str
    identity_mode: str

    @classmethod
    def from_dict(cls, name: str, payload: object) -> "McpServerConfig":
        if not isinstance(payload, dict):
            raise ValueError(f"MCP identity proxy server '{name}' must be a JSON object.")
        upstream_url = str(payload.get("upstreamUrl", "")).strip()
        scope = str(payload.get("scope", "")).strip()
        identity_mode = str(payload.get("identityMode", "agent")).strip().lower()
        parsed = urlsplit(upstream_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError(f"MCP identity proxy server '{name}' requires an HTTPS upstreamUrl.")
        if not scope:
            raise ValueError(f"MCP identity proxy server '{name}' requires a scope.")
        if identity_mode not in {"agent", "agent_user"}:
            raise ValueError(f"MCP identity proxy server '{name}' has unsupported identityMode '{identity_mode}'.")
        return cls(upstream_url=upstream_url, scope=scope, identity_mode=identity_mode)


def load_server_configs(value: str | None = None) -> dict[str, McpServerConfig]:
    raw = value if value is not None else os.getenv("AGENT_MCP_SERVERS_JSON", "")
    if not raw.strip():
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("AGENT_MCP_SERVERS_JSON must contain a JSON object.")
    return {str(name): McpServerConfig.from_dict(str(name), config) for name, config in payload.items()}


@cache
def server_configs() -> dict[str, McpServerConfig]:
    return load_server_configs()


@cache
def token_provider() -> AgentIdentityTokenProvider:
    return AgentIdentityTokenProvider.from_environment()


def upstream_url(config: McpServerConfig, path: str, query: str) -> str:
    base = urlsplit(config.upstream_url)
    suffix = path.strip("/")
    target_path = base.path.rstrip("/")
    if suffix:
        target_path = f"{target_path}/{suffix}"
    return urlunsplit((base.scheme, base.netloc, target_path or "/", query, ""))


def forwarded_request_headers(request: Request, token: str) -> dict[str, str]:
    headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() not in _HOP_BY_HOP_HEADERS and name.lower() != "authorization"
    }
    headers["Authorization"] = " ".join(("Bearer", token))
    return headers


def forwarded_response_headers(response: httpx.Response) -> dict[str, str]:
    return {
        name: value
        for name, value in response.headers.items()
        if name.lower() not in _HOP_BY_HOP_HEADERS
    }


async def access_token(config: McpServerConfig) -> str:
    provider = token_provider()
    if config.identity_mode == "agent_user":
        return await provider.get_agent_user_token(config.scope)
    return await provider.get_agent_token(config.scope)


app = FastAPI(title="Autopilots Agent Identity MCP adapter")


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "servers": sorted(server_configs())})


@app.api_route(
    "/servers/{server_name}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
@app.api_route(
    "/servers/{server_name}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def proxy(server_name: str, request: Request, path: str = "") -> StreamingResponse:
    config = server_configs().get(server_name)
    if config is None:
        raise HTTPException(status_code=404, detail=f"Unknown MCP server '{server_name}'.")
    token = await access_token(config)
    client = httpx.AsyncClient(timeout=None)
    upstream_request = client.build_request(
        request.method,
        upstream_url(config, path, request.url.query),
        headers=forwarded_request_headers(request, token),
        content=await request.body(),
    )
    try:
        response = await client.send(upstream_request, stream=True)
    except Exception:
        await client.aclose()
        raise

    async def stream_body():
        try:
            async for chunk in response.aiter_raw():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    return StreamingResponse(
        stream_body(),
        status_code=response.status_code,
        headers=forwarded_response_headers(response),
    )
