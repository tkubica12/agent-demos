import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

from bridge.runtime.base import AgentRequest, AgentResponse, DreamRequest
from bridge.runtime.hermes import DreamExecutionUncertainError, HermesRuntimeAdapter


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "runtimes" / "hermes"))
import cron_runtime
import start_hermes


class DreamRecoveryTests(unittest.TestCase):
    def create_operation(self, profile, *, phase="pending"):
        receipt = cron_runtime._new_system_operation(
            {"id": "dream-job"}, "revision", "occurrence", "adhoc"
        )
        receipt.update({"state": "running", "phase": phase})
        cron_runtime.atomic_write_json(
            cron_runtime.system_schedule_receipts_path(profile),
            {"dream-job:occurrence": receipt},
        )
        return cron_runtime._system_operation(receipt, "dream-job")

    def create_adapter(self, transport):
        adapter = HermesRuntimeAdapter(
            credential_factory=lambda: object(),
            sandbox_config_factory=lambda: object(),
            ensure_sandbox=lambda *args, **kwargs: SimpleNamespace(
                sandbox_id="test-sandbox", endpoint_url="http://runtime.test",
            ),
            client_factory=lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs),
        )
        adapter._wait_for_health = AsyncMock()
        return adapter

    def test_lost_status_response_resumes_durable_agent_without_second_inference(self):
        status_calls = []
        inference_calls = []
        with tempfile.TemporaryDirectory(dir=PROJECT_DIR) as directory:
            profile = Path(directory)
            operation = self.create_operation(profile)
            app = start_hermes.create_health_app(profile, profile, None, None)
            transport = httpx.ASGITransport(app=app)
            request = DreamRequest(session_id=operation["sessionId"])

            def status(_):
                status_calls.append(True)
                if len(status_calls) == 1:
                    raise HTTPException(status_code=503, detail="status temporarily unavailable")
                return {"records": [], "roleRelease": {"release": "1"}}

            async def infer(_):
                current = cron_runtime.get_system_schedule_checkpoint(
                    profile, job_id="dream-job", revision="revision", occurrence_id="occurrence",
                )
                self.assertEqual(current["phase"], "dream_started")
                inference_calls.append(True)
                return AgentResponse(
                    text="Dream completed", raw={"gatewayUrl": "http://runtime.test"},
                )

            async def run():
                first = self.create_adapter(transport)
                with patch.object(first, "invoke", side_effect=infer):
                    with self.assertRaises(httpx.HTTPStatusError):
                        await first.dream(request, operation=operation)
                second = self.create_adapter(transport)
                with patch.object(second, "invoke", side_effect=infer):
                    return await second.dream(request, operation=operation)

            with (
                patch.dict(os.environ, {"API_SERVER_KEY": "operator-key"}),
                patch.object(start_hermes, "build_learning_status", side_effect=status),
            ):
                result = asyncio.run(run())
        self.assertEqual(inference_calls, [True])
        self.assertEqual(len(status_calls), 2)
        self.assertEqual(result.agent.text, "Dream completed")

    def test_uncertain_dream_started_refuses_automatic_inference(self):
        with tempfile.TemporaryDirectory(dir=PROJECT_DIR) as directory:
            profile = Path(directory)
            operation = self.create_operation(profile, phase="dream_started")
            app = start_hermes.create_health_app(profile, profile, None, None)
            adapter = self.create_adapter(httpx.ASGITransport(app=app))
            with (
                patch.dict(os.environ, {"API_SERVER_KEY": "operator-key"}),
                patch.object(adapter, "invoke", new_callable=AsyncMock) as inference,
            ):
                with self.assertRaises(DreamExecutionUncertainError) as error:
                    asyncio.run(adapter.dream(
                        DreamRequest(session_id=operation["sessionId"]), operation=operation,
                    ))
        self.assertEqual(error.exception.status, "uncertain")
        self.assertIn("explicitly start a new ad-hoc Dream", str(error.exception))
        inference.assert_not_awaited()

    def test_lost_completed_checkpoint_ack_does_not_repeat_inference(self):
        with tempfile.TemporaryDirectory(dir=PROJECT_DIR) as directory:
            profile = Path(directory)
            operation = self.create_operation(profile)
            app = start_hermes.create_health_app(profile, profile, None, None)
            runtime = httpx.ASGITransport(app=app)
            lost = []

            async def transport(request):
                response = await runtime.handle_async_request(request)
                if (
                    request.url.path == "/internal/cron/system/checkpoint"
                    and json.loads(request.content)["phase"] == "dream_completed"
                    and not lost
                ):
                    lost.append(True)
                    await response.aclose()
                    raise httpx.ReadError("response lost after checkpoint commit", request=request)
                return response

            adapter = self.create_adapter(httpx.MockTransport(transport))
            inference = AsyncMock(return_value=AgentResponse(
                text="Completed before response loss", raw={"gatewayUrl": "http://runtime.test"},
            ))
            request = DreamRequest(session_id=operation["sessionId"])

            async def run():
                with self.assertRaises(httpx.ReadError):
                    await adapter.dream(request, operation=operation)
                return await adapter.dream(request, operation=operation)

            with (
                patch.dict(os.environ, {"API_SERVER_KEY": "operator-key"}),
                patch.object(adapter, "invoke", inference),
                patch.object(start_hermes, "build_learning_status", return_value={"records": []}),
            ):
                result = asyncio.run(run())
        inference.assert_awaited_once()
        self.assertEqual(result.agent.text, "Completed before response loss")

    def test_checkpoint_endpoint_rejects_stale_owner(self):
        with tempfile.TemporaryDirectory(dir=PROJECT_DIR) as directory:
            profile = Path(directory)
            operation = self.create_operation(profile)
            app = start_hermes.create_health_app(profile, profile, None, None)
            adapter = self.create_adapter(httpx.ASGITransport(app=app))
            with patch.dict(os.environ, {"API_SERVER_KEY": "operator-key"}):
                with self.assertRaises(httpx.HTTPStatusError) as error:
                    asyncio.run(adapter.checkpoint_system_schedule(
                        job_id="dream-job", revision="revision", occurrence_id="occurrence",
                        owner_token="stale", expected_phase="pending",
                        phase="dream_started", payload={},
                    ))
            current = cron_runtime.get_system_schedule_checkpoint(
                profile, job_id="dream-job", revision="revision", occurrence_id="occurrence",
            )
        self.assertEqual(error.exception.response.status_code, 409)
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
                adapter = HermesRuntimeAdapter(
                    credential_factory=lambda: object(),
                    sandbox_config_factory=lambda: object(),
                    ensure_sandbox=lambda *args, **kwargs: SimpleNamespace(
                        sandbox_id="test", endpoint_url="http://runtime.test",
                        reused_existing_sandbox=True, data_volume="test",
                    ),
                )
                with (
                    patch.dict(os.environ, {
                        "API_SERVER_KEY": "operator-key", "USER_SCHEDULING_ENABLED": "true",
                    }),
                    patch.object(adapter, "_wait_for_health", new_callable=AsyncMock),
                    patch.object(adapter, "_cron_jobs", side_effect=[[before], [after], [after]]),
                    patch.object(adapter, "_cron_request", return_value=reconcile),
                    patch.object(adapter, "_begin_learning_turn", return_value=("turn", [])),
                    patch.object(adapter, "_invoke_hermes_with_abort", return_value=("session", {"output": "Updated"})),
                    patch.object(adapter, "_reconcile_learning_turn", return_value={"accepted": [], "rejected": []}),
                ):
                    with self.assertRaisesRegex(RuntimeError, "reconciliation failed|not armed"):
                        asyncio.run(adapter.invoke(AgentRequest(
                            prompt="Change the schedule", conversation_id="conversation",
                            user_id="operator", source="teams_personal", must_answer=True,
                        )))


if __name__ == "__main__":
    unittest.main()
