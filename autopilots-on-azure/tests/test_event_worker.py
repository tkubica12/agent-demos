from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from bridge import event_worker
from bridge.durable_transport import Journal, TransportSettings
from scripts.native_transport import connection_body, expected_trigger, trigger_matches


class EventWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
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
        self.assertEqual(row["phase"], "running")
        self.assertEqual(row["settled"], 0)
        self.bus.settle.assert_not_called()

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


class NativeTransportContractTests(unittest.TestCase):
    def outputs(self):
        return {
            "subscription_id": "00000000-0000-0000-0000-000000000001",
            "resource_group_name": "rg-owned", "sandbox_location": "swedencentral",
            "sandbox_groups": {"gateway": {"name": "worker-gateway"}, "runtime": {"name": "worker-runtime"}},
        }

    def test_gateway_callback_targets_only_scheduling_queue(self):
        result = expected_trigger(self.outputs(), "gateway", "worker", "sandbox-id")
        callback = result["properties"]["notificationDetails"]
        command = callback["body"]["command"]
        self.assertIn("@{base64(string(triggerBody()))}", command)
        self.assertEqual(command.count("triggerBody()"), 1)
        self.assertEqual(callback["body"]["activationMode"], "OnDemand")
        self.assertEqual(callback["authentication"]["audience"], "https://management.azuredevcompute.io/")
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
        actual["notificationDetails"]["body"]["command"] += " changed"
        self.assertFalse(trigger_matches(actual, desired))

    def test_explicit_wrong_callback_identity_is_rejected(self):
        desired = expected_trigger(self.outputs(), "gateway", "worker", "sandbox-id")["properties"]
        actual = json.loads(json.dumps(desired))
        actual["notificationDetails"]["authentication"] = {"type": "None"}
        self.assertFalse(trigger_matches(actual, desired))


if __name__ == "__main__":
    unittest.main()
