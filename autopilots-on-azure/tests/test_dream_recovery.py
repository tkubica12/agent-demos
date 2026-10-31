import asyncio
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from aiohttp import web
from aiohttp.test_utils import TestServer

from bridge.runtime.base import AgentRequest, AgentResponse, DreamRequest
from bridge.runtime.hermes import DreamExecutionUncertainError, HermesRuntimeAdapter


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "runtimes" / "hermes"))
import cron_runtime  # noqa: E402
import runtime_routes  # noqa: E402
from native_learning import LearningConflictError  # noqa: E402
from tests.native_hermes_fakes import FakeSkillLedger, FakeWriteApproval  # noqa: E402


class DreamRecoveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
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

    def test_updated_enabled_job_surfaces_reconcile_failure_or_missing_arm(self):
        for reconcile in ({"status": "error", "error": "broker unavailable"}, {"status": "ok"}):
            with self.subTest(reconcile=reconcile):
                before = {
                    "id": "existing", "revision": "before", "enabled": True,
                    "state": "scheduled", "nextRunAt": "2026-09-07T02:00:00Z",
                    "externallyScheduled": True,
                }
                after = {**before, "revision": "after", "externallyScheduled": False}
                adapter = HermesRuntimeAdapter(runtime_url_factory=lambda: "http://runtime.test")
                with (
                    patch.dict(os.environ, {"USER_SCHEDULING_ENABLED": "true"}),
                    patch.object(adapter, "_wait_for_health", new_callable=AsyncMock),
                    patch.object(adapter, "_cron_jobs", side_effect=[[before], [after], [after]]),
                    patch.object(adapter, "_cron_request", return_value=reconcile),
                    patch.object(adapter, "_invoke_hermes", return_value=("sessions", {"output": "Updated"})),
                ):
                    with self.assertRaisesRegex(RuntimeError, "reconciliation failed|not armed"):
                        asyncio.run(adapter.invoke(AgentRequest(
                            prompt="Change the schedule", conversation_id="conversation",
                            user_id="operator", source="teams_personal", must_answer=True,
                        )))


if __name__ == "__main__":
    unittest.main()
