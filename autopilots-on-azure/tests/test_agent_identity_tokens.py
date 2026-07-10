from __future__ import annotations

import json
import unittest

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


if __name__ == "__main__":
    unittest.main()
