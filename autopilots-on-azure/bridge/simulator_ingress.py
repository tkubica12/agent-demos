"""Admit the chat simulator as a channel of an Agent 365 Worker.

The simulator delivers activities with an application token from its channel app
(audience: the Worker's Agent Identity, or its blueprint). The Worker keeps its Agent 365
identity, Agent User and Work IQ tools; replies go back to the simulator with an Agent
Identity token for the simulator channel app, because Entra does not let a blueprint
request app-only tokens for other resources (AADSTS82001).
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit

import jwt


@dataclass(frozen=True)
class SimulatorChannel:
    channel_client_id: str
    service_url: str
    tenant_id: str
    blueprint_client_id: str
    agent_identity_client_id: str

    @classmethod
    def from_environment(cls) -> "SimulatorChannel | None":
        client_id = os.getenv("SIMULATOR_CHANNEL_CLIENT_ID", "").strip()
        service_url = os.getenv("SIMULATOR_SERVICE_URL", "").strip().rstrip("/")
        if not client_id and not service_url:
            return None
        if not client_id or not service_url:
            raise ValueError("SIMULATOR_CHANNEL_CLIENT_ID and SIMULATOR_SERVICE_URL must both be set.")
        tenant = os.getenv("AGENT365_TENANT_ID", "").strip()
        blueprint = os.getenv("AGENT365_BLUEPRINT_CLIENT_ID", "").strip()
        agent_identity = os.getenv("AGENT365_AGENT_IDENTITY_CLIENT_ID", "").strip()
        parsed = urlsplit(service_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
            or "\\" in service_url
            or any(ord(char) <= 32 for char in service_url)
        ):
            raise ValueError("SIMULATOR_SERVICE_URL must be the simulator's HTTPS origin.")
        return cls(
            channel_client_id=str(uuid.UUID(client_id)),
            service_url=service_url,
            tenant_id=str(uuid.UUID(tenant)),
            blueprint_client_id=str(uuid.UUID(blueprint)),
            agent_identity_client_id=str(uuid.UUID(agent_identity)),
        )

    def owns(self, service_url: object) -> bool:
        raw = str(service_url or "").strip()
        if not raw or "\\" in raw or any(ord(char) <= 32 for char in raw):
            return False
        parsed = urlsplit(raw)
        expected = urlsplit(self.service_url)
        if (
            parsed.scheme.lower() != expected.scheme.lower()
            or (parsed.hostname or "").lower() != (expected.hostname or "").lower()
            or (parsed.port or 443) != (expected.port or 443)
            or parsed.username
            or parsed.password
        ):
            return False
        return parsed.path == "" or parsed.path.startswith("/")

    @property
    def reply_scopes(self) -> list[str]:
        return [f"{self.channel_client_id}/.default"]

    def verify(self, token: str) -> dict:
        """Return the claims of a valid simulator channel application token or raise ValueError."""
        try:
            key = _signing_keys(self.tenant_id).get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=[
                    self.agent_identity_client_id,
                    self.blueprint_client_id,
                    f"api://{self.blueprint_client_id}",
                ],
                options={"require": ["exp", "iat", "nbf", "iss", "aud", "tid"]},
            )
        except jwt.PyJWKClientConnectionError:
            raise
        except jwt.PyJWTError as exc:
            raise ValueError("Invalid simulator channel token.") from exc
        issuers = {
            f"https://login.microsoftonline.com/{self.tenant_id}/v2.0",
            f"https://sts.windows.net/{self.tenant_id}/",
        }
        caller = str(claims.get("azp") or claims.get("appid") or "").lower()
        if (
            str(claims["iss"]).lower() not in issuers
            or str(claims["tid"]).lower() != self.tenant_id
            or caller != self.channel_client_id
            or claims.get("scp") is not None
            or claims.get("idtyp", "app") != "app"
            or claims["exp"] <= time.time()
        ):
            raise ValueError("Invalid simulator channel issuer, tenant, caller or application claims.")
        return claims


@lru_cache(maxsize=4)
def _signing_keys(tenant_id: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(
        f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys", cache_keys=True, lifespan=3600
    )
