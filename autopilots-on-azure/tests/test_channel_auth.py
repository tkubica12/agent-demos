from __future__ import annotations

import json
import asyncio
import base64
import os
import time
import unittest
from contextlib import ExitStack, asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException, Request
from microsoft_agents.activity import Activity, ChannelAccount, ConversationAccount, InvokeResponse, ResourceResponse
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.core import AgentApplication, ClaimsIdentity, MemoryStorage, TurnContext

from bridge import app as bridge_app
from bridge.channel_auth import ChannelAuthentication, ChannelCloudAdapter, ChannelServiceClientFactory, SERVICE_SETTINGS
from bridge.proactive_delivery import delivery_reference_metadata, send_proactive_activity
from bridge.generated_files import GeneratedAttachment
from bridge.runtime.base import AgentRequest, AgentResponse
from bridge.runtime.hermes import HermesRuntimeAdapter
from bridge import work_activity


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

    async def test_demo_non_message_sdk_route_does_not_require_bearer(self):
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
                payload["type"] = "event"
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


class SimulatorMessageAcknowledgementTests(unittest.IsolatedAsyncioTestCase):
    @asynccontextmanager
    async def channel(self, mode="demo", handler=None):
        release = asyncio.Event()
        admitted = asyncio.Event()
        requests, replies, contexts = [], [], []
        environment = {
            **(DEMO if mode == "demo" else STANDARD),
            "USE_AGENTIC_AUTH": "false", "WORKER_ID": "agent-4",
            "GENERATED_CHAT_FILES_ENABLED": "false",
        }

        async def native(outgoing):
            requests.append(outgoing)
            if outgoing.url.path == "/api/sessions" and outgoing.method == "GET":
                return httpx.Response(200, json={"data": []})
            if outgoing.url.path == "/api/sessions" and outgoing.method == "POST":
                return httpx.Response(201, json={"session": {"id": json.loads(outgoing.content)["id"]}})
            if outgoing.url.path == "/v1/runs":
                admitted.set()
                return httpx.Response(202, json={"run_id": "run-held"})
            if outgoing.url.path == "/v1/runs/run-held":
                await release.wait()
                return httpx.Response(200, json={"run_id": "run-held", "status": "completed", "output": "Later reply"})
            return httpx.Response(404, json={"error": "missing"})

        runtime = HermesRuntimeAdapter(client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(native), **kwargs,
        ))

        async def invoke(**kwargs):
            result = await runtime._native_run("https://native.example", "test-key", AgentRequest(
                prompt=kwargs["message"], conversation_id=kwargs["conversation_id"],
                user_id=kwargs["user_id"], source=kwargs["source"], must_answer=True,
                metadata=kwargs["metadata"],
            ))
            return AgentResponse(result["output"])

        async def send(*args):
            replies.append(args[-1])
            return ResourceResponse(id="reply-1")

        connector = SimpleNamespace(
            conversations=SimpleNamespace(send_to_conversation=send, reply_to_activity=send),
            close=AsyncMock(),
        )
        user_tokens = SimpleNamespace(close=AsyncMock())

        async def create_connector(context, identity, service_url, audience, *args):
            contexts.append((context, identity, service_url, audience))
            return connector

        factory = SimpleNamespace(
            create_user_token_client=AsyncMock(return_value=user_tokens),
            create_connector_client=AsyncMock(side_effect=create_connector),
        )
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, environment, clear=True))
            auth = ChannelAuthentication.from_environment()
            manager = MsalConnectionManager(**auth.configuration) if mode == "standard" else None
            if mode == "standard":
                stack.enter_context(patch.object(
                    auth.validator, "_get_public_key_or_secret",
                    AsyncMock(return_value=ChannelAuthenticationTests.key.public_key()),
                ))
            adapter = ChannelCloudAdapter(channel_service_client_factory=factory)
            agent = AgentApplication(storage=MemoryStorage(), adapter=adapter, start_typing_timer=False)
            agent.activity("message")(handler or bridge_app.handle_teams_message)
            agent.activity("invoke")(handler or bridge_app.handle_teams_invoke)
            for name, value in {
                "channel_authentication": auth, "channel_connection_manager": manager,
                "agent365_adapter": adapter, "agent365_app": agent,
            }.items():
                stack.enter_context(patch.object(bridge_app, name, value))
            stack.enter_context(patch.object(bridge_app, "invoke_agent_runtime", side_effect=invoke))
            stack.enter_context(patch.object(bridge_app, "supports_typing_indicators", return_value=False))
            stack.enter_context(patch.object(bridge_app.durable_transport, "enabled", return_value=False))
            stack.enter_context(patch.dict(bridge_app._teams_memory, {}, clear=True))
            incoming = activity(DEMO["DEMO_SERVICE_URL"] if mode == "demo" else "https://simulator.example")
            incoming.reply_to_id = "parent-message"
            incoming.channel_data = {"tenant": {"id": TENANT}}
            headers = (
                {"X-Demo-Channel-Key": DEMO["DEMO_CHANNEL_KEY"]} if mode == "demo"
                else {"Authorization": "Bearer " + ChannelAuthenticationTests().token()}
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=bridge_app.app), base_url="https://bridge",
            ) as client:
                try:
                    yield SimpleNamespace(
                        client=client, headers=headers, payload=json.loads(incoming.model_dump_json(by_alias=True, exclude_none=True)),
                        release=release, admitted=admitted, requests=requests, replies=replies,
                        contexts=contexts, factory=factory, connector=connector, user_tokens=user_tokens,
                    )
                finally:
                    release.set()
                    if bridge_app.background_turns:
                        await asyncio.gather(*list(bridge_app.background_turns), return_exceptions=True)
                    await asyncio.sleep(0)

    async def test_slow_simulator_turn_acks_before_completion_and_delivers_once_with_original_scope(self):
        for mode in ("demo", "standard"):
            with self.subTest(mode=mode):
                async with self.channel(mode) as channel:
                    response = await asyncio.wait_for(
                        channel.client.post("/api/messages", json=channel.payload, headers=channel.headers), timeout=1,
                    )
                    self.assertEqual(response.status_code, 202)
                    await asyncio.wait_for(channel.admitted.wait(), timeout=1)
                    self.assertEqual(work_activity.active_work_count(), 1)
                    self.assertEqual(channel.replies, [])
                    context, identity, service_url, _ = channel.contexts[0]
                    self.assertEqual(context.activity.type, "message")
                    self.assertEqual(context.activity.id, "message-1")
                    self.assertEqual(context.activity.reply_to_id, "parent-message")
                    self.assertEqual(context.activity.channel_data, channel.payload["channelData"])
                    self.assertEqual(context.activity.conversation.id, "conversation-1")
                    self.assertEqual(context.activity.from_property.aad_object_id, "owner-1")
                    self.assertEqual(context.activity.recipient.id, f"28:{AGENT}")
                    self.assertEqual(service_url, channel.payload["serviceUrl"])
                    if mode == "standard":
                        self.assertTrue(identity.is_authenticated)
                        self.assertEqual(identity.claims["azp"], CHANNEL)
                        self.assertEqual(identity.claims["tid"], TENANT)
                    else:
                        self.assertEqual(identity.authentication_type, "Anonymous")
                    channel.release.set()
                    await asyncio.wait_for(asyncio.gather(*list(bridge_app.background_turns)), timeout=1)
                    await asyncio.sleep(0)
                    self.assertEqual(work_activity.active_work_count(), 0)
                    self.assertEqual([reply.text for reply in channel.replies], ["Later reply"])
                    self.assertEqual(sum(r.method == "POST" and r.url.path == "/v1/runs" for r in channel.requests), 1)
                    channel.connector.close.assert_awaited_once()
                    channel.user_tokens.close.assert_awaited_once()

    async def test_auth_maintenance_and_incomplete_references_schedule_nothing(self):
        async with self.channel() as channel:
            response = await channel.client.post("/api/messages", json=channel.payload)
            self.assertEqual(response.status_code, 401)
            response = await channel.client.post("/api/messages", json={
                **channel.payload, "serviceUrl": "https://other.example",
            }, headers=channel.headers)
            self.assertEqual(response.status_code, 403)
            with patch.object(work_activity.Path, "exists", return_value=True):
                response = await channel.client.post("/api/messages", json=channel.payload, headers=channel.headers)
            self.assertEqual(response.status_code, 503)
            for field in ("id", "from", "recipient", "conversation"):
                payload = json.loads(json.dumps(channel.payload))
                if field == "id":
                    payload["id"] = ""
                else:
                    payload[field]["id"] = ""
                response = await channel.client.post("/api/messages", json=payload, headers=channel.headers)
                self.assertEqual(response.status_code, 400)
            channel.factory.create_connector_client.assert_not_awaited()
            self.assertEqual(channel.requests, [])
            self.assertEqual(work_activity.active_work_count(), 0)
            self.assertEqual(bridge_app.background_turns, set())

    async def test_invokes_keep_synchronous_sdk_response_body(self):
        bodies = {
            "htmlwidget/calltool": {"content": [{"type": "text", "text": "tool result"}], "isError": False},
            "adaptiveCard/action": {"statusCode": 200, "type": "application/vnd.microsoft.card.adaptive",
                                    "value": {"type": "AdaptiveCard", "version": "1.5", "body": []}},
        }
        for name, body in bodies.items():
            with self.subTest(name=name):
                async def handler(context, _state):
                    await context.send_activity(Activity(type="invokeResponse", value=InvokeResponse(status=200, body=body)))

                async with self.channel(handler=handler) as channel:
                    response = await channel.client.post("/api/messages", json={
                        **channel.payload, "type": "invoke", "name": name, "value": {"data": "submission"},
                    }, headers=channel.headers)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json(), body)
                    self.assertEqual(bridge_app.background_turns, set())
                    self.assertEqual(channel.requests, [])

    async def test_message_card_submissions_and_expect_replies_are_not_detached(self):
        for overrides in ({"value": {"token": "signed-card-action", "choice": "yes"}},
                          {"deliveryMode": "expectReplies"}):
            with self.subTest(overrides=overrides):
                entered, release = asyncio.Event(), asyncio.Event()

                async def handler(context, _state):
                    entered.set()
                    await release.wait()
                    await context.send_activity("Submission accepted")

                async with self.channel(handler=handler) as channel:
                    pending = asyncio.create_task(channel.client.post(
                        "/api/messages", json={**channel.payload, **overrides}, headers=channel.headers,
                    ))
                    try:
                        await asyncio.wait_for(entered.wait(), timeout=1)
                        self.assertFalse(pending.done())
                        self.assertEqual(bridge_app.background_turns, set())
                    finally:
                        release.set()
                    response = await asyncio.wait_for(pending, timeout=1)
                    self.assertEqual(response.status_code, 200 if "deliveryMode" in overrides else 202)
                    if "deliveryMode" in overrides:
                        self.assertEqual(response.json()["activities"][0]["text"], "Submission accepted")
                    else:
                        self.assertEqual([reply.text for reply in channel.replies], ["Submission accepted"])
                    self.assertEqual(channel.requests, [])

    async def test_background_setup_failure_is_logged_and_releases_active_work(self):
        async with self.channel() as channel:
            channel.factory.create_connector_client.side_effect = RuntimeError("Connector unavailable")
            with self.assertLogs("bridge.work_activity", level="ERROR") as logs:
                response = await channel.client.post("/api/messages", json=channel.payload, headers=channel.headers)
                self.assertEqual(response.status_code, 202)
                await asyncio.gather(*list(bridge_app.background_turns), return_exceptions=True)
                await asyncio.sleep(0)
            self.assertIn("Connector unavailable", "\n".join(logs.output))
            self.assertEqual(work_activity.active_work_count(), 0)
            self.assertEqual(channel.requests, [])

    async def test_shutdown_waits_for_accepted_turn_before_flushing_telemetry(self):
        async with self.channel() as channel:
            response = await channel.client.post("/api/messages", json=channel.payload, headers=channel.headers)
            self.assertEqual(response.status_code, 202)
            await asyncio.wait_for(channel.admitted.wait(), timeout=1)
            with patch.object(bridge_app, "flush_telemetry") as flush:
                shutdown = asyncio.create_task(bridge_app.flush_bridge_telemetry())
                await asyncio.sleep(0)
                self.assertFalse(shutdown.done())
                flush.assert_not_called()
                self.assertEqual(work_activity.active_work_count(), 1)
                channel.release.set()
                await asyncio.wait_for(shutdown, timeout=1)
                flush.assert_called_once()
            self.assertEqual(work_activity.active_work_count(), 0)
            self.assertEqual([reply.text for reply in channel.replies], ["Later reply"])


if __name__ == "__main__":
    unittest.main()
