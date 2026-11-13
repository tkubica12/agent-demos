from __future__ import annotations

import json
import base64
import os
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException, Request
from microsoft_agents.activity import Activity, ChannelAccount, ConversationAccount
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.core import ClaimsIdentity, TurnContext

from bridge import app as bridge_app
from bridge.channel_auth import ChannelAuthentication, ChannelCloudAdapter, ChannelServiceClientFactory, SERVICE_SETTINGS
from bridge.proactive_delivery import delivery_reference_metadata, send_proactive_activity
from bridge.generated_files import GeneratedAttachment


TENANT = "11111111-1111-4111-8111-111111111111"
AGENT = "22222222-2222-4222-8222-222222222222"
CHANNEL = "33333333-3333-4333-8333-333333333333"
STANDARD = {
    f"{SERVICE_SETTINGS}AUTHTYPE": "FederatedCredentials",
    f"{SERVICE_SETTINGS}CLIENTID": AGENT,
    f"{SERVICE_SETTINGS}TENANTID": TENANT,
    f"{SERVICE_SETTINGS}FEDERATEDCLIENTID": "44444444-4444-4444-8444-444444444444",
    f"{SERVICE_SETTINGS}SCOPES__0": f"{CHANNEL}/.default",
    "CONNECTIONSMAP__0__SERVICEURL": r"^https://simulator\.example/?$",
    "CONNECTIONSMAP__0__CONNECTION": "SERVICE_CONNECTION",
}
DEMO = {
    "DEMO_CHANNEL_KEY": "test-key-not-a-secret-" + "x" * 32,
    "DEMO_SERVICE_URL": "https://simulator.example/demo/test-key-not-a-secret-" + "x" * 32,
}


def request(headers=None):
    return Request({
        "type": "http", "method": "POST", "path": "/api/messages",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    })


def activity(service_url="https://simulator.example"):
    return Activity(
        type="message", service_url=service_url, text="hello", id="message-1",
        channel_id="msteams",
        from_property=ChannelAccount(id="user-1", aad_object_id="owner-1"),
        recipient=ChannelAccount(id=f"28:{AGENT}", name="Agent"),
        conversation=ConversationAccount(id="conversation-1", conversation_type="personal"),
    )


