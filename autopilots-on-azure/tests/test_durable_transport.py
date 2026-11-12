from __future__ import annotations

import base64
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bridge.durable_transport import (
    MAX_MESSAGE_BYTES,
    Journal,
    NativeBus,
    TransportSettings,
    canonical,
    decode_callback,
)


class DurableTransportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.settings = TransportSettings(
            "worker", "gateway", "client", "bus.servicebus.windows.net",
            "/connector/connections/gateway", "worker",
            Path(self.temporary.name) / "journal.db",
        )
        self.body = {
            "version": "1.0", "type": "hermes.cron.fire", "workerId": "worker",
            "jobId": "job-one", "revision": "revision-one",
            "trace": {"traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01"},
        }
        self.envelope = {
            "MessageId": "message-one", "LockToken": "lease-one",
            "Properties": {"DeliveryCount": 1},
            "ContentData": base64.b64encode(canonical(self.body).encode()).decode(),
        }
        self.journal = Journal(self.settings.journal_path)

    def encoded(self):
        return base64.b64encode(json.dumps(self.envelope).encode()).decode()

    def admit(self, queue="worker", body=None, envelope=None, connection_id="connection"):
        return self.journal.admit(
            queue, envelope or self.envelope, body or self.body, connection_id,
        )

    def test_schedule_callback_accepts_supported_job_and_trace(self):
        envelope, body = decode_callback(self.encoded(), "worker", self.settings)
        self.assertEqual(body, self.body)
        self.assertEqual(envelope["Properties"]["DeliveryCount"], 1)

    def test_callback_rejects_other_queues_workers_and_job_types(self):
        with self.assertRaisesRegex(ValueError, "settling actor"):
            decode_callback(self.encoded(), "worker-activities", self.settings)
        for body in (
            {**self.body, "workerId": "another-worker"},
            {**self.body, "type": "unsupported"},
            {**self.body, "version": "2.0"},
            {**self.body, "trace": {"baggage": "private"}},
        ):
            with self.subTest(body=body):
                self.envelope["ContentData"] = base64.b64encode(canonical(body).encode()).decode()
                with self.assertRaises(ValueError):
                    decode_callback(self.encoded(), "worker", self.settings)

    def test_document_retry_requires_its_operation_identity(self):
        body = {"version": "1.0", "type": "document.publish.retry", "workerId": "worker"}
        self.envelope["ContentData"] = base64.b64encode(canonical(body).encode()).decode()
        with self.assertRaisesRegex(ValueError, "operation identity"):
            decode_callback(self.encoded(), "worker", self.settings)

    def test_invalid_envelope_and_delivery_count_are_rejected(self):
        self.envelope["Properties"]["DeliveryCount"] = True
        with self.assertRaisesRegex(ValueError, "delivery count"):
            decode_callback(self.encoded(), "worker", self.settings)
        self.envelope["Properties"]["DeliveryCount"] = 1
        self.envelope["LockToken"] = ""
        with self.assertRaisesRegex(ValueError, "PeekLock"):
            decode_callback(self.encoded(), "worker", self.settings)

    def test_duplicate_message_id_cannot_change_effect_payload(self):
        self.admit()
        changed = {**self.body, "jobId": "different-job"}
        with self.assertRaisesRegex(ValueError, "changed its payload"):
            self.admit(body=changed)

    def test_trace_changes_do_not_change_the_scheduled_effect(self):
        self.admit()
        changed_trace = {**self.body, "trace": {"traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01"}}
        self.assertEqual(self.admit(body=changed_trace)["phase"], "pending")

    def test_redelivery_reopens_only_settlement_and_keeps_completion(self):
        self.admit()
        self.journal.update("worker", "message-one", phase="completed", result='{"done":true}', settled=1)
        redelivery = {
            **self.envelope,
            "LockToken": "lease-two",
            "Properties": {"DeliveryCount": 2},
        }
        row = self.admit(envelope=redelivery)
        self.assertEqual(row["phase"], "completed")
        self.assertEqual(row["result"], '{"done":true}')
        self.assertEqual(row["settled"], 0)
        self.assertEqual(row["delivery_count"], 2)

    def test_same_lease_cannot_change_owning_connection(self):
        self.admit(connection_id="first")
        with self.assertRaisesRegex(ValueError, "owning connection"):
            self.admit(connection_id="second")

    def test_backup_is_an_independent_valid_database(self):
        self.admit()
        destination = Path(self.temporary.name) / "backup" / "journal.db"
        self.journal.backup(destination)
        restored = Journal(destination)
        self.assertEqual(restored.get("worker", "message-one")["payload"], canonical(self.body))
        with closing(sqlite3.connect(destination)) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_obsolete_recovery_data_is_removed_without_deleting_schedule_state(self):
        self.admit()
        activity_body = {"version": "1.0", "type": "legacy.activity"}
        self.admit("worker-activities", body=activity_body)
        self.journal.save_state("payload:old", {"activity": "private"})
        self.journal.save_state("outbound:old", {"job": "legacy"})
        self.journal.save_state("teams:conversation", {"turns": ["keep"]})
        with closing(self.journal.connect()) as connection, connection:
            connection.execute("CREATE TABLE responses (id TEXT PRIMARY KEY, payload TEXT)")
            connection.execute("INSERT INTO responses VALUES ('old', 'private')")

        self.assertTrue(self.journal.remove_obsolete_recovery_data("worker-activities"))
        with self.assertRaisesRegex(ValueError, "No durable receipt"):
            self.journal.get("worker-activities", "message-one")
        self.assertEqual(self.journal.get("worker", "message-one")["phase"], "pending")
        self.assertIsNone(self.journal.load_state("payload:old"))
        self.assertIsNone(self.journal.load_state("outbound:old"))
        self.assertEqual(self.journal.load_state("teams:conversation"), {"turns": ["keep"]})
        with closing(self.journal.connect()) as connection:
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='responses'"
            ).fetchone())

    def test_native_bus_settles_only_the_scheduling_queue(self):
        credential = SimpleNamespace(get_token=lambda *args: SimpleNamespace(token="unit-test-credential"))
        with patch(
            "bridge.durable_transport.urllib.request.urlopen",
            return_value=io.BytesIO(b'{"response":{"statusCode":204}}'),
        ) as send:
            NativeBus(self.settings, credential).settle("worker", "complete", "lease-one")
        operation = json.loads(send.call_args.args[0].data)["request"]
        self.assertEqual((operation["method"], operation["path"]), ("DELETE", "/worker/messages/complete"))
        self.assertEqual(operation["queries"]["lockToken"], "lease-one")
        with self.assertRaisesRegex(ValueError, "Unsupported queue"):
            NativeBus(self.settings, credential).settle("worker-activities", "complete", "lease")

    def test_callback_size_limit_is_enforced(self):
        with self.assertRaisesRegex(ValueError, "bounded envelope size"):
            decode_callback("x" * (MAX_MESSAGE_BYTES * 3 + 1), "worker", self.settings)


if __name__ == "__main__":
    unittest.main()
