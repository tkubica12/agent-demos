from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import FastAPI
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from bridge import event_worker, telemetry
from bridge.durable_transport import Journal, TransportSettings
from scripts.native_transport import connection_body, expected_trigger, trigger_matches


class EventWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_transport_still_reports_authenticated_gateway_quiescence(self):
        app = FastAPI()
        with (
            patch.object(event_worker, "enabled", return_value=False),
            patch.object(event_worker, "initialize_journal") as initialize,
            patch.dict(os.environ, {"API_SERVER_KEY": "test-key"}),
        ):
            event_worker.attach(app)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
                denied = await client.get("/internal/transport/status")
                self.assertEqual(denied.status_code, 401)
                with patch("bridge.work_activity.active_work_count", return_value=2):
                    busy = await client.get("/internal/transport/status", headers={"X-Autopilot-Key": "test-key"})
                self.assertEqual(busy.status_code, 200)
                self.assertEqual(busy.json()["active"], 2)
                self.assertEqual(busy.json()["gatewayActive"], 2)
                self.assertEqual(busy.json()["schedulingActive"], 0)
                self.assertFalse(busy.json()["enabled"])
                with patch("bridge.work_activity.active_work_count", return_value=0):
                    idle = await client.get("/internal/transport/status", headers={"X-Autopilot-Key": "test-key"})
                self.assertEqual(idle.json()["active"], 0)
                self.assertEqual((await client.post("/internal/transport/callback")).status_code, 404)
            initialize.assert_not_called()

    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temporary.cleanup)
        self.settings = TransportSettings(
            "worker", "gateway", "client", "bus.servicebus.windows.net",
            "/connector/connections/gateway", "worker",
            Path(self.temporary.name) / "journal.db",
        )
        self.journal = Journal(self.settings.journal_path)
        self.message_id = "schedule-message"
        self.body = {
            "version": "1.0", "type": "hermes.cron.fire", "workerId": "worker",
            "jobId": "job-one", "revision": "revision-one",
        }
        self.envelope = {
            "MessageId": self.message_id, "LockToken": "lease",
            "Properties": {"DeliveryCount": 1},
        }
        self.bus = MagicMock()
        self.bus.__enter__.return_value = self.bus
        self.patches = [
            patch.object(event_worker, "settings", return_value=self.settings),
            patch.object(event_worker, "journal", return_value=self.journal),
            patch.object(event_worker, "bus", return_value=self.bus),
            patch.object(event_worker, "checkpoint", new_callable=AsyncMock),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def admit(self):
        return self.journal.admit(
            self.settings.schedule_queue, self.envelope, self.body, self.settings.connection_id,
        )

    async def test_owner_resumes_only_known_result_without_handler_or_broker_replay(self):
        self.admit()
        self.journal.update("worker", self.message_id, phase="unknown", settled=1, effect_stage="native")
        resume = AsyncMock(return_value={"status": "completed", "deliveryActivityId": "teams-ack"})
        inference = AsyncMock()
        app = FastAPI()
        proof = {
            "queue": "worker", "messageId": self.message_id,
            "executionId": "native-execution", "outputSha256": "a" * 64, "reason": "verified result",
        }
        with (
            patch.object(event_worker, "enabled", return_value=True),
            patch.object(event_worker, "initialize_journal"),
            patch.object(event_worker, "require_collective_learning_operator", return_value="entra:tenant:owner"),
        ):
            event_worker.attach(app, schedule_handler=inference, known_result_handler=resume)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
                first = await client.post("/internal/transport/resume-known-result", json=proof)
                self.assertEqual(first.status_code, 200, first.text)
                repeated = await client.post("/internal/transport/resume-known-result", json=proof)
                self.assertEqual(repeated.status_code, 200, repeated.text)
                conflict = await client.post(
                    "/internal/transport/resume-known-result", json={**proof, "executionId": "other"},
                )
                self.assertEqual(conflict.status_code, 409)
        resume.assert_awaited_once_with(self.body, {"executionId": "native-execution", "outputSha256": "a" * 64})
        inference.assert_not_awaited()
        self.bus.settle.assert_not_called()
        self.assertEqual(self.journal.get("worker", self.message_id)["phase"], "completed")

    async def test_unknown_external_send_blocks_even_owner_known_result_resume(self):
        self.admit()
        self.journal.update("worker", self.message_id, phase="unknown", settled=1, effect_stage="external_started")
        resume = AsyncMock()
        app = FastAPI()
        with (
            patch.object(event_worker, "enabled", return_value=True),
            patch.object(event_worker, "initialize_journal"),
            patch.object(event_worker, "require_collective_learning_operator", return_value="entra:tenant:owner"),
        ):
            event_worker.attach(app, known_result_handler=resume)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
                response = await client.post("/internal/transport/resume-known-result", json={
                    "queue": "worker", "messageId": self.message_id, "executionId": "execution",
                    "outputSha256": "a" * 64, "reason": "operator request",
                })
        self.assertEqual(response.status_code, 409)
        resume.assert_not_awaited()
        self.assertEqual(self.journal.get("worker", self.message_id)["phase"], "unknown")

    async def test_schedule_work_is_checkpointed_before_and_after_settlement(self):
        self.admit()
        handler = AsyncMock(return_value={"status": "completed"})

        async def guard(work, queue, message_id):
            return await work

        with patch.object(event_worker, "_guarded", side_effect=guard):
            await event_worker._process(self.settings.schedule_queue, self.message_id, handler)

        row = self.journal.get(self.settings.schedule_queue, self.message_id)
        self.assertEqual(row["phase"], "completed")
        self.assertEqual(row["settled"], 1)
        handler.assert_awaited_once_with(self.body)
        self.bus.settle.assert_called_once_with(self.settings.schedule_queue, "complete", "lease")
        self.assertGreaterEqual(event_worker.checkpoint.await_count, 3)

    async def test_dream_spans_report_real_completion_not_duplicate_freshness(self):
        exporter = InMemorySpanExporter()
        configuration = telemetry.TelemetrySettings(worker_id="worker", agent_id="agent")
        provider = TracerProvider(resource=configuration.resource())
        provider.add_span_processor(SimpleSpanProcessor(telemetry.MetadataOnlyExporter(exporter)))
        self.addCleanup(provider.shutdown)
        self.body["type"] = "system.dream"
        self.body["privatePayload"] = "must never enter telemetry"
        self.admit()
        with (
            patch.object(telemetry, "_settings", configuration),
            patch.object(telemetry.trace, "get_tracer", side_effect=provider.get_tracer),
        ):
            await self.execute(AsyncMock(return_value={"status": "completed"}))
            self.envelope["LockToken"] = "redelivery"
            self.admit()
            await self.execute(AsyncMock())
            self.message_id = "native-duplicate"
            self.envelope.update(MessageId=self.message_id, LockToken="duplicate-lease")
            self.admit()
            await self.execute(AsyncMock(return_value={"status": "duplicate"}))
        spans = exporter.get_finished_spans()
        self.assertEqual([span.name for span in spans], ["transport.process"] * 3)
        self.assertEqual([span.attributes["autopilots.outcome"] for span in spans], [
            "completed", "duplicate", "duplicate",
        ])
        for span in spans:
            self.assertEqual(span.attributes["autopilots.invocation.source"], "system.dream")
            self.assertEqual(span.attributes["autopilots.worker.id"], configuration.opaque_id("worker", "worker"))
            self.assertNotIn("must never enter telemetry", str(span.attributes))

    async def test_dream_spans_preserve_failed_and_unknown_terminal_outcomes(self):
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(telemetry.MetadataOnlyExporter(exporter)))
        self.addCleanup(provider.shutdown)
        self.body["type"] = "system.dream"
        with patch.object(telemetry.trace, "get_tracer", side_effect=provider.get_tracer):
            for index, status in enumerate(("failed", "uncertain", "stale")):
                self.message_id = f"outcome-{index}"
                self.envelope.update(MessageId=self.message_id, LockToken=f"lease-{index}")
                self.admit()
                await self.execute(AsyncMock(return_value={"status": status}))
        self.assertEqual(
            [span.attributes["autopilots.outcome"] for span in exporter.get_finished_spans()],
            ["failed", "unknown", "stale"],
        )

    async def test_completed_schedule_redelivery_only_completes_the_new_lease(self):
        self.admit()
        self.journal.update(self.settings.schedule_queue, self.message_id, phase="completed", result='{"done":true}')
        handler = AsyncMock()
        await event_worker._process(self.settings.schedule_queue, self.message_id, handler)
        handler.assert_not_awaited()
        self.bus.settle.assert_called_once_with(self.settings.schedule_queue, "complete", "lease")

    async def test_schedule_failure_is_not_settled_as_success(self):
        self.admit()
        handler = AsyncMock(side_effect=RuntimeError("schedule failed"))

        async def guard(work, queue, message_id):
            return await work

        with patch.object(event_worker, "_guarded", side_effect=guard):
            with self.assertRaisesRegex(RuntimeError, "schedule failed"):
                await event_worker._process(self.settings.schedule_queue, self.message_id, handler)
        row = self.journal.get(self.settings.schedule_queue, self.message_id)
        self.assertEqual(row["phase"], "retryable")
        self.assertEqual(row["settled"], 0)
        self.bus.settle.assert_not_called()

    async def execute(self, handler):
        async def guard(work, queue, message_id):
            return await work

        with patch.object(event_worker, "_guarded", side_effect=guard):
            await event_worker._process(self.settings.schedule_queue, self.message_id, handler)

    async def test_uncertain_and_failed_outcomes_are_deadlettered_not_completed(self):
        for outcome, phase in (("uncertain", "unknown"), ("failed", "failed")):
            with self.subTest(outcome=outcome):
                self.admit()
                self.journal.update("worker", self.message_id, phase="pending", settled=0)
                self.bus.settle.reset_mock()
                await self.execute(AsyncMock(return_value={"status": outcome, "reason": "inspect"}))
                row = self.journal.get("worker", self.message_id)
                self.assertEqual(row["phase"], phase)
                self.assertEqual(row["settled"], 1)
                self.bus.settle.assert_called_once_with("worker", "deadletter", "lease")

    async def test_known_run_timeout_is_recoverable_without_broker_completion(self):
        self.admit()
        self.journal.update("worker", self.message_id, native_run_id="known")
        with self.assertRaises(event_worker.InvocationPendingError):
            await self.execute(AsyncMock(side_effect=event_worker.InvocationPendingError("still running")))
        self.assertEqual(self.journal.get("worker", self.message_id)["phase"], "known_run")
        self.bus.settle.assert_not_called()

    async def test_final_preclaim_failure_terminalizes_and_releases_admission(self):
        self.envelope["Properties"]["DeliveryCount"] = 10
        self.admit()
        with self.assertRaisesRegex(RuntimeError, "before native admission"):
            await self.execute(AsyncMock(side_effect=RuntimeError("before native admission")))
        self.assertEqual(self.journal.get("worker", self.message_id)["phase"], "failed")
        self.bus.settle.assert_called_once_with("worker", "deadletter", "lease")

    async def test_final_known_run_is_quarantined_not_replayed(self):
        self.envelope["Properties"]["DeliveryCount"] = 10
        self.admit()
        self.journal.update("worker", self.message_id, native_run_id="known", effect_stage="native")
        await self.execute(AsyncMock(return_value={"status": "in_progress"}))
        self.assertEqual(self.journal.get("worker", self.message_id)["phase"], "unknown")
        self.bus.settle.assert_called_once_with("worker", "deadletter", "lease")

    async def test_outbound_effect_response_loss_is_not_replayed(self):
        self.admit()
        sent = AsyncMock(side_effect=TimeoutError("Teams response lost"))

        async def handler(_):
            return await event_worker.external_effect("teams", sent)

        with patch.object(event_worker, "enabled", return_value=True):
            with self.assertRaisesRegex(event_worker.AmbiguousEffectError, "Teams response lost"):
                await self.execute(handler)
            self.assertEqual(self.journal.get("worker", self.message_id)["phase"], "unknown")
            self.envelope["LockToken"] = "new-lease"
            self.envelope["Properties"]["DeliveryCount"] = 2
            self.admit()
            await self.execute(handler)
        sent.assert_awaited_once()
        self.assertTrue(all(call.args[1] == "deadletter" for call in self.bus.settle.call_args_list))

    async def test_saved_outbound_ack_is_reused_after_native_ack_failure(self):
        self.admit()
        sent = AsyncMock(return_value={"activityId": "sent-once"})
        attempts = 0

        async def handler(_):
            nonlocal attempts
            result = await event_worker.external_effect("teams", sent)
            attempts += 1
            if attempts == 1:
                raise ConnectionError("native acknowledgement unavailable")
            return {"status": "completed", **result}

        with patch.object(event_worker, "enabled", return_value=True):
            with self.assertRaises(ConnectionError):
                await self.execute(handler)
            await self.execute(handler)
        sent.assert_awaited_once()
        self.assertEqual(self.journal.get("worker", self.message_id)["phase"], "completed")

    async def test_repair_delivery_id_reuses_original_occurrence_outbound_ack(self):
        self.admit()
        sent = AsyncMock(return_value={"activityId": "sent-once"})

        async def handler(_):
            return {"status": "completed", **await event_worker.external_effect("teams", sent)}

        with patch.object(event_worker, "enabled", return_value=True):
            await self.execute(handler)
            self.message_id = "repair-delivery"
            self.envelope.update(MessageId=self.message_id, LockToken="repair-lease")
            self.admit()
            await self.execute(handler)
        sent.assert_awaited_once()
        self.assertEqual(self.journal.get("worker", self.message_id)["phase"], "completed")

    async def test_status_counts_live_gateway_work_not_historical_retry_receipts(self):
        self.admit()
        self.journal.update("worker", self.message_id, phase="retryable")
        app = FastAPI()
        with (
            patch.object(event_worker, "enabled", return_value=True),
            patch.object(event_worker, "initialize_journal", return_value=self.journal),
            patch.dict("os.environ", {"API_SERVER_KEY": "key"}),
            patch("bridge.work_activity.active_work_count", return_value=2),
        ):
            event_worker.attach(app)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://gateway",
            ) as client:
                response = await client.get(
                    "/internal/transport/status", headers={"x-autopilot-key": "key"},
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["active"], 2)
        self.assertEqual(response.json()["gatewayActive"], 2)
        self.assertEqual(response.json()["unfinished"], 0)
        self.assertEqual(response.json()["recoverable"], 1)

    async def test_repair_routes_require_real_owner_and_derive_the_audit_principal(self):
        from tests import test_collective_learning_routes as owner_auth_tests

        owner_auth = owner_auth_tests.CollectiveLearningRouteTests()
        owner_auth.setUp()
        self.addCleanup(owner_auth.doCleanups)
        principal = f"entra:{owner_auth.tenant_id}:{owner_auth.owner_id}"
        owner_headers = {"Authorization": f"Bearer {owner_auth.token()}"}
        repair = AsyncMock(return_value={"status": "repaired"})
        app = FastAPI()
        with (
            patch.object(event_worker, "enabled", return_value=True),
            patch.object(event_worker, "initialize_journal", return_value=self.journal),
        ):
            event_worker.attach(app, schedule_repair_handler=repair)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://gateway",
            ) as client:
                for path in ("recover", "resolve", "rearm-preclaim", "resolve-schedule"):
                    denied = await client.post(
                        f"/internal/transport/{path}", json={},
                        headers={"X-Autopilot-Key": "runtime-key"},
                    )
                    self.assertEqual(denied.status_code, 401, denied.text)
                    forged = await client.post(
                        f"/internal/transport/{path}", json={},
                        headers={
                            "Authorization": f"Bearer {owner_auth.token(oid='runtime-agent')}",
                            "X-Autopilot-Key": "runtime-key",
                        },
                    )
                    self.assertEqual(forged.status_code, 403, forged.text)
                repair.assert_not_awaited()
                self.admit()
                self.journal.update("worker", self.message_id, phase="unknown")
                resolution = {
                    "queue": "worker", "messageId": self.message_id,
                    "outcome": "skipped", "reason": "owner inspected effects",
                }
                forged = await client.post(
                    "/internal/transport/resolve", json={**resolution, "operator": "forged"},
                    headers=owner_headers,
                )
                self.assertEqual(forged.status_code, 422)
                resolved = await client.post(
                    "/internal/transport/resolve", json=resolution, headers=owner_headers,
                )
                self.assertEqual(resolved.status_code, 200, resolved.text)
                audit = json.loads(self.journal.get("worker", self.message_id)["resolution"])
                self.assertEqual(audit["operator"], principal)
                payload = {"jobId": "job", "revision": "revision", "repairId": "ticket", "reason": "preclaim"}
                rearmed = await client.post(
                    "/internal/transport/rearm-preclaim", json=payload, headers=owner_headers,
                )
                self.assertEqual(rearmed.status_code, 200)
                repair.assert_awaited_once_with("rearm-preclaim", payload, principal)
                recovered = await client.post("/internal/transport/recover", json={}, headers=owner_headers)
                self.assertEqual(recovered.status_code, 200)
                callback = await client.post("/internal/transport/callback", json={}, headers=owner_headers)
                self.assertEqual(callback.status_code, 401)

    async def test_gateway_keepalive_uses_its_native_health_url(self):
        response = MagicMock()
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(return_value=response)
        with (
            patch.dict("os.environ", {"TRANSPORT_SELF_URL": "https://gateway.example"}),
            patch.object(event_worker.httpx, "AsyncClient", return_value=client),
            patch.object(event_worker.asyncio, "sleep", side_effect=asyncio.CancelledError),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await event_worker._keep_active()
        client.get.assert_awaited_once_with("https://gateway.example/health")

    async def test_native_ingress_admits_raw_envelope_without_settling_at_callback_receipt(self):
        from bridge.transport_ingress import NativeTransportIngressMiddleware

        app = FastAPI()
        app.add_middleware(NativeTransportIngressMiddleware)
        envelope = {
            **self.envelope,
            "ContentData": base64.b64encode(json.dumps(self.body).encode()).decode(),
        }
        with (
            patch.object(event_worker, "enabled", return_value=True),
            patch.object(event_worker, "initialize_journal", return_value=self.journal),
            patch.object(event_worker, "sweep_stranded", new_callable=AsyncMock),
            patch.object(event_worker, "start_work") as start,
        ):
            event_worker.attach(app)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://gateway:8001",
            ) as client:
                response = await client.post("/internal/transport/callback", json=envelope)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["acknowledged"])
        receipt = self.journal.get("worker", self.message_id)
        self.assertEqual(receipt["lock_token"], "lease")
        self.assertEqual(receipt["connection_id"], self.settings.connection_id)
        start.assert_called_once_with("worker", self.message_id, None)
        self.bus.settle.assert_not_called()


