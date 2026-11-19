import json
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from bridge import simulator_ingress
from bridge.channel_auth import Agent365SimulatorClientFactory, ChannelAuthentication
from bridge.simulator_ingress import SimulatorChannel


TENANT = "11111111-1111-1111-1111-111111111111"
BLUEPRINT = "22222222-2222-2222-2222-222222222222"
AGENT_IDENTITY = "88888888-8888-8888-8888-888888888888"
CHANNEL = "33333333-3333-3333-3333-333333333333"
SIMULATOR_URL = "https://pmchat.example.test"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(key=KEY, **overrides):
    now = int(time.time())
    claims = {
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "aud": BLUEPRINT,
        "iat": now - 1,
        "nbf": now - 1,
        "exp": now + 300,
        "tid": TENANT,
        "azp": CHANNEL,
        "idtyp": "app",
        **overrides,
    }
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm="RS256")


def activity(service_url=SIMULATOR_URL, **overrides):
    return json.dumps({
        "type": "message",
        "id": "a1",
        "text": "ahoj",
        "channelId": "msteams",
        "serviceUrl": service_url,
        "from": {"id": "u1", "aadObjectId": "44444444-4444-4444-4444-444444444444"},
        "recipient": {"id": BLUEPRINT},
        "conversation": {"id": "c1"},
        **overrides,
    }).encode()


def request(bearer=""):
    headers = {"authorization": f"Bearer {bearer}"} if bearer else {}
    return SimpleNamespace(headers=headers, state=SimpleNamespace())


class SimulatorIngressTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {
            "USE_AGENTIC_AUTH": "true",
            "AGENT365_TENANT_ID": TENANT,
            "AGENT365_BLUEPRINT_CLIENT_ID": BLUEPRINT,
            "AGENT365_AGENT_IDENTITY_CLIENT_ID": AGENT_IDENTITY,
            "SIMULATOR_CHANNEL_CLIENT_ID": CHANNEL.upper(),
            "SIMULATOR_SERVICE_URL": SIMULATOR_URL + "/",
        }, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        keys = SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=KEY.public_key()))
        signing = patch.object(simulator_ingress, "_signing_keys", return_value=keys)
        signing.start()
        self.addCleanup(signing.stop)
        self.auth = ChannelAuthentication.from_environment()

    def test_agent365_mode_keeps_simulator_settings(self):
        self.assertEqual(self.auth.mode, "agent365")
        self.assertEqual(self.auth.simulator.channel_client_id, CHANNEL)
        self.assertEqual(self.auth.simulator.service_url, SIMULATOR_URL)

    def test_no_simulator_without_settings(self):
        with patch.dict(os.environ, {"SIMULATOR_CHANNEL_CLIENT_ID": "", "SIMULATOR_SERVICE_URL": ""}):
            self.assertIsNone(ChannelAuthentication.from_environment().simulator)

    def test_rejects_plain_http_service_url(self):
        with patch.dict(os.environ, {"SIMULATOR_SERVICE_URL": "http://pmchat.example.test"}):
            with self.assertRaises(ValueError):
                SimulatorChannel.from_environment()

    async def test_accepts_valid_simulator_token(self):
        req = request(token())
        await self.auth.authenticate(req, activity(f"{SIMULATOR_URL}/v3"))
        self.assertEqual(req.state.claims_identity.claims["azp"], CHANNEL)
        self.assertTrue(req.state.claims_identity.is_authenticated)

    async def test_accepts_v1_token_with_appid(self):
        req = request(token(iss=f"https://sts.windows.net/{TENANT}/", azp=None, appid=CHANNEL))
        await self.auth.authenticate(req, activity())
        self.assertEqual(req.state.claims_identity.claims["appid"], CHANNEL)

    async def test_simulator_activity_without_token_is_rejected(self):
        with self.assertRaises(HTTPException) as caught:
            await self.auth.authenticate(request(), activity())
        self.assertEqual(caught.exception.status_code, 401)

    async def test_rejects_wrong_caller_signature_audience_and_user_tokens(self):
        for bad in (
            token(azp="55555555-5555-5555-5555-555555555555"),
            token(key=OTHER_KEY),
            token(aud="66666666-6666-6666-6666-666666666666"),
            token(scp="access_agent", idtyp="user"),
            token(tid="77777777-7777-7777-7777-777777777777"),
        ):
            with self.assertRaises(HTTPException) as caught:
                await self.auth.authenticate(request(bad), activity())
            self.assertEqual(caught.exception.status_code, 401)

    async def test_other_service_urls_pass_through_unchanged(self):
        req = request()
        await self.auth.authenticate(req, activity("https://smba.trafficmanager.net/emea/"))
        self.assertFalse(hasattr(req.state, "claims_identity"))

    async def test_lookalike_host_is_not_the_simulator(self):
        req = request()
        await self.auth.authenticate(req, activity(SIMULATOR_URL + ".evil.test"))
        self.assertFalse(hasattr(req.state, "claims_identity"))

    async def test_accepts_token_for_agent_identity(self):
        req = request(token(aud=AGENT_IDENTITY))
        await self.auth.authenticate(req, activity())
        self.assertEqual(req.state.claims_identity.claims["aud"], AGENT_IDENTITY)

    async def test_reply_to_simulator_uses_agent_identity_token(self):
        blueprint = SimpleNamespace(get_agentic_application_token=AsyncMock(return_value="t1"))
        manager = SimpleNamespace(get_default_connection=lambda: blueprint)
        factory = Agent365SimulatorClientFactory(manager, self.auth.simulator)
        context = SimpleNamespace(activity=SimpleNamespace(
            is_agentic_request=lambda: False, service_url=SIMULATOR_URL))
        msal_app = SimpleNamespace(acquire_token_for_client=lambda scopes: (
            {"access_token": "reply", "expires_in": 3600} if scopes == [f"{CHANNEL}/.default"] else {}))
        with patch("bridge.channel_auth.ConfidentialClientApplication", return_value=msal_app) as cca, \
                patch("bridge.channel_auth.TeamsConnectorClient", side_effect=lambda **kw: kw):
            first = await factory.create_connector_client(context, object(), SIMULATOR_URL, BLUEPRINT)
            await factory.create_connector_client(context, object(), SIMULATOR_URL, BLUEPRINT)
        self.assertEqual(first, {"endpoint": SIMULATOR_URL, "token": "reply"})
        self.assertEqual(cca.call_args.args[0], AGENT_IDENTITY)
        self.assertEqual(cca.call_args.kwargs["client_credential"], {"client_assertion": "t1"})
        blueprint.get_agentic_application_token.assert_awaited_once_with(TENANT, AGENT_IDENTITY)

    async def test_reply_elsewhere_keeps_default_scope(self):
        factory = Agent365SimulatorClientFactory(SimpleNamespace(), self.auth.simulator)
        context = SimpleNamespace(activity=SimpleNamespace(
            is_agentic_request=lambda: True, service_url="https://smba.trafficmanager.net/emea/"))
        with patch(
            "microsoft_agents.hosting.core.RestChannelServiceClientFactory.create_connector_client",
            new=AsyncMock(return_value="client"),
        ) as parent:
            await factory.create_connector_client(context, object(), "https://smba.trafficmanager.net/emea/", "aud")
        self.assertIsNone(parent.await_args.args[4])


if __name__ == "__main__":
    unittest.main()
