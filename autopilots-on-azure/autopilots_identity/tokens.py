from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from azure.core.credentials import AccessToken, TokenCredential
from azure.identity import DefaultAzureCredential


TOKEN_EXCHANGE_SCOPE = "api://AzureADTokenExchange/.default"
CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"


def _env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value and value != "not-configured":
            return value
    return ""


@dataclass(frozen=True)
class AgentIdentityTokenConfig:
    tenant_id: str
    blueprint_client_id: str
    agent_identity_client_id: str
    agent_user_id: str = ""
    authority_host: str = "https://login.microsoftonline.com"

    @classmethod
    def from_environment(cls) -> "AgentIdentityTokenConfig":
        return cls(
            tenant_id=_env(
                "AGENT365_TENANT_ID",
                "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID",
                "AZURE_TENANT_ID",
            ),
            blueprint_client_id=_env(
                "AGENT365_BLUEPRINT_CLIENT_ID",
                "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID",
            ),
            agent_identity_client_id=_env("AGENT365_AGENT_IDENTITY_CLIENT_ID"),
            agent_user_id=_env("AGENT365_AGENT_USER_ID"),
            authority_host=_env("AGENT365_AUTHORITY_HOST") or "https://login.microsoftonline.com",
        )

    def validate(self, *, require_agent_user: bool = False) -> None:
        missing = []
        if not self.tenant_id:
            missing.append("AGENT365_TENANT_ID")
        if not self.blueprint_client_id:
            missing.append("AGENT365_BLUEPRINT_CLIENT_ID")
        if not self.agent_identity_client_id:
            missing.append("AGENT365_AGENT_IDENTITY_CLIENT_ID")
        if require_agent_user and not self.agent_user_id:
            missing.append("AGENT365_AGENT_USER_ID")
        if missing:
            raise RuntimeError(f"Agent 365 token acquisition requires: {', '.join(missing)}.")


class AgentIdentityTokenProvider:
    def __init__(
        self,
        config: AgentIdentityTokenConfig,
        *,
        credential_factory: Callable[[], TokenCredential] = DefaultAzureCredential,
        client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
    ) -> None:
        self._config = config
        self._credential_factory = credential_factory
        self._client_factory = client_factory
        self._credential: TokenCredential | None = None
        self._cache: dict[tuple[str, str], tuple[str, float]] = {}
        self._cache_metadata: dict[tuple[str, str], dict[str, int | float]] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    @classmethod
    def from_environment(cls) -> "AgentIdentityTokenProvider":
        return cls(AgentIdentityTokenConfig.from_environment())

    async def get_agent_token(self, scope: str) -> str:
        self._config.validate()
        return await self._cached_token(("agent", scope), lambda: self._exchange_agent_token(scope))

    async def get_agent_user_token(self, scope: str) -> str:
        self._config.validate(require_agent_user=True)
        return await self._cached_token(("agent_user", scope), lambda: self._exchange_agent_user_token(scope))

    async def _cached_token(
        self,
        key: tuple[str, str],
        acquire: Callable[[], Awaitable[tuple[str, int]]],
    ) -> str:
        """Serialize acquisition per token, so independent scopes are exchanged concurrently."""
        async with self._locks.setdefault(key, asyncio.Lock()):
            cached = self._cached(key)
            if cached:
                return cached
            token, expires_in = await acquire()
            self._store(key, token, expires_in)
            return token

    async def _exchange_agent_token(self, scope: str) -> tuple[str, int]:
        return await self._exchange(
            {
                "client_id": self._config.agent_identity_client_id,
                "scope": scope,
                "grant_type": "client_credentials",
                "client_assertion_type": CLIENT_ASSERTION_TYPE,
                "client_assertion": await self._blueprint_exchange_token(),
            }
        )

    async def _exchange_agent_user_token(self, scope: str) -> tuple[str, int]:
        blueprint_token = await self._blueprint_exchange_token()
        agent_exchange_token = await self.get_agent_token(TOKEN_EXCHANGE_SCOPE)
        return await self._exchange(
            {
                "client_id": self._config.agent_identity_client_id,
                "scope": scope,
                "grant_type": "user_fic",
                "client_assertion_type": CLIENT_ASSERTION_TYPE,
                "client_assertion": blueprint_token,
                "user_id": self._config.agent_user_id,
                "user_federated_identity_credential": agent_exchange_token,
            }
        )

    async def _blueprint_exchange_token(self) -> str:
        return await self._cached_token(("blueprint", TOKEN_EXCHANGE_SCOPE), self._exchange_blueprint_token)

    async def _exchange_blueprint_token(self) -> tuple[str, int]:
        credential = self._credential
        if credential is None:
            credential = self._credential_factory()
            self._credential = credential
        managed_identity_token: AccessToken = await asyncio.to_thread(credential.get_token, TOKEN_EXCHANGE_SCOPE)
        return await self._exchange(
            {
                "client_id": self._config.blueprint_client_id,
                "scope": TOKEN_EXCHANGE_SCOPE,
                "grant_type": "client_credentials",
                "client_assertion_type": CLIENT_ASSERTION_TYPE,
                "client_assertion": managed_identity_token.token,
                "fmi_path": self._config.agent_identity_client_id,
            }
        )

    async def _exchange(self, form: dict[str, str]) -> tuple[str, int]:
        endpoint = f"{self._config.authority_host.rstrip('/')}/{self._config.tenant_id}/oauth2/v2.0/token"
        async with self._client_factory(timeout=30) as client:
            response = await client.post(endpoint, data=form)
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
        token = str(payload.get("access_token", "")).strip()
        if not token:
            raise RuntimeError("Microsoft Entra token exchange returned no access_token.")
        expires_in = payload.get("expires_in")
        if isinstance(expires_in, bool) or not isinstance(expires_in, (str, int)) or int(expires_in) <= 0:
            raise RuntimeError("Microsoft Entra token exchange returned no valid server lifetime.")
        return token, int(expires_in)

    def _cached(self, key: tuple[str, str]) -> str:
        value = self._cache.get(key)
        if value and value[1] > time.time():
            return value[0]
        return ""

    def _store(self, key: tuple[str, str], token: str, expires_in: int) -> None:
        acquired = time.time()
        refresh_at = acquired + max(0, expires_in - 60)
        self._cache[key] = (token, refresh_at)
        self._cache_metadata[key] = {
            "acquiredAt": acquired, "serverExpiresAt": acquired + expires_in,
            "refreshAfter": refresh_at,
            "acquisitions": int(self._cache_metadata.get(key, {}).get("acquisitions", 0)) + 1,
        }

    def cache_status(self) -> list[dict[str, Any]]:
        return [
            {"kind": key[0], "scope": key[1], **metadata}
            for key, metadata in list(self._cache_metadata.items())
        ]
