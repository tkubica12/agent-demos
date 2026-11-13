import asyncio
import base64
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import httpx
from aiohttp import web
from aiohttp.test_utils import TestServer

from bridge.runtime.base import AgentRequest, AgentResponse, DreamRequest
from bridge.runtime.hermes import DreamExecutionUncertainError, HermesRuntimeAdapter
from bridge import durable_transport, event_worker
from bridge.durable_transport import Journal


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "runtimes" / "hermes"))
import cron_runtime  # noqa: E402
import runtime_routes  # noqa: E402
from native_learning import LearningConflictError  # noqa: E402
from tests.native_hermes_fakes import FakeSkillLedger, FakeWriteApproval  # noqa: E402


class DreamRecoveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(directory.cleanup)
        self.profile = Path(directory.name)
        native = patch.dict(sys.modules, {
            "tools": types.ModuleType("tools"),
            "tools.skill_ledger": FakeSkillLedger(self.profile),
            "tools.write_approval": FakeWriteApproval(strict=False),
        })
        native.start()
        self.addCleanup(native.stop)
        environment = patch.dict(os.environ, {"API_SERVER_KEY": "operator-key"})
        environment.start()
        self.addCleanup(environment.stop)

    def create_operation(self, *, phase="pending"):
        receipt = cron_runtime._new_system_operation(
            {"id": "dream-job"}, "revision", "occurrence", "adhoc"
        )
        receipt.update({"state": "running", "phase": phase})
        cron_runtime.atomic_write_json(
            cron_runtime.system_schedule_receipts_path(self.profile),
            {"dream-job:occurrence": receipt},
        )
        return cron_runtime._system_operation(receipt, "dream-job")

    def with_runtime(self, scenario):
        async def run():
            app = web.Application()
            runtime_routes.register_routes(app, self.profile)
            server = TestServer(app)
            await server.start_server()
            try:
                return await scenario(str(server.make_url("")).rstrip("/"))
            finally:
                await server.close()

        return asyncio.run(run())

    @staticmethod
    def create_adapter(base_url, transport=None):
        adapter = HermesRuntimeAdapter(
            runtime_url_factory=lambda: base_url,
            client_factory=lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs),
        )
        adapter._wait_for_health = AsyncMock()
        return adapter

    def test_lost_digest_response_resumes_durable_agent_without_second_inference(self):
        operation = self.create_operation()
        request = DreamRequest(session_id=operation["sessionId"])
        inference_calls = []
        digest_calls = []
        real_digest = runtime_routes.learning_digest

        def digest(*args, **kwargs):
            digest_calls.append(True)
            if len(digest_calls) == 1:
                raise LearningConflictError("digest temporarily unavailable")
            return real_digest(*args, **kwargs)

        async def infer(_):
            current = cron_runtime.get_system_schedule_checkpoint(
                self.profile, job_id="dream-job", revision="revision", occurrence_id="occurrence",
            )
            self.assertEqual(current["phase"], "dream_started")
            inference_calls.append(True)
            return AgentResponse(text="Dream completed", raw={"runtimeUrl": "http://runtime.test"})

        async def scenario(base_url):
            first = self.create_adapter(base_url)
            with patch.object(first, "invoke", side_effect=infer):
                with self.assertRaises(httpx.HTTPStatusError):
                    await first.dream(request, operation=operation)
            second = self.create_adapter(base_url)
            with patch.object(second, "invoke", side_effect=infer):
                return await second.dream(request, operation=operation)

        with patch.object(runtime_routes, "learning_digest", side_effect=digest):
            result = self.with_runtime(scenario)
        self.assertEqual(inference_calls, [True])
        self.assertEqual(len(digest_calls), 2)
        self.assertEqual(result.agent.text, "Dream completed")
        self.assertEqual(result.learning_digest["changes"], [])

    def test_uncertain_dream_started_refuses_automatic_inference(self):
        operation = self.create_operation(phase="dream_started")

        async def scenario(base_url):
            adapter = self.create_adapter(base_url)
            with patch.object(adapter, "invoke", new_callable=AsyncMock) as inference:
                with self.assertRaises(DreamExecutionUncertainError) as error:
                    await adapter.dream(DreamRequest(session_id=operation["sessionId"]), operation=operation)
            inference.assert_not_awaited()
            return error.exception

        error = self.with_runtime(scenario)
        self.assertEqual(error.status, "uncertain")
        self.assertIn("explicitly start a new ad-hoc Dream", str(error))

    def test_started_dream_reattaches_known_native_run_without_new_inference(self):
        operation = self.create_operation(phase="dream_started")
        journal = Journal(self.profile / "gateway.db")
        journal.admit("schedule", {
            "MessageId": "dream-delivery", "LockToken": "lease", "Properties": {"DeliveryCount": 2},
        }, {"type": "system.dream"}, "connection")
        journal.update("schedule", "dream-delivery", native_run_id="known-dream-run")
        native_requests = []

        async def scenario(base_url):
            async with httpx.AsyncHTTPTransport() as real:
                async def transport(outgoing):
                    if outgoing.url.path.startswith("/v1/runs"):
                        native_requests.append((outgoing.method, outgoing.url.path))
                        self.assertEqual((outgoing.method, outgoing.url.path), ("GET", "/v1/runs/known-dream-run"))
                        return httpx.Response(200, json={
                            "run_id": "known-dream-run", "status": "completed", "output": "Recovered Dream",
                        })
                    return await real.handle_async_request(outgoing)

                adapter = self.create_adapter(base_url, httpx.MockTransport(transport))
                delivery_token = durable_transport.delivery_identity.set("dream-delivery")
                queue_token = durable_transport.delivery_queue.set("schedule")
                try:
                    with (
                        patch.object(durable_transport, "enabled", return_value=True),
                        patch.object(durable_transport, "journal", return_value=journal),
                        patch.object(event_worker, "checkpoint", new_callable=AsyncMock),
                        patch.object(adapter, "_resolve_transcript_id", return_value="known-transcript"),
                        patch.object(adapter, "_ensure_session", new_callable=AsyncMock),
                    ):
                        return await adapter.dream(
                            DreamRequest(session_id=operation["sessionId"]), operation=operation,
                        )
                finally:
                    durable_transport.delivery_identity.reset(delivery_token)
                    durable_transport.delivery_queue.reset(queue_token)

        result = self.with_runtime(scenario)
        self.assertEqual(result.agent.text, "Recovered Dream")
        self.assertEqual(native_requests, [("GET", "/v1/runs/known-dream-run")])
        self.assertEqual(
            cron_runtime.get_system_schedule_checkpoint(
                self.profile, job_id="dream-job", revision="revision", occurrence_id="occurrence",
            )["phase"], "dream_completed",
        )

    def test_lost_completed_checkpoint_ack_does_not_repeat_inference(self):
        operation = self.create_operation()
        request = DreamRequest(session_id=operation["sessionId"])
        lost = []
        inference = AsyncMock(return_value=AgentResponse(
            text="Completed before response loss", raw={"runtimeUrl": "http://runtime.test"},
        ))

        async def scenario(base_url):
            async with httpx.AsyncHTTPTransport() as real:

                async def transport(outgoing):
                    response = await real.handle_async_request(outgoing)
                    if (
                        outgoing.url.path == "/internal/cron/system/checkpoint"
                        and json.loads(outgoing.content)["phase"] == "dream_completed"
                        and not lost
                    ):
                        lost.append(True)
                        await response.aread()
                        await response.aclose()
                        raise httpx.ReadError("response lost after checkpoint commit", request=outgoing)
                    return response

                adapter = self.create_adapter(base_url, httpx.MockTransport(transport))
                with patch.object(adapter, "invoke", inference):
                    with self.assertRaises(httpx.ReadError):
                        await adapter.dream(request, operation=operation)
                    return await adapter.dream(request, operation=operation)

        result = self.with_runtime(scenario)
        inference.assert_awaited_once()
        self.assertEqual(result.agent.text, "Completed before response loss")

    def test_checkpoint_endpoint_rejects_stale_owner(self):
        operation = self.create_operation()

        async def scenario(base_url):
            with self.assertRaises(httpx.HTTPStatusError) as error:
                await self.create_adapter(base_url).checkpoint_system_schedule(
                    job_id="dream-job", revision="revision", occurrence_id="occurrence",
                    owner_token="stale", expected_phase="pending", phase="dream_started", payload={},
                )
            return error.exception

        error = self.with_runtime(scenario)
        current = cron_runtime.get_system_schedule_checkpoint(
            self.profile, job_id="dream-job", revision="revision", occurrence_id="occurrence",
        )
        self.assertEqual(error.response.status_code, 409)
        self.assertEqual(current["phase"], "pending")
        self.assertEqual(current["ownerToken"], operation["ownerToken"])

    def test_native_repair_requires_gateway_owner_grant_not_runtime_key(self):
        private_key = Ed25519PrivateKey.generate()
        payload = {"jobId": "job", "revision": "revision", "repairId": "ticket", "reason": "owner evidence"}
        principal = "entra:11111111-1111-1111-1111-111111111111:22222222-2222-2222-2222-222222222222"
        signed = []

        async def scenario(base_url):
            adapter = self.create_adapter(base_url)
            original_request = adapter._cron_request

            async def capture(*args, **kwargs):
                signed.append(kwargs["body"])
                return await original_request(*args, **kwargs)

            async with httpx.AsyncClient() as client:
                denied = await client.post(
                    base_url + "/internal/cron/rearm-preclaim",
                    headers={"X-Autopilot-Key": "operator-key"}, json={**payload, "operator": principal},
                )
                self.assertEqual(denied.status_code, 403)
                with patch.object(adapter, "_cron_request", side_effect=capture):
                    result = await adapter.repair_schedule("rearm-preclaim", payload, operator=principal)
                self.assertEqual(result["status"], "rearmed")
                tampered = {**signed[0], "payload": {**payload, "revision": "different"}}
                for path, body in (
                    ("rearm-preclaim", tampered), ("resolve", signed[0]),
                    ("rearm-preclaim", {**signed[0], "operator": "entra:forged"}),
                ):
                    response = await client.post(
                        base_url + "/internal/cron/" + path,
                        headers={"X-Autopilot-Key": "operator-key"}, json=body,
                    )
                    self.assertEqual(response.status_code, 403, response.text)

        with (
            patch.dict(os.environ, {
                "WORKER_ID": "worker",
                "COLLECTIVE_LEARNING_APPROVAL_PRIVATE_KEY": base64.b64encode(private_key.private_bytes_raw()).decode(),
                "COLLECTIVE_LEARNING_APPROVAL_PUBLIC_KEY": base64.b64encode(private_key.public_key().public_bytes_raw()).decode(),
            }),
            patch.object(runtime_routes, "rearm_preclaim_schedule", return_value={"status": "rearmed"}) as repair,
        ):
            self.with_runtime(scenario)
        repair.assert_called_once_with(
            self.profile, job_id="job", revision="revision", repair_id="ticket", operator=principal, reason="owner evidence",
        )

if __name__ == "__main__":
    unittest.main()
