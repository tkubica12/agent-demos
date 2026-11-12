from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from azure.core.credentials import AccessToken

from autopilots_identity.tokens import AgentIdentityTokenConfig, AgentIdentityTokenProvider


class FakeCredential:
    def get_token(self, scope: str) -> AccessToken:
        return AccessToken(f"managed:{scope}", 4102444800)


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


class FakeClient:
    def __init__(self, calls: list[dict[str, str]], responses: list[str], **_: object) -> None:
        self.calls = calls
        self.responses = responses

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def post(self, url: str, data: dict[str, str]) -> FakeResponse:
        self.calls.append({"url": url, **data})
        return FakeResponse({"access_token": self.responses.pop(0), "expires_in": 3600})


class AgentIdentityTokenProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_status_exposes_server_lifetime_but_never_credentials(self) -> None:
        provider = self.provider([], [])
        key = ("agent_user", "mail-resource/.default")
        with patch("autopilots_identity.tokens.time.time", return_value=1000):
            provider._store(key, "private-example-token", 3600)
        status = provider.cache_status()
        self.assertEqual(status[0]["serverExpiresAt"], 4600)
        self.assertEqual(status[0]["refreshAfter"], 4540)
        self.assertEqual(status[0]["acquisitions"], 1)
        self.assertNotIn("private-example-token", json.dumps(status))
        with patch("autopilots_identity.tokens.time.time", return_value=5000):
            provider._store(key, "new-private-example-token", 3600)
        self.assertEqual(provider.cache_status()[0]["acquisitions"], 2)

    async def test_short_server_lifetime_is_never_extended_by_cache_policy(self) -> None:
        provider = self.provider([], [])
        key = ("agent", "resource/.default")
        with patch("autopilots_identity.tokens.time.time", return_value=1000):
            provider._store(key, "short-lived-example", 5)
            self.assertEqual(provider._cached(key), "")
        self.assertEqual(provider.cache_status()[0]["serverExpiresAt"], 1005)

    def provider(self, calls: list[dict[str, str]], responses: list[str]) -> AgentIdentityTokenProvider:
        return AgentIdentityTokenProvider(
            AgentIdentityTokenConfig(
                tenant_id="tenant-1",
                blueprint_client_id="blueprint-1",
                agent_identity_client_id="agent-1",
                agent_user_id="user-1",
            ),
            credential_factory=FakeCredential,
            client_factory=lambda **kwargs: FakeClient(calls, responses, **kwargs),
        )

    async def test_agent_token_uses_managed_identity_then_blueprint_federation(self) -> None:
        calls: list[dict[str, str]] = []
        provider = self.provider(calls, ["blueprint-token", "agent-token"])

        token = await provider.get_agent_token("api://private/.default")

        self.assertEqual(token, "agent-token")
        self.assertEqual([call["client_id"] for call in calls], ["blueprint-1", "agent-1"])
        self.assertEqual(calls[0]["client_assertion"], "managed:api://AzureADTokenExchange/.default")
        self.assertEqual(calls[0]["fmi_path"], "agent-1")
        self.assertEqual(calls[1]["scope"], "api://private/.default")

    async def test_agent_user_token_uses_user_fic_exchange_and_cache(self) -> None:
        calls: list[dict[str, str]] = []
        provider = self.provider(calls, ["blueprint-token", "agent-exchange-token", "agent-user-token"])

        first = await provider.get_agent_user_token("mail-resource/Tools.ListInvoke.All")
        second = await provider.get_agent_user_token("mail-resource/Tools.ListInvoke.All")

        self.assertEqual(first, "agent-user-token")
        self.assertEqual(second, first)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[2]["grant_type"], "user_fic")
        self.assertEqual(calls[2]["user_id"], "user-1")
        self.assertEqual(calls[2]["user_federated_identity_credential"], "agent-exchange-token")

    async def test_independent_scopes_share_one_blueprint_exchange_and_run_concurrently(self) -> None:
        calls: list[dict[str, str]] = []

        class ScopeClient(FakeClient):
            async def post(self, url: str, data: dict[str, str]) -> FakeResponse:
                self.calls.append(data)
                await asyncio.sleep(0.01)
                return FakeResponse({"access_token": f"{data['grant_type']}:{data['scope']}", "expires_in": 3600})

        provider = AgentIdentityTokenProvider(
            AgentIdentityTokenConfig("tenant-1", "blueprint-1", "agent-1", "user-1"),
            credential_factory=FakeCredential,
            client_factory=lambda **kwargs: ScopeClient(calls, [], **kwargs),
        )

        tokens = await asyncio.gather(
            provider.get_agent_token("api://private/.default"),
            provider.get_agent_token("api://public/.default"),
            provider.get_agent_user_token("mail-resource/.default"),
            provider.get_agent_user_token("teams-resource/.default"),
        )

        self.assertEqual(tokens[0], "client_credentials:api://private/.default")
        self.assertEqual(tokens[3], "user_fic:teams-resource/.default")
        self.assertEqual(sum(call["client_id"] == "blueprint-1" for call in calls), 1)
        self.assertEqual(sum(call["scope"] == "api://AzureADTokenExchange/.default" and call["client_id"] == "agent-1"
                             for call in calls), 1)
        self.assertEqual(len(calls), 6)


if __name__ == "__main__":
    unittest.main()
