from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sys
import time
import uuid
from functools import cached_property
from typing import Any
from urllib.parse import urlparse, urlunparse

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat, load_pem_private_key
import websockets


class OpenClawGatewayError(RuntimeError):
    pass


def gateway_http_url_to_ws(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    if parsed.scheme == "https":
        scheme = "wss"
    elif parsed.scheme == "http":
        scheme = "ws"
    elif parsed.scheme in {"ws", "wss"}:
        scheme = parsed.scheme
    else:
        raise ValueError(f"Unsupported gateway URL scheme: {parsed.scheme}")
    return urlunparse((scheme, parsed.netloc, parsed.path or "/", "", parsed.query, ""))


class OpenClawGatewayClient:
    def __init__(
        self,
        *,
        url: str,
        token: str,
        timeout_seconds: int = 600,
        device_token: str | None = None,
        device_private_key_pem: str | None = None,
    ) -> None:
        self.url = url
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.device_token = device_token
        self.device_private_key_pem = device_private_key_pem
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.accepted: dict[str, dict[str, Any]] = {}

    @cached_property
    def device_private_key(self) -> Ed25519PrivateKey:
        if self.device_private_key_pem:
            loaded = load_pem_private_key(self.device_private_key_pem.encode("utf-8"), password=None)
            if not isinstance(loaded, Ed25519PrivateKey):
                raise ValueError("OPENCLAW_BRIDGE_DEVICE_PRIVATE_KEY_PEM must be an Ed25519 private key.")
            return loaded
        return Ed25519PrivateKey.generate()

    async def invoke_agent(self, *, message: str, session_key: str, agent_id: str | None = None) -> str:
        async with websockets.connect(self.url, max_size=25 * 1024 * 1024) as websocket:
            await self._connect(websocket)
            params: dict[str, Any] = {
                "message": message,
                "sessionKey": session_key,
                "timeout": self.timeout_seconds,
                "cleanupBundleMcpOnRunEnd": True,
                "idempotencyKey": str(uuid.uuid4()),
            }
            if agent_id:
                params["agentId"] = agent_id
            payload = await self._request(
                websocket,
                "agent",
                params,
                expect_final=True,
                timeout_seconds=self.timeout_seconds + 30,
            )
        return self._response_text(payload)

    async def _connect(self, websocket) -> None:
        deadline = asyncio.get_running_loop().time() + 30
        while True:
            timeout = deadline - asyncio.get_running_loop().time()
            if timeout <= 0:
                raise TimeoutError("Timed out waiting for OpenClaw gateway connect challenge.")
            frame = json.loads(await asyncio.wait_for(websocket.recv(), timeout=timeout))
            if frame.get("type") != "event" or frame.get("event") != "connect.challenge":
                continue
            nonce = (frame.get("payload") or {}).get("nonce")
            if not nonce:
                raise OpenClawGatewayError("OpenClaw gateway connect challenge did not include a nonce.")
            scopes = [
                "operator.admin",
                "operator.read",
                "operator.write",
                "operator.approvals",
                "operator.pairing",
                "operator.talk.secrets",
            ]
            connect_token = self.device_token or self.token
            device = create_device_auth(
                nonce=nonce,
                token=connect_token,
                scopes=scopes,
                client_id="gateway-client",
                client_mode="backend",
                role="operator",
                private_key=self.device_private_key,
            )
            auth = {"token": connect_token}
            if self.device_token:
                auth["deviceToken"] = self.device_token
            await self._request(
                websocket,
                "connect",
                {
                    "minProtocol": 4,
                    "maxProtocol": 4,
                    "client": {
                        "id": "gateway-client",
                        "displayName": "openclaw-aca-express-bridge",
                        "version": "0.1.0",
                        "platform": sys.platform,
                        "mode": "backend",
                        "instanceId": str(uuid.uuid4()),
                    },
                    "caps": [],
                    "auth": auth,
                    "role": "operator",
                    "scopes": scopes,
                    "device": device,
                },
                timeout_seconds=30,
            )
            return

    async def _request(
        self,
        websocket,
        method: str,
        params: dict[str, Any],
        *,
        timeout_seconds: int,
        expect_final: bool = False,
    ) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        await websocket.send(json.dumps({"type": "req", "id": request_id, "method": method, "params": params}))
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            timeout = deadline - asyncio.get_running_loop().time()
            if timeout <= 0:
                raise TimeoutError(f"Timed out waiting for OpenClaw gateway method {method}.")
            frame = json.loads(await asyncio.wait_for(websocket.recv(), timeout=timeout))
            if frame.get("type") == "event":
                continue
            if frame.get("type") != "res" or frame.get("id") != request_id:
                continue
            if not frame.get("ok"):
                error = frame.get("error") or {}
                message = error.get("message") or f"OpenClaw gateway method {method} failed."
                raise OpenClawGatewayError(message)
            payload = frame.get("payload") or {}
            if expect_final and payload.get("status") == "accepted":
                self.accepted[request_id] = payload
                continue
            return payload

    @staticmethod
    def _response_text(payload: dict[str, Any]) -> str:
        if payload.get("status") == "in_flight":
            run_id = payload.get("runId")
            return f"Agent run {run_id} is already in flight." if run_id else "Agent run is already in flight."

        payloads = ((payload.get("result") or {}).get("payloads") or [])
        parts: list[str] = []
        for item in payloads:
            text = item.get("text") if isinstance(item, dict) else None
            if text:
                parts.append(str(text).rstrip())
            media_urls = item.get("mediaUrls") if isinstance(item, dict) else None
            if isinstance(media_urls, list):
                parts.extend(f"Attachment: {url}" for url in media_urls if isinstance(url, str))
            media_url = item.get("mediaUrl") if isinstance(item, dict) else None
            if isinstance(media_url, str):
                parts.append(f"Attachment: {media_url}")
        if parts:
            return "\n".join(parts)
        return payload.get("summary") or "No reply from agent."


def base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def create_device_auth(
    *,
    nonce: str,
    token: str,
    scopes: list[str],
    client_id: str,
    client_mode: str,
    role: str,
    private_key: Ed25519PrivateKey | None = None,
) -> dict[str, Any]:
    private_key = private_key or Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    public_raw = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    device_id = hashlib.sha256(public_raw).hexdigest()
    signed_at = int(time.time() * 1000)
    payload = "|".join(
        [
            "v3",
            device_id,
            client_id,
            client_mode,
            role,
            ",".join(scopes),
            str(signed_at),
            token,
            nonce,
            sys.platform,
            "",
        ]
    )
    signature = private_key.sign(payload.encode("utf-8"))
    return {
        "id": device_id,
        "publicKey": base64url(public_raw),
        "signature": base64url(signature),
        "signedAt": signed_at,
        "nonce": nonce,
    }


def generate_bridge_device_identity() -> dict[str, str]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    public_raw = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    private_pem = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode("utf-8")
    return {
        "deviceId": hashlib.sha256(public_raw).hexdigest(),
        "publicKey": base64url(public_raw),
        "privateKeyPem": private_pem,
    }