class NativeTransportContractTests(unittest.TestCase):
    def outputs(self):
        return {
            "subscription_id": "00000000-0000-0000-0000-000000000001",
            "resource_group_name": "rg-owned", "sandbox_location": "swedencentral",
            "sandbox_groups": {"gateway": {"name": "worker-gateway"}, "runtime": {"name": "worker-runtime"}},
            "sandbox_services": {"gateway": {"callback_url": "https://gateway--8001.example"}},
        }

    def test_gateway_callback_targets_only_scheduling_queue(self):
        result = expected_trigger(self.outputs(), "gateway", "worker", "sandbox-id")
        callback = result["properties"]["notificationDetails"]
        self.assertEqual(callback["body"], "@triggerBody()")
        self.assertEqual(callback["callbackUrl"], "https://gateway--8001.example/internal/transport/callback")
        self.assertNotIn("executeShellCommand", json.dumps(result))
        self.assertEqual(callback["authentication"]["audience"], "https://auth.adcproxy.io/")
        self.assertEqual(result["properties"]["connectionDetails"]["connectionName"], "gateway-servicebus-mi")
        self.assertEqual(result["properties"]["parameters"][0]["value"], "worker")

    def test_runtime_does_not_receive_queued_work(self):
        with self.assertRaisesRegex(ValueError, "Only the gateway"):
            expected_trigger(self.outputs(), "runtime", "worker", "sandbox-id")

    def test_connection_uses_managed_identity_without_stored_key(self):
        result = connection_body("bus.servicebus.windows.net", "swedencentral", "gateway")
        self.assertEqual(
            result["properties"]["connectionProperties"]["authentication"],
            {"type": "ManagedServiceIdentity"},
        )
        self.assertNotIn("connectionString", json.dumps(result))

    def test_native_trigger_readback_normalizes_only_observed_server_fields(self):
        desired = expected_trigger(self.outputs(), "gateway", "worker", "sandbox-id")["properties"]
        actual = json.loads(json.dumps(desired))
        actual["notificationDetails"]["httpMethod"] = "Post"
        actual["notificationDetails"].pop("authentication")
        actual.update(provisioningState="Succeeded", type="NotSpecified")
        self.assertTrue(trigger_matches(actual, desired))
        actual["notificationDetails"]["body"] = "@triggerBody().ContentData"
        self.assertFalse(trigger_matches(actual, desired))

    def test_explicit_wrong_callback_identity_is_rejected(self):
        desired = expected_trigger(self.outputs(), "gateway", "worker", "sandbox-id")["properties"]
        actual = json.loads(json.dumps(desired))
        actual["notificationDetails"]["authentication"] = {"type": "None"}
        self.assertFalse(trigger_matches(actual, desired))


if __name__ == "__main__":
    unittest.main()
