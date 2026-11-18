import json
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from bridge import chat_ingress


TENANT = "11111111-1111-1111-1111-111111111111"
BLUEPRINT = "22222222-2222-2222-2222-222222222222"
CHAT_CLIENT = "33333333-3333-3333-3333-333333333333"
USER = "44444444-4444-4444-4444-444444444444"
SERVICE_URL = "https://chat.example.test/connector"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(key=KEY, **overrides):
    claims = {
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "aud": BLUEPRINT,
        "iat": int(time.time()) - 1,
        "nbf": int(time.time()) - 1,
        "exp": int(time.time()) + 300,
        "tid": TENANT,
        "oid": USER,
        "azp": CHAT_CLIENT,
        "scp": "access_agent",
        "name": "Chat User",
        **overrides,
    }
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm="RS256")


class ChatIngressTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {
            "AGENT365_TENANT_ID": TENANT,
            "AGENT365_BLUEPRINT_CLIENT_ID": BLUEPRINT,
            "CHAT_CLIENT_IDS": f" {CHAT_CLIENT.upper()} ",
            "CHAT_SERVICE_URLS": SERVICE_URL,
        })
        environment.start()
        self.addCleanup(environment.stop)
        keys = SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=KEY.public_key()))
        signing_keys = patch.object(chat_ingress, "_signing_keys", return_value=keys)
        signing_keys.start()
        self.addCleanup(signing_keys.stop)

    async def post(self, activity=None, *, bearer="", path="/api/messages", method="POST", raw=None):
        forwarded = []
        sent = []

        async def app(scope, receive, send):
            body = b""
            while True:
                message = await receive()
                body += message.get("body", b"")
                if not message.get("more_body"):
                    break
            forwarded.append(body)
            await send({"type": "http.response.start", "status": 202, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        body = raw if raw is not None else json.dumps(activity if activity is not None else self.activity()).encode()
        chunks = [body[:10], body[10:]]

        async def receive():
            chunk = chunks.pop(0)
            return {"type": "http.request", "body": chunk, "more_body": bool(chunks)}

        async def send(message):
            sent.append(message)

        headers = [(b"authorization", f"Bearer {bearer}".encode())] if bearer else []
        scope = {"type": "http", "method": method, "path": path, "headers": headers}
        await chat_ingress.ChatIngressMiddleware(app)(scope, receive, send)
        return sent[0]["status"], (json.loads(forwarded[0]) if forwarded else None)

    @staticmethod
    def activity(**overrides):
        return {
            "type": "message",
            "text": "status projektu P104667",
            "serviceUrl": SERVICE_URL,
            "conversation": {"id": "c1", "conversationType": "personal"},
            "from": {"id": "claimed", "aadObjectId": "55555555-5555-5555-5555-555555555555", "name": "Somebody Else"},
            **overrides,
        }

    async def test_valid_chat_token_binds_sender_to_token_identity(self):
        status, forwarded = await self.post(bearer=token())
        self.assertEqual(status, 202)
        self.assertEqual(forwarded["from"], {"id": USER, "aadObjectId": USER, "name": "Chat User"})
        self.assertEqual(forwarded["text"], "status projektu P104667")
        self.assertEqual(forwarded["channelData"]["tenant"]["id"], TENANT)

    async def test_version_one_token_shape_is_accepted(self):
        status, forwarded = await self.post(bearer=token(
            iss=f"https://sts.windows.net/{TENANT}/", aud=f"api://{BLUEPRINT}", azp=None, appid=CHAT_CLIENT,
        ))
        self.assertEqual(status, 202)
        self.assertEqual(forwarded["from"]["aadObjectId"], USER)

    async def test_missing_or_malformed_token_is_rejected(self):
        self.assertEqual((await self.post())[0], 401)
        self.assertEqual((await self.post(bearer="not-a-token"))[0], 401)
        self.assertEqual((await self.post(bearer=token(key=OTHER_KEY)))[0], 401)
        self.assertEqual((await self.post(bearer=token(exp=int(time.time()) - 600)))[0], 401)

    async def test_wrong_audience_issuer_or_tenant_is_rejected(self):
        other = "99999999-9999-9999-9999-999999999999"
        for overrides in (
            {"aud": other},
            {"iss": f"https://login.microsoftonline.com/{other}/v2.0"},
            {"iss": "https://api.botframework.com"},
            {"tid": other},
        ):
            with self.subTest(overrides):
                status, forwarded = await self.post(bearer=token(**overrides))
                self.assertEqual(status, 401)
                self.assertIsNone(forwarded)

    async def test_token_without_chat_scope_client_or_user_is_forbidden(self):
        for overrides in (
            {"scp": "User.Read"},
            {"scp": None, "roles": ["access_agent"]},
            {"azp": "99999999-9999-9999-9999-999999999999"},
            {"idtyp": "app"},
            {"oid": None},
        ):
            with self.subTest(overrides):
                status, forwarded = await self.post(bearer=token(**overrides))
                self.assertIn(status, (401, 403))
                self.assertIsNone(forwarded)

    async def test_reply_address_must_be_the_configured_chat_service(self):
        for service_url in (
            "https://attacker.example/connector",
            "https://chat.example.test.attacker.example/connector",
            "https://chat.example.test/connector-evil",
            "http://chat.example.test/connector",
            "",
        ):
            with self.subTest(service_url):
                status, forwarded = await self.post(self.activity(serviceUrl=service_url), bearer=token())
                self.assertEqual(status, 403)
                self.assertIsNone(forwarded)
        status, _ = await self.post(self.activity(serviceUrl=SERVICE_URL + "/v3/"), bearer=token())
        self.assertEqual(status, 202)

    async def test_invalid_activity_body_is_rejected(self):
        self.assertEqual((await self.post(raw=b"not json", bearer=token()))[0], 400)
        self.assertEqual((await self.post(raw=b"[1]", bearer=token()))[0], 400)

    async def test_chat_mode_without_full_configuration_fails_closed(self):
        for missing in ("CHAT_SERVICE_URLS", "AGENT365_TENANT_ID", "AGENT365_BLUEPRINT_CLIENT_ID"):
            with self.subTest(missing), patch.dict(os.environ, {missing: ""}):
                status, forwarded = await self.post(bearer=token())
                self.assertEqual(status, 503)
                self.assertIsNone(forwarded)

    async def test_other_routes_and_workers_without_chat_are_untouched(self):
        status, forwarded = await self.post({"probe": True}, path="/health", method="GET")
        self.assertEqual((status, forwarded), (202, {"probe": True}))
        original = self.activity()
        with patch.dict(os.environ, {"CHAT_CLIENT_IDS": ""}):
            status, forwarded = await self.post(original)
        self.assertEqual((status, forwarded), (202, original))

    async def test_gateway_application_admits_only_verified_chat_turns(self):
        import httpx

        from bridge import app as bridge_app

        observed = []

        async def process(replay, *_args):
            observed.append(await replay.json())
            return None

        transport = httpx.ASGITransport(app=bridge_app.app)
        with patch.object(bridge_app, "start_agent_process", side_effect=process):
            async with httpx.AsyncClient(transport=transport, base_url="https://gateway.test") as client:
                rejected = await client.post("/api/messages", json=self.activity())
                forged = await client.post(
                    "/api/messages", json=self.activity(), headers={"Authorization": f"Bearer {token(key=OTHER_KEY)}"},
                )
                accepted = await client.post(
                    "/api/messages", json=self.activity(), headers={"Authorization": f"Bearer {token()}"},
                )
        self.assertEqual((rejected.status_code, forged.status_code, accepted.status_code), (401, 401, 200))
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]["from"]["aadObjectId"], USER)


if __name__ == "__main__":
    unittest.main()
