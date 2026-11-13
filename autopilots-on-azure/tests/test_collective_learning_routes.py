import asyncio
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from bridge import app as bridge_app
from bridge import operator_auth
from bridge.runtime.hermes import HermesRuntimeAdapter


class CollectiveLearningRouteTests(unittest.TestCase):
    tenant_id = "11111111-1111-1111-1111-111111111111"
    owner_id = "22222222-2222-2222-2222-222222222222"
    audience = "33333333-3333-3333-3333-333333333333"
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        environment = patch.dict(os.environ, {
            "API_SERVER_KEY": "runtime-key",
            "AGENT365_TENANT_ID": self.tenant_id,
            "WORKER_OWNER_ID": self.owner_id,
            "COLLECTIVE_LEARNING_OPERATOR_CLIENT_ID": self.audience,
        })
        environment.start()
        self.addCleanup(environment.stop)
        keys = SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=self.key.public_key()))
        signing_keys = patch.object(operator_auth, "_signing_keys", return_value=keys)
        signing_keys.start()
        self.addCleanup(signing_keys.stop)

    def token(self, **overrides):
        return jwt.encode({
            "iss": f"https://login.microsoftonline.com/{self.tenant_id}/v2.0",
            "aud": self.audience,
            "iat": int(time.time()) - 1,
            "nbf": int(time.time()) - 1,
            "exp": int(time.time()) + 300,
            "tid": self.tenant_id,
            "oid": self.owner_id,
            "azp": operator_auth.AZURE_CLI_CLIENT_ID,
            "ver": "2.0",
            "scp": operator_auth.OPERATOR_SCOPE,
            **overrides,
        }, self.key, algorithm="RS256")

    def test_operator_routes_require_human_identity_and_derive_receipt_principal(self):
        adapter = SimpleNamespace(
            runtime_kind="hermes",
            prepare_collective_learning=AsyncMock(return_value={"packetDigest": "a" * 64}),
            pending_collective_learning=AsyncMock(return_value={"packetDigest": "a" * 64}),
            prepare_refresh_rejection=AsyncMock(return_value={"dispositionDigest": "b" * 64}),
            reject_and_refresh=AsyncMock(return_value={"accepted": True}),
            approve_collective_learning=AsyncMock(return_value={"approved": True}),
            export_collective_learning=AsyncMock(return_value={"packet": {}}),
        )
        body = {"dispositionDigest": "b" * 64, "reason": "Not reusable"}
        headers = {"Authorization": f"Bearer {self.token()}"}

        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bridge_app.app), base_url="http://bridge") as client:
                for method, path, payload in (
                    ("POST", "prepare", {}),
                    ("GET", "pending", None), ("POST", "prepare-rejection", {}),
                    ("POST", "reject", body),
                    ("POST", "approve", {"packetDigest": "a" * 64}),
                    ("GET", "export", None),
                ):
                    url = "/internal/collective-learning/" + path
                    denied = await client.request(method, url, json=payload)
                    self.assertEqual(denied.status_code, 401)
                    runtime = await client.request(method, url, json=payload, headers={"X-Autopilot-Key": "runtime-key"})
                    self.assertEqual(runtime.status_code, 401)
                    allowed = await client.request(method, url, json=payload, headers=headers)
                    self.assertEqual(allowed.status_code, 200, allowed.text)
                forged = await client.post("/internal/collective-learning/approve",
                                           json={"packetDigest": "a" * 64, "approvedBy": "someone-else"}, headers=headers)
                self.assertEqual(forged.status_code, 422)
                forged_rejection = await client.post("/internal/collective-learning/reject",
                                                     json={**body, "rejectedBy": "someone-else"}, headers=headers)
                self.assertEqual(forged_rejection.status_code, 422)
                adapter.reject_and_refresh.side_effect = ValueError("Disposition changed")
                conflict = await client.post("/internal/collective-learning/reject", json=body,
                                             headers=headers)
                self.assertEqual(conflict.status_code, 409)
                adapter.approve_collective_learning.side_effect = ValueError("Approved digest does not match")
                conflict = await client.post("/internal/collective-learning/approve",
                                             json={"packetDigest": "c" * 64}, headers=headers)
                self.assertEqual(conflict.status_code, 409)

        with patch.object(bridge_app, "runtime_adapter", return_value=adapter):
            asyncio.run(run())
        adapter.pending_collective_learning.assert_awaited_once()
        adapter.prepare_refresh_rejection.assert_awaited_once()
        self.assertEqual(adapter.reject_and_refresh.await_count, 2)
        adapter.reject_and_refresh.assert_awaited_with(
            disposition_digest=body["dispositionDigest"],
            rejected_by=f"entra:{self.tenant_id}:{self.owner_id}", reason=body["reason"],
        )
        self.assertEqual(adapter.approve_collective_learning.await_args_list[0].kwargs,
                         {"packet_digest": "a" * 64, "approved_by": f"entra:{self.tenant_id}:{self.owner_id}"})
        adapter.export_collective_learning.assert_awaited_once()

    def test_operator_tokens_reject_untrusted_or_non_owner_principals_before_runtime_access(self):
        invalid = (
            (self.token(aud="different-api"), 401),
            (self.token(iss="https://attacker.example"), 401),
            (self.token(exp=int(time.time()) - 10), 401),
            (self.token(tid="other-tenant"), 403),
            (self.token(oid="agent-user"), 403),
            (self.token(azp="runtime-managed-identity"), 403),
            (self.token(scp="Other.Scope"), 403),
            (self.token(scp=["CollectiveLearning.Review"]), 403),
            (self.token(idtyp="app"), 403),
            (self.token(ver="1.0"), 403),
            (self.token() + "invalid", 401),
            ("not-a-token", 401),
        )

        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bridge_app.app), base_url="http://bridge") as client:
                for token, status in invalid:
                    with self.subTest(token=token[:10], status=status):
                        response = await client.post("/internal/collective-learning/approve",
                                                     json={"packetDigest": "a" * 64},
                                                     headers={"Authorization": f"Bearer {token}", "X-Autopilot-Key": "runtime-key"})
                        self.assertEqual(response.status_code, status, response.text)

        with patch.object(bridge_app, "runtime_adapter") as adapter:
            asyncio.run(run())
            adapter.assert_not_called()

    def test_unprepared_pending_packet_keeps_the_explicit_runtime_error(self):
        requests = []

        def runtime(request):
            requests.append(request)
            if request.url.path == "/internal/collective-learning/pending":
                return httpx.Response(400, json={"detail": "Prepare the Learning Packet before approval."})
            return httpx.Response(200, json={})

        adapter = HermesRuntimeAdapter(
            runtime_url_factory=lambda: "https://runtime.example",
            client_factory=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(runtime), **kwargs),
        )

        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bridge_app.app), base_url="http://bridge") as client:
                response = await client.get("/internal/collective-learning/pending",
                                            headers={"Authorization": f"Bearer {self.token()}"})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), {"detail": {
                    "source": "runtime", "statusCode": 400,
                    "message": "Prepare the Learning Packet before approval.",
                }})

        with patch.object(bridge_app, "runtime_adapter", return_value=adapter):
            asyncio.run(run())
        pending = [request for request in requests if request.url.path.endswith("/collective-learning/pending")]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].headers["X-Autopilot-Key"], "runtime-key")

    def test_collective_routes_keep_runtime_auth_server_and_transport_failures_explicit(self):
        upstream_request = httpx.Request("GET", "https://runtime.example/internal/collective-learning/pending")
        unauthorized = httpx.Response(401, json={"detail": "Runtime key was rejected."}, request=upstream_request)
        forbidden = httpx.Response(403, json={"detail": "Runtime access was forbidden."}, request=upstream_request)
        unavailable = httpx.Response(503, text="<html>upstream unavailable</html>", request=upstream_request)
        failures = [
            (httpx.HTTPStatusError("unauthorized", request=upstream_request, response=unauthorized), 401, 401),
            (httpx.HTTPStatusError("forbidden", request=upstream_request, response=forbidden), 403, 403),
            (httpx.HTTPStatusError("unavailable", request=upstream_request, response=unavailable), 502, 503),
            (httpx.ConnectError("unreachable", request=upstream_request), 502, None),
            (httpx.ReadTimeout("timeout", request=upstream_request), 504, None),
        ]
        operations = (
            ("prepare_collective_learning", "POST", "prepare", {}),
            ("pending_collective_learning", "GET", "pending", None),
            ("prepare_refresh_rejection", "POST", "prepare-rejection", {}),
            ("reject_and_refresh", "POST", "reject", {"dispositionDigest": "b" * 64, "reason": "Keep local"}),
            ("approve_collective_learning", "POST", "approve", {"packetDigest": "a" * 64}),
            ("export_collective_learning", "GET", "export", None),
        )
        adapter = SimpleNamespace(**{name: AsyncMock() for name, *_ in operations})
        headers = {"Authorization": f"Bearer {self.token()}"}

        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bridge_app.app), base_url="http://bridge") as client:
                for failure, expected, upstream_status in failures:
                    for name, method, path, body in operations:
                        with self.subTest(path=path, failure=type(failure).__name__, status=upstream_status):
                            getattr(adapter, name).side_effect = failure
                            response = await client.request(method, "/internal/collective-learning/" + path,
                                                            json=body, headers=headers)
                            self.assertEqual(response.status_code, expected, response.text)
                            detail = response.json()["detail"]
                            self.assertEqual(detail["source"], "runtime")
                            self.assertEqual(detail.get("statusCode"), upstream_status)
                            self.assertNotIn("<html>", detail["message"])

        with patch.object(bridge_app, "runtime_adapter", return_value=adapter):
            asyncio.run(run())

    def test_missing_operator_configuration_fails_closed_even_with_a_token(self):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bridge_app.app), base_url="http://bridge") as client:
                response = await client.get("/internal/collective-learning/export",
                                            headers={"Authorization": f"Bearer {self.token()}"})
                self.assertEqual(response.status_code, 503)

        with patch.dict(os.environ, {"COLLECTIVE_LEARNING_OPERATOR_CLIENT_ID": ""}), patch.object(bridge_app, "runtime_adapter") as adapter:
            asyncio.run(run())
            adapter.assert_not_called()

    def test_gateway_health_exposes_diagnostics_only_with_the_runtime_operations_key(self):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bridge_app.app), base_url="http://bridge") as client:
                public = await client.get("/health")
                self.assertEqual(public.status_code, 200)
                self.assertEqual(set(public.json()), {"status", "release"})
                denied = await client.get("/health/detailed")
                self.assertEqual(denied.status_code, 401)
                detailed = await client.get("/health/detailed", headers={"X-Autopilot-Key": "runtime-key"})
                self.assertEqual(detailed.status_code, 200)
                self.assertIn("processId", detailed.json())
                self.assertIn("memoryNonce", detailed.json())
        asyncio.run(run())