class ChannelAuthenticationTests(unittest.IsolatedAsyncioTestCase):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def token(self, **overrides):
        return jwt.encode({
            "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
            "aud": AGENT, "azp": CHANNEL, "tid": TENANT, "ver": "2.0", "idtyp": "app",
            "iat": int(time.time()) - 1, "nbf": int(time.time()) - 1, "exp": int(time.time()) + 300,
            **overrides,
        }, self.key, algorithm="RS256")

    async def test_standard_configuration_authenticates_without_agent365_variables(self):
        with patch.dict(os.environ, STANDARD, clear=True):
            auth = ChannelAuthentication.from_environment()
        self.assertEqual(auth.mode, "standard")
        manager = MsalConnectionManager(**auth.configuration)
        incoming = request({"Authorization": f"Bearer {self.token()}"})
        with patch.object(auth.validator, "_get_public_key_or_secret", AsyncMock(return_value=self.key.public_key())):
            await auth.authenticate(incoming, activity().model_dump_json(by_alias=True, exclude_none=True).encode(), manager)
        self.assertTrue(incoming.state.claims_identity.is_authenticated)
        self.assertEqual(incoming.state.claims_identity.claims["azp"], CHANNEL)

    async def test_standard_tokens_reject_wrong_boundary_expiry_and_signature(self):
        with patch.dict(os.environ, STANDARD, clear=True):
            auth = ChannelAuthentication.from_environment()
        manager = MsalConnectionManager(**auth.configuration)
        body = activity().model_dump_json(by_alias=True, exclude_none=True).encode()
        invalid = [
            self.token(aud="other-agent"), self.token(azp=AGENT),
            self.token(tid=CHANNEL), self.token(iss="https://attacker.example"),
            self.token(scp="access_as_user"), self.token(idtyp="user"),
            self.token(ver="1.0"), self.token(exp=int(time.time()) - 1),
            self.token(iat=None), self.token(aud=[AGENT]), self.token() + "invalid", "not-a-token",
        ]
        with patch.object(auth.validator, "_get_public_key_or_secret", AsyncMock(return_value=self.key.public_key())):
            for token in invalid:
                with self.subTest(token=token[:10]):
                    with self.assertRaises(HTTPException) as error:
                        await auth.authenticate(request({"Authorization": f"Bearer {token}"}), body, manager)
                    self.assertEqual(error.exception.status_code, 401)
            for headers in ({}, {"Authorization": "Basic not-a-token"}):
                with self.assertRaises(HTTPException) as error:
                    await auth.authenticate(request(headers), body, manager)
                self.assertEqual(error.exception.status_code, 401)

    async def test_standard_service_url_must_match_mapping(self):
        with patch.dict(os.environ, STANDARD, clear=True):
            auth = ChannelAuthentication.from_environment()
        manager = MsalConnectionManager(**auth.configuration)
        with patch.object(auth.validator, "_get_public_key_or_secret", AsyncMock(return_value=self.key.public_key())):
            for url in ("https://attacker.example", "https://simulator.example.evil", "http://simulator.example"):
                with self.assertRaises(HTTPException) as error:
                    await auth.authenticate(
                        request({"Authorization": f"Bearer {self.token()}"}),
                        activity(url).model_dump_json(by_alias=True, exclude_none=True).encode(), manager,
                    )
                self.assertEqual(error.exception.status_code, 403)

    async def test_demo_requires_header_and_exact_service_url_and_sets_sdk_anonymous_identity(self):
        with patch.dict(os.environ, DEMO, clear=True):
            auth = ChannelAuthentication.from_environment()
        body = activity(DEMO["DEMO_SERVICE_URL"]).model_dump_json(by_alias=True, exclude_none=True).encode()
        for headers in ({}, {"X-Demo-Channel-Key": "wrong"}, {"X-Demo-Channel-Key": "\u0100" * 32}):
            with self.assertRaises(HTTPException) as error:
                await auth.authenticate(request(headers), body)
            self.assertEqual(error.exception.status_code, 401)
        incoming = request({"X-Demo-Channel-Key": DEMO["DEMO_CHANNEL_KEY"]})
        await auth.authenticate(incoming, body)
        self.assertEqual(incoming.state.claims_identity.authentication_type, "Anonymous")
        self.assertFalse(incoming.state.claims_identity.is_authenticated)
        with self.assertRaises(HTTPException) as error:
            await auth.authenticate(incoming, activity(DEMO["DEMO_SERVICE_URL"] + "/").model_dump_json(by_alias=True, exclude_none=True).encode())
        self.assertEqual(error.exception.status_code, 403)

    async def test_demo_sdk_request_route_does_not_require_bearer(self):
        with patch.dict(os.environ, DEMO, clear=True):
            auth = ChannelAuthentication.from_environment()
        observed = []

        async def process(incoming, *_):
            observed.append(incoming.state.claims_identity)
            return bridge_app.JSONResponse({}, status_code=202)

        with (
            patch.object(bridge_app, "channel_authentication", auth),
            patch.object(bridge_app, "start_agent_process", side_effect=process),
        ):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bridge_app.app), base_url="https://bridge") as client:
                url = "/api/messages"
                payload = json.loads(activity(DEMO["DEMO_SERVICE_URL"]).model_dump_json(by_alias=True, exclude_none=True))
                denied = await client.post(url, json=payload)
                accepted = await client.post(url, json=payload, headers={"X-Demo-Channel-Key": DEMO["DEMO_CHANNEL_KEY"]})
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0].authentication_type, "Anonymous")

    async def test_demo_connector_factory_never_acquires_outbound_token_including_proactive(self):
        manager = SimpleNamespace(get_token_provider=unittest.mock.Mock(side_effect=AssertionError("Must not acquire a token")))
        factory = ChannelServiceClientFactory(manager, demo_service_url=DEMO["DEMO_SERVICE_URL"])
        anonymous = ClaimsIdentity({}, False, authentication_type="Anonymous")
        context = TurnContext(bridge_app.agent365_adapter, activity(DEMO["DEMO_SERVICE_URL"]), anonymous)
        connector = await factory.create_connector_client(context, anonymous, DEMO["DEMO_SERVICE_URL"], "audience")
        user_tokens = await factory.create_user_token_client(context, anonymous)
        try:
            self.assertNotIn("Authorization", connector.client.headers)
            with self.assertRaises(PermissionError):
                await factory.create_connector_client(context, anonymous, "https://other.example", "audience")
        finally:
            await connector.close()
            await user_tokens.close()

    async def test_standard_proactive_without_saved_claims_uses_configured_channel_scope(self):
        with patch.dict(os.environ, STANDARD, clear=True):
            auth = ChannelAuthentication.from_environment()
        manager = MsalConnectionManager(**auth.configuration)
        factory = ChannelServiceClientFactory(manager)
        identity = ClaimsIdentity({"aud": AGENT, "appid": AGENT}, False)
        context = TurnContext(bridge_app.agent365_adapter, activity(), identity)
        with patch.object(manager.get_default_connection(), "get_access_token", AsyncMock(return_value="local-token")) as acquire:
            user_tokens = await factory.create_user_token_client(context, identity)
            connector = await factory.create_connector_client(context, identity, "https://simulator.example", identity.get_token_audience())
            try:
                acquire.assert_awaited_once_with(identity.get_token_audience(), [CHANNEL + "/.default"])
                self.assertEqual(connector.client.headers["Authorization"], "Bearer local-token")
            finally:
                await user_tokens.close()
                await connector.close()

    async def test_channel_update_omits_null_reference_fields_and_uses_json_dates(self):
        outgoing = activity()
        outgoing.id = "update-1"
        outgoing.locale = None
        outgoing.timestamp = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
        observed = []

        class Response:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            def raise_for_status(self):
                pass

            async def json(self):
                return {"id": "update-1"}

        def put(path, **kwargs):
            observed.append(kwargs["json"])
            return Response()

        context = SimpleNamespace(turn_state={"ConnectorClient": SimpleNamespace(client=SimpleNamespace(put=put))})
        await ChannelCloudAdapter().update_activity(context, outgoing)
        self.assertNotIn("locale", observed[0])
        self.assertIsInstance(observed[0]["timestamp"], str)

    async def test_demo_proactive_preserves_anonymous_and_rejects_stale_destination(self):
        with patch.dict(os.environ, DEMO, clear=True):
            incoming = activity(DEMO["DEMO_SERVICE_URL"])
            context = TurnContext(bridge_app.agent365_adapter, incoming)
            stored = delivery_reference_metadata(context, worker_id="agent-1", boundary="one_to_one")
            sent = []

            async def continuation(identity, outgoing, callback):
                self.assertEqual(identity.authentication_type, "Anonymous")
                sent.append(outgoing.service_url)
                await callback(SimpleNamespace(send_activity=AsyncMock(return_value={"id": "reply-1"})))

            adapter = SimpleNamespace(continue_conversation_with_claims=continuation)
            result = await send_proactive_activity(adapter, stored, "scheduled")
            self.assertEqual(result["activityId"], "reply-1")
            self.assertEqual(sent, [DEMO["DEMO_SERVICE_URL"]])
            stored["conversation"]["conversation_reference"]["serviceUrl"] = "https://stale.example"
            with self.assertRaisesRegex(RuntimeError, "DEMO_SERVICE_URL"):
                await send_proactive_activity(adapter, stored, "scheduled")

    async def test_demo_generated_file_upload_uses_fresh_connector_and_preserves_key_path(self):
        with patch.dict(os.environ, DEMO, clear=True):
            incoming = activity(DEMO["DEMO_SERVICE_URL"])
            original = TurnContext(bridge_app.agent365_adapter, incoming)
            stored = delivery_reference_metadata(original, worker_id="agent-1", boundary="one_to_one")
            data = b"Native generated test bytes"
            uploaded, delivered = [], []

            class Upload:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    return False

                def raise_for_status(self):
                    pass

                async def json(self):
                    return {"id": "generated-file-1"}

            def post(path, **kwargs):
                uploaded.append((path, kwargs["json"]))
                return Upload()

            async def send(outgoing):
                delivered.append(outgoing)
                return {"id": "generated-reply-1"}

            async def continuation(identity, outgoing, callback):
                self.assertEqual(identity.authentication_type, "Anonymous")
                connector = SimpleNamespace(base_uri=DEMO["DEMO_SERVICE_URL"], client=SimpleNamespace(post=post))
                fresh = SimpleNamespace(
                    activity=incoming, adapter=SimpleNamespace(),
                    turn_state={"ConnectorClient": connector}, send_activity=send,
                )
                await callback(fresh)

            result = await send_proactive_activity(
                SimpleNamespace(continue_conversation_with_claims=continuation), stored,
                file=GeneratedAttachment("report.txt", "text/plain", data),
            )
            self.assertEqual(result["activityId"], "generated-reply-1")
            self.assertEqual(base64.b64decode(uploaded[0][1]["originalBase64"]), data)
            self.assertEqual(uploaded[0][1]["name"], "report.txt")
            self.assertEqual(
                delivered[0].attachments[0].content_url,
                DEMO["DEMO_SERVICE_URL"] + "/v3/attachments/generated-file-1/views/original",
            )

    def test_demo_fails_startup_when_incomplete_short_or_combined_with_entra(self):
        cases = [
            {"DEMO_CHANNEL_KEY": "short", "DEMO_SERVICE_URL": DEMO["DEMO_SERVICE_URL"]},
            {"DEMO_CHANNEL_KEY": DEMO["DEMO_CHANNEL_KEY"]},
            {"DEMO_SERVICE_URL": DEMO["DEMO_SERVICE_URL"]},
            {**DEMO, "USE_AGENTIC_AUTH": "true"},
            {**DEMO, f"{SERVICE_SETTINGS}CLIENTID": AGENT},
            {**DEMO, "DEMO_SERVICE_URL": DEMO["DEMO_SERVICE_URL"].replace("https:", "http:")},
            {**DEMO, "DEMO_SERVICE_URL": DEMO["DEMO_SERVICE_URL"] + "?x=1"},
        ]
        for environment in cases:
            with self.subTest(environment=list(environment)):
                with patch.dict(os.environ, environment, clear=True):
                    with self.assertRaises(ValueError):
                        ChannelAuthentication.from_environment()

    def test_agent365_and_unconfigured_modes_keep_existing_selection(self):
        for environment, expected in (({}, "anonymous"), ({"USE_AGENTIC_AUTH": "true"}, "agent365")):
            with patch.dict(os.environ, environment, clear=True):
                self.assertEqual(ChannelAuthentication.from_environment().mode, expected)

    def test_simulator_never_claims_agent_identity_even_with_stale_agent365_ids(self):
        with patch.dict(os.environ, {
            **STANDARD, "AGENT365_AGENT_IDENTITY_CLIENT_ID": "stale-instance",
            "AGENT365_AGENT_USER_ID": "stale-user",
        }, clear=True):
            boundary = bridge_app.agent_auth_context(activity())
        self.assertEqual(boundary.selected_mode, "none")
        self.assertEqual(boundary.available_modes, ())
        self.assertEqual(boundary.agent_instance_id, "")
        self.assertEqual(boundary.invoking_user_id, "owner-1")


if __name__ == "__main__":
    unittest.main()
