from __future__ import annotations

import json
import os

import httpx
from azure.identity import DefaultAzureCredential
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse


TOKEN_SCOPE = os.getenv("FOUNDRY_TOKEN_SCOPE", "https://cognitiveservices.azure.com/.default")
SYSTEM_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"

app = FastAPI(title="Foundry Entra token proxy for OpenClaw")
credential = DefaultAzureCredential()


def foundry_base_url() -> str:
    value = os.getenv("FOUNDRY_OPENAI_BASE_URL")
    if not value:
        raise RuntimeError("FOUNDRY_OPENAI_BASE_URL must be set, for example https://<resource>.services.ai.azure.com/openai/v1")
    return value.rstrip("/")


def target_url(path: str, query: bytes) -> str:
    stripped = path
    if stripped.startswith("/v1/"):
        stripped = stripped[len("/v1") :]
    elif stripped == "/v1":
        stripped = ""
    url = f"{foundry_base_url()}{stripped}"
    if query:
        url = f"{url}?{query.decode('utf-8')}"
    return url


def forwarded_headers(request: Request) -> dict[str, str]:
    blocked = {"host", "authorization", "api-key", "content-length"}
    headers = {key: value for key, value in request.headers.items() if key.lower() not in blocked}
    token = credential.get_token(TOKEN_SCOPE).token
    headers["Authorization"] = f"Bearer {token}"
    return headers


def verify_setting() -> bool | str:
    configured = os.getenv("FOUNDRY_PROXY_CA_BUNDLE")
    if configured:
        return configured
    if os.path.exists(SYSTEM_CA_BUNDLE):
        return SYSTEM_CA_BUNDLE
    return True


def translated_body(path: str, body: bytes) -> bytes:
    if not path.endswith("/chat/completions") or not body:
        return body
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body
    if "max_tokens" in payload and "max_completion_tokens" not in payload:
        payload["max_completion_tokens"] = payload.pop("max_tokens")
        return json.dumps(payload).encode("utf-8")
    return body


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy(path: str, request: Request) -> StreamingResponse:
    body = translated_body(request.url.path, await request.body())
    url = target_url(request.url.path, request.url.query.encode("utf-8"))
    client = httpx.AsyncClient(timeout=None, verify=verify_setting())
    upstream = await client.send(
        client.build_request(
            request.method,
            url,
            content=body,
            headers=forwarded_headers(request),
        ),
        stream=True,
    )
    excluded = {"content-encoding", "transfer-encoding", "connection"}
    headers = {key: value for key, value in upstream.headers.items() if key.lower() not in excluded}

    async def stream_body():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        stream_body(),
        status_code=upstream.status_code,
        headers=headers,
        media_type=upstream.headers.get("content-type"),
    )
