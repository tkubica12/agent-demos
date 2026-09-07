from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

import bridge.app as bridge_app
import bridge.servicebus_scheduler as servicebus_scheduler
from bridge.proactive_delivery import delivery_reference_key, send_proactive_activity
from bridge.servicebus_scheduler import (
    ServiceBusScheduleConsumer,
    ServiceBusScheduleSender,
    message_trace_headers,
)


RUNTIME_DIR = Path(__file__).resolve().parents[1] / "runtimes" / "hermes"
sys.path.insert(0, str(RUNTIME_DIR))

import cron_runtime  # noqa: E402


class FakeReceivedMessage:
    def __init__(self, payload: object, *, delivery_count: int = 1) -> None:
        self.body = [json.dumps(payload).encode("utf-8")]
        self.delivery_count = delivery_count


class FakeReceiver:
    def __init__(self) -> None:
        self.completed = []
        self.abandoned = []
        self.dead_lettered = []

    def complete_message(self, message) -> None:
        self.completed.append(message)

    def abandon_message(self, message) -> None:
        self.abandoned.append(message)

    def dead_letter_message(self, message, **kwargs) -> None:
        self.dead_lettered.append((message, kwargs))


class UserSchedulingTests(unittest.TestCase):
    def test_document_retry_enables_consumer_without_user_cron(self):
        with patch.dict(
            os.environ,
            {
                "USER_SCHEDULING_ENABLED": "false",
                "DOCUMENT_RETRY_ENABLED": "true",
            },
        ):
            consumer = ServiceBusScheduleConsumer(
                handler=lambda payload: asyncio.sleep(0)
            )

        self.assertTrue(consumer.status()["enabled"])

    def test_azure_provider_plugin_directory_matches_configured_name(self):
        self.assertTrue((RUNTIME_DIR / "plugins" / "azure" / "__init__.py").is_file())
        self.assertFalse((RUNTIME_DIR / "plugins" / "azure_cron").exists())
        dockerfile = (RUNTIME_DIR / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("ENV PYTHONPATH=/app", dockerfile)

    def test_delivery_reference_key_is_stable_and_boundary_scoped(self):
        first = delivery_reference_key(
            worker_id="hermes2",
            conversation_id="conversation-1",
            boundary="public_channel",
        )
        second = delivery_reference_key(
            worker_id="hermes2",
            conversation_id="conversation-1",
            boundary="one_to_one",
        )

        self.assertEqual(first, delivery_reference_key(
            worker_id="hermes2",
            conversation_id="conversation-1",
            boundary="public_channel",
        ))
        self.assertNotEqual(first, second)
        self.assertNotIn("conversation-1", first)

    def test_proactive_delivery_reconstructs_conversation_and_sends_text(self):
        sent = []
        fake_conversation = SimpleNamespace(
            claims={"aud": "app-1"},
            conversation_reference=SimpleNamespace(
                get_continuation_activity=lambda: "continuation"
            ),
            validate=lambda: None,
        )

        class Adapter:
            async def continue_conversation_with_claims(
                self,
                identity,
                continuation,
                callback,
            ):
                self.identity = identity
                self.continuation = continuation
                await callback(SimpleNamespace(send_activity=self.send_activity))

            async def send_activity(self, activity):
                sent.append(activity)
                return {"id": "activity-1"}

        with patch(
            "bridge.proactive_delivery.Conversation.from_json_to_store_item",
            return_value=fake_conversation,
        ):
            result = asyncio.run(send_proactive_activity(
                Adapter(),
                {
                    "boundary": "public_channel",
                    "conversation": {"conversation_reference": {}},
                },
                "Scheduled hello",
            ))

        self.assertEqual(sent[0].text, "Scheduled hello")
        self.assertEqual(result["activityId"], "activity-1")

    def test_service_bus_message_completes_after_success(self):
        handled = []
        spans = []
        handler_traces = []
        tracer = TracerProvider().get_tracer("scheduler-test")
        async def run():
            previous = os.environ.get("WORKER_ID")
            os.environ["WORKER_ID"] = "hermes2"
            try:
                async def handler(payload):
                    handled.append(payload)
                    handler_traces.append(trace.get_current_span().get_span_context().trace_id)
                    return {"status": "completed"}
                consumer = ServiceBusScheduleConsumer(handler=handler)
                consumer._loop = asyncio.get_running_loop()
                receiver = FakeReceiver()
                message = FakeReceivedMessage({
                    "version": "1.0",
                    "type": "hermes.cron.fire",
                    "workerId": "hermes2",
                    "jobId": "job-1",
                    "revision": "revision-1",
                    "_traceCarrier": {"baggage": "must-not-be-forwarded"},
                })
                message.application_properties = {
                    b"traceparent": b"00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01",
                }
                with patch(
                    "bridge.servicebus_scheduler.operation_span",
                    wraps=servicebus_scheduler.operation_span,
                ) as span, patch("bridge.telemetry.trace.get_tracer", return_value=tracer):
                    await asyncio.to_thread(consumer._process, receiver, message)
                    spans.append(span.call_args.kwargs)
                return consumer, receiver
            finally:
                if previous is None:
                    os.environ.pop("WORKER_ID", None)
                else:
                    os.environ["WORKER_ID"] = previous

        consumer, receiver = asyncio.run(run())

        self.assertEqual(len(receiver.completed), 1)
        self.assertEqual(consumer.status()["completed"], 1)
        self.assertEqual(receiver.abandoned, [])
        self.assertEqual(spans[0]["carrier"], {
            "traceparent": "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01",
        })
        self.assertEqual(spans[0]["operation_id"], "job-1:revision-1")
        self.assertEqual(handler_traces, [int("a" * 32, 16)])

    def test_document_retry_message_is_scheduled_with_identity(self):
        scheduled = []
        cancelled = []

        class Sender:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def schedule_messages(self, message, due_at):
                scheduled.append((message, due_at))
                return [42]

            def cancel_scheduled_messages(self, sequence_numbers):
                cancelled.extend(sequence_numbers)

        class Client:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def get_queue_sender(self, queue):
                self.queue = queue
                return Sender()

        with patch.dict(
            os.environ,
            {
                "WORKER_ID": "hermes2",
                "SCHEDULER_SERVICEBUS_NAMESPACE": (
                    "namespace.servicebus.windows.net"
                ),
                "SCHEDULER_SERVICEBUS_QUEUE": "worker-hermes2",
            },
        ), patch(
            "bridge.servicebus_scheduler.trace_headers",
            return_value={"traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01"},
        ):
            sender = ServiceBusScheduleSender(
                client_factory=lambda *args, **kwargs: Client(),
                credential_factory=lambda: object(),
            )
            result = sender.schedule_document_retry(
                operation_id="a" * 24,
                attempt=1,
                due_at_unix=time.time() + 60,
            )
            sender.cancel_scheduled(result["sequenceNumber"])

        body = json.loads(
            b"".join(
                bytes(part) for part in scheduled[0][0].body
            )
        )
        self.assertEqual(
            body["type"],
            "document.publish.retry",
        )
        self.assertEqual(body["workerId"], "hermes2")
        self.assertEqual(result["sequenceNumber"], 42)
        self.assertEqual(cancelled, [42])
        self.assertEqual(scheduled[0][0].application_properties, {
            "traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01",
        })

    def test_service_bus_trace_carrier_excludes_baggage_and_nontext_values(self):
        message = FakeReceivedMessage({})
        message.application_properties = {
            b"traceparent": b"00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01",
            "tracestate": "vendor=value",
            b"baggage": b"private=user@example.test",
            "Authorization": "secret",
            b"another": 1,
        }
        self.assertEqual(message_trace_headers(message), {
            "traceparent": "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01",
            "tracestate": "vendor=value",
        })
        message.application_properties = {"traceparent": 12}
        self.assertEqual(message_trace_headers(message), {})

    def test_consumer_without_transport_carrier_starts_clean_root(self):
        captured = []
        tracer = TracerProvider().get_tracer("scheduler-test")

        async def handler(payload):
            captured.append(trace.get_current_span().get_span_context().trace_id)
            return {"status": "completed"}

        consumer = ServiceBusScheduleConsumer(handler=handler)
        with (
            patch("bridge.telemetry.trace.get_tracer", return_value=tracer),
            tracer.start_as_current_span("unrelated") as unrelated,
        ):
            asyncio.run(consumer._invoke_handler({
                "jobId": "job", "revision": "revision",
                "_traceCarrier": {"traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01"},
            }, {}))
            unrelated_id = unrelated.get_span_context().trace_id
        self.assertNotEqual(captured[0], unrelated_id)
        self.assertNotEqual(captured[0], int("a" * 32, 16))

    def test_consumer_span_closes_after_handler_error_or_timeout(self):
        async def run(timeout):
            spans = []
            finished = asyncio.Event()

            async def handler(payload):
                spans.append(trace.get_current_span())
                try:
                    if timeout:
                        await asyncio.sleep(10)
                    raise RuntimeError("handler failure")
                finally:
                    finished.set()

            consumer = ServiceBusScheduleConsumer(handler=handler)
            consumer._loop = asyncio.get_running_loop()
            receiver = FakeReceiver()
            message = FakeReceivedMessage({
                "version": "1.0", "type": "hermes.cron.fire", "workerId": "worker",
                "jobId": "job", "revision": "revision",
            })
            await asyncio.to_thread(consumer._process, receiver, message)
            await asyncio.wait_for(finished.wait(), timeout=2)
            await asyncio.sleep(0)
            return receiver, spans

        for timeout in (False, True):
            with self.subTest(timeout=timeout):
                tracer = TracerProvider().get_tracer("scheduler-test")
                with (
                    patch.dict(os.environ, {
                        "WORKER_ID": "worker", "SCHEDULER_MAX_LOCK_RENEWAL_SECONDS": "1",
                        "SCHEDULER_MAX_DELIVERY_COUNT": "5",
                    }),
                    patch("bridge.telemetry.trace.get_tracer", return_value=tracer),
                ):
                    receiver, spans = asyncio.run(run(timeout))
                self.assertEqual(len(receiver.abandoned), 1)
                self.assertEqual(len(spans), 1)
                self.assertFalse(spans[0].is_recording())

    def test_scheduled_document_lock_rearms_without_model_turn(self):
        class Adapter:
            runtime_kind = "hermes"

            async def process_document_background(self, operation_id):
                self.operation_id = operation_id
                return {
                    "status": "locked",
                    "operationId": operation_id,
                    "attempt": 2,
                    "nextAttemptUnix": time.time() + 60,
                }

        adapter = Adapter()
        with (
            patch.object(
                bridge_app,
                "runtime_adapter",
                return_value=adapter,
            ),
            patch.object(
                bridge_app.schedule_sender,
                "schedule_document_retry",
                return_value={"sequenceNumber": 42},
            ) as schedule,
        ):
            result = asyncio.run(
                bridge_app.process_scheduled_message(
                    {
                        "version": "1.0",
                        "type": "document.publish.retry",
                        "workerId": "hermes2",
                        "operationId": "a" * 24,
                        "attempt": 1,
                    }
                )
            )

        self.assertEqual(result["status"], "scheduled")
        self.assertEqual(adapter.operation_id, "a" * 24)
        schedule.assert_called_once()

    def test_proactive_delivery_uses_app_id_when_claims_are_empty(self):
        fake_conversation = SimpleNamespace(
            claims={},
            conversation_reference=SimpleNamespace(
                get_continuation_activity=lambda: "continuation"
            ),
            validate=lambda: None,
        )

        class Adapter:
            async def continue_conversation(
                self,
                app_id,
                continuation,
                callback,
            ):
                self.app_id = app_id
                await callback(SimpleNamespace(
                    send_activity=lambda activity: asyncio.sleep(
                        0,
                        result={"id": "activity-2"},
                    )
                ))

        adapter = Adapter()
        with (
            patch(
                "bridge.proactive_delivery.Conversation.from_json_to_store_item",
                return_value=fake_conversation,
            ),
            patch.dict(
                os.environ,
                {"AGENT365_BLUEPRINT_CLIENT_ID": "blueprint-app-id"},
            ),
        ):
            result = asyncio.run(send_proactive_activity(
                adapter,
                {
                    "boundary": "one_to_one",
                    "conversation": {"conversation_reference": {}},
                },
                "Scheduled hello",
            ))

        self.assertEqual(adapter.app_id, "blueprint-app-id")
        self.assertEqual(result["activityId"], "activity-2")

    def test_proactive_delivery_rejects_null_activity_id(self):
        fake_conversation = SimpleNamespace(
            claims={"aud": "app-1"},
            conversation_reference=SimpleNamespace(
                get_continuation_activity=lambda: "continuation"
            ),
            validate=lambda: None,
        )

        class Adapter:
            async def continue_conversation_with_claims(
                self,
                identity,
                continuation,
                callback,
            ):
                await callback(SimpleNamespace(
                    send_activity=lambda activity: asyncio.sleep(
                        0,
                        result={"id": None},
                    )
                ))

        with patch(
            "bridge.proactive_delivery.Conversation.from_json_to_store_item",
            return_value=fake_conversation,
        ):
            with self.assertRaisesRegex(RuntimeError, "no activity ID"):
                asyncio.run(send_proactive_activity(
                    Adapter(),
                    {
                        "boundary": "one_to_one",
                        "conversation": {"conversation_reference": {}},
                    },
                    "Scheduled hello",
                ))

    def test_invalid_service_bus_message_is_dead_lettered(self):
        previous = os.environ.get("WORKER_ID")
        os.environ["WORKER_ID"] = "hermes2"
        try:
            consumer = ServiceBusScheduleConsumer(
                handler=lambda payload: asyncio.sleep(0)
            )
            receiver = FakeReceiver()
            consumer._process(
                receiver,
                FakeReceivedMessage({
                    "version": "2.0",
                    "type": "hermes.cron.fire",
                    "workerId": "hermes2",
                }),
            )
        finally:
            if previous is None:
                os.environ.pop("WORKER_ID", None)
            else:
                os.environ["WORKER_ID"] = previous

        self.assertEqual(len(receiver.dead_lettered), 1)
        self.assertEqual(
            receiver.dead_lettered[0][1]["reason"],
            "invalid_schedule_message",
        )

    def test_cron_execution_receipt_retries_delivery_without_rerunning(self):
        job = {
            "id": "job-1",
            "prompt": "private scheduled prompt",
            "schedule": {"kind": "interval", "minutes": 3},
            "next_run_at": "2026-07-22T17:00:00+00:00",
            "enabled": True,
            "state": "scheduled",
        }
        revision = "revision-1"
        fire_count = 0

        with tempfile.TemporaryDirectory(dir=RUNTIME_DIR.parent.parent) as temp_dir:
            profile = Path(temp_dir)
            cron_modules = {
                "cron": types.ModuleType("cron"),
                "cron.jobs": types.ModuleType("cron.jobs"),
                "cron.scheduler_provider": types.ModuleType("cron.scheduler_provider"),
                "azure_cron_provider": types.ModuleType("azure_cron_provider"),
            }
            cron_modules["cron.jobs"].get_job = lambda job_id: job

            class Provider:
                def fire_due(self, job_id, *, output_callback):
                    nonlocal fire_count
                    fire_count += 1
                    output_dir = profile / "cron" / "output" / job_id
                    output_dir.mkdir(parents=True)
                    output_path = output_dir / "2026-07-22_17-00-00.md"
                    output_path.write_text(
                        "# Cron Job\n\n## Prompt\n\nprivate scheduled prompt"
                        "\n\n## Response\n\nScheduled hello",
                        encoding="utf-8",
                    )
                    output_callback(output_path)
                    return True

            cron_modules["cron.scheduler_provider"].resolve_cron_scheduler = Provider
            cron_modules["azure_cron_provider"].schedule_revision = lambda value: revision
            (profile / "local").mkdir()
            cron_runtime.atomic_write_json(
                cron_runtime.delivery_references_path(profile),
                {
                    "ref-1": {
                        "boundary": "public_channel",
                        "conversation": {"claims": {}},
                    }
                },
            )
            cron_runtime.atomic_write_json(
                cron_runtime.cron_delivery_path(profile),
                {"job-1": {"referenceKey": "ref-1"}},
            )

            with patch.dict(sys.modules, cron_modules):
                first = cron_runtime.fire_cron_job(
                    profile,
                    job_id="job-1",
                    revision=revision,
                )
                retry = cron_runtime.fire_cron_job(
                    profile,
                    job_id="job-1",
                    revision=revision,
                )
                cron_runtime.acknowledge_cron_delivery(
                    profile,
                    job_id="job-1",
                    revision=revision,
                    delivery_activity_id="original-activity",
                )
                original_receipt = cron_runtime.read_json_object(
                    cron_runtime.cron_delivery_receipts_path(profile)
                )["job-1:revision-1"]
                cron_runtime.acknowledge_cron_delivery(
                    profile, job_id="job-1", revision=revision,
                    delivery_activity_id="another-activity",
                )
                duplicate = cron_runtime.fire_cron_job(
                    profile,
                    job_id="job-1",
                    revision=revision,
                )
                stored_receipt = cron_runtime.read_json_object(
                    cron_runtime.cron_delivery_receipts_path(profile)
                )["job-1:revision-1"]

        self.assertEqual(first["output"], "Scheduled hello")
        self.assertNotIn("private scheduled prompt", first["output"])
        self.assertEqual(retry["status"], "pending_delivery")
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(fire_count, 1)
        self.assertEqual(stored_receipt["output"], "")
        self.assertIsNone(stored_receipt["deliveryReference"])
        self.assertEqual(stored_receipt, original_receipt)
        self.assertTrue(stored_receipt["hasOutput"])
        self.assertEqual(stored_receipt["deliveryActivityId"], "original-activity")
        self.assertEqual(
            stored_receipt["outputSha256"],
            hashlib.sha256(b"Scheduled hello").hexdigest(),
        )

    def test_interrupted_receipt_recovers_completed_output_without_rerunning(self):
        job = {
            "id": "job-1",
            "schedule": {"kind": "interval", "minutes": 3},
            "next_run_at": "2026-07-22T17:03:00+00:00",
            "enabled": True,
            "state": "scheduled",
        }
        revision = "revision-1"

        with tempfile.TemporaryDirectory(dir=RUNTIME_DIR.parent.parent) as temp_dir:
            profile = Path(temp_dir)
            output_dir = profile / "cron" / "output" / "job-1"
            output_dir.mkdir(parents=True)
            previous = output_dir / "2026-07-22_17-00-00.md"
            previous.write_text("previous", encoding="utf-8")
            receipt = {
                "output": "",
                "deliveryReference": None,
                "deliveryMode": "local",
                "delivered": False,
                "state": "executing",
                "lastRunAtBefore": None,
                "executionId": "execution-1",
            }
            cron_runtime.atomic_write_json(
                cron_runtime.cron_delivery_receipts_path(profile),
                {"job-1:revision-1": receipt},
            )
            output_path = output_dir / "2026-07-22_17-03-00.md"
            output_path.write_text(
                "# Cron Job\n\n## Response\n\nRecovered output",
                encoding="utf-8",
            )
            cron_runtime._record_cron_output(
                profile, job_id="job-1", revision=revision,
                execution_id="execution-1", output_path=output_path,
            )
            (output_dir / "2026-07-22_17-06-00.md").write_text(
                "# Cron Job\n\n## Response\n\nLater occurrence output",
                encoding="utf-8",
            )
            cron_modules = {
                "cron": types.ModuleType("cron"),
                "cron.jobs": types.ModuleType("cron.jobs"),
                "cron.scheduler_provider": types.ModuleType("cron.scheduler_provider"),
                "azure_cron_provider": types.ModuleType("azure_cron_provider"),
            }
            cron_modules["cron.jobs"].get_job = lambda job_id: job
            cron_modules["cron.scheduler_provider"].resolve_cron_scheduler = lambda: SimpleNamespace(
                reconcile=lambda: None,
                fire_due=lambda job_id: (_ for _ in ()).throw(
                    AssertionError("must not rerun")
                ),
            )
            cron_modules["azure_cron_provider"].schedule_revision = lambda value: revision

            with patch.dict(sys.modules, cron_modules):
                recovered = cron_runtime.fire_cron_job(
                    profile,
                    job_id="job-1",
                    revision=revision,
                )

        self.assertEqual(recovered["status"], "pending_delivery")
        self.assertEqual(recovered["output"], "Recovered output")

    def test_processing_failure_is_delivered_instead_of_marked_duplicate(self):
        initial = {
            "id": "job-1",
            "schedule": {"kind": "interval", "minutes": 3},
            "next_run_at": "2026-07-22T17:03:00+00:00",
            "last_run_at": None,
            "enabled": True,
            "state": "scheduled",
            "deliver": "local",
        }
        failed = {
            **initial,
            "last_run_at": "2026-07-22T17:03:01+00:00",
            "last_status": "error",
        }
        jobs = iter([initial, failed])
        cron_modules = {
            "cron": types.ModuleType("cron"),
            "cron.jobs": types.ModuleType("cron.jobs"),
            "cron.scheduler_provider": types.ModuleType("cron.scheduler_provider"),
            "azure_cron_provider": types.ModuleType("azure_cron_provider"),
        }
        cron_modules["cron.jobs"].get_job = lambda job_id: next(jobs)
        cron_modules["cron.scheduler_provider"].resolve_cron_scheduler = (
            lambda: SimpleNamespace(fire_due=lambda job_id, **kwargs: False)
        )
        cron_modules["azure_cron_provider"].schedule_revision = lambda value: "revision-1"

        with tempfile.TemporaryDirectory(dir=RUNTIME_DIR.parent.parent) as temp_dir:
            with patch.dict(sys.modules, cron_modules):
                result = cron_runtime.fire_cron_job(
                    Path(temp_dir),
                    job_id="job-1",
                    revision="revision-1",
                )

        self.assertEqual(result["status"], "completed")
        self.assertIn("scheduled task failed", result["output"].lower())

    def test_active_execution_receipt_stays_in_progress(self):
        job = {
            "id": "job-1",
            "schedule": {"kind": "interval", "minutes": 3},
            "next_run_at": "2026-07-22T17:03:00+00:00",
            "last_run_at": None,
            "enabled": True,
            "state": "scheduled",
            "fire_claim": {"at": "2026-07-22T17:00:00+00:00", "by": "worker"},
        }
        receipt = {
            "output": "",
            "deliveryReference": None,
            "deliveryMode": "local",
            "delivered": False,
            "state": "executing",
            "lastRunAtBefore": None,
            "outputFingerprintBefore": "",
            "startedAtEpoch": time.time(),
            "reconciled": False,
        }
        cron_modules = {
            "cron": types.ModuleType("cron"),
            "cron.jobs": types.ModuleType("cron.jobs"),
            "cron.scheduler_provider": types.ModuleType("cron.scheduler_provider"),
            "azure_cron_provider": types.ModuleType("azure_cron_provider"),
        }
        cron_modules["cron.jobs"].get_job = lambda job_id: job
        cron_modules["cron.scheduler_provider"].resolve_cron_scheduler = (
            lambda: (_ for _ in ()).throw(AssertionError("must not reconcile"))
        )
        cron_modules["azure_cron_provider"].schedule_revision = lambda value: "revision-1"

        with tempfile.TemporaryDirectory(dir=RUNTIME_DIR.parent.parent) as temp_dir:
            profile = Path(temp_dir)
            cron_runtime.atomic_write_json(
                cron_runtime.cron_delivery_receipts_path(profile),
                {"job-1:revision-1": receipt},
            )
            with patch.dict(sys.modules, cron_modules):
                result = cron_runtime.fire_cron_job(
                    profile,
                    job_id="job-1",
                    revision="revision-1",
                )

        self.assertEqual(result["status"], "in_progress")

    def test_delivered_receipts_are_bounded_per_job(self):
        receipts = {
            f"job-1:revision-{index:02d}": {
                "delivered": True,
                "deliveredAt": f"2026-07-22T17:{index:02d}:00+00:00",
            }
            for index in range(25)
        }
        receipts["job-1:pending"] = {"delivered": False, "state": "pending_delivery"}
        receipts.update({
            f"job-2:revision-{index:02d}": {
                "delivered": False,
                "state": "pending_delivery",
                "startedAtEpoch": float(index),
            }
            for index in range(25)
        })
        receipts["job-2:executing"] = {
            "delivered": False,
            "state": "executing",
            "startedAtEpoch": 1.0,
        }

        cron_runtime._prune_delivery_receipts(receipts, keep_delivered_per_job=20)

        delivered = [
            value for value in receipts.values() if value.get("delivered")
        ]
        self.assertEqual(len(delivered), 20)
        self.assertIn("job-1:pending", receipts)
        self.assertEqual(
            len([
                key
                for key, value in receipts.items()
                if key.startswith("job-2:")
                and value.get("state") == "pending_delivery"
            ]),
            25,
        )
        self.assertIn("job-2:executing", receipts)

    def test_system_dream_schedule_claims_completes_and_rearms(self):
        job = {
            "id": "dream-job",
            "name": "Renamed by user",
            "prompt": "Changed by user",
            "schedule": {"kind": "cron", "expr": "0 2 * * *"},
            "schedule_display": "0 2 * * *",
            "repeat": {"times": None, "completed": 2},
            "next_run_at": "2026-07-25T02:00:00+00:00",
            "enabled": True,
            "state": "scheduled",
            "last_run_at": None,
        }
        jobs = [job]
        marked = []
        reconciled = []
        cron_modules = {
            "cron": types.ModuleType("cron"),
            "cron.jobs": types.ModuleType("cron.jobs"),
            "cron.scheduler_provider": types.ModuleType("cron.scheduler_provider"),
            "azure_cron_provider": types.ModuleType("azure_cron_provider"),
        }
        cron_modules["cron.jobs"].list_jobs = lambda include_disabled=True: list(jobs)
        cron_modules["cron.jobs"].create_job = lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("existing system job should be reused")
        )
        cron_modules["cron.jobs"].remove_job = lambda job_id: True
        def update_job(job_id, updates):
            job.update(updates)
            return job

        cron_modules["cron.jobs"].update_job = update_job
        cron_modules["cron.jobs"].get_job = lambda job_id: job
        cron_modules["cron.jobs"].claim_job_for_fire = lambda job_id: True
        cron_modules["cron.jobs"].mark_job_run = (
            lambda job_id, success, error=None: marked.append(
                (job_id, success, error)
            )
        )
        cron_modules["cron.scheduler_provider"].resolve_cron_scheduler = (
            lambda: SimpleNamespace(
                reconcile=lambda: reconciled.append(True)
            )
        )
        cron_modules["azure_cron_provider"].schedule_revision = (
            lambda value: "dream-revision"
        )

        with tempfile.TemporaryDirectory(dir=RUNTIME_DIR.parent.parent) as temp_dir:
            profile = Path(temp_dir)
            cron_runtime.atomic_write_json(
                cron_runtime.cron_delivery_path(profile),
                {"dream-job": {"systemType": "dream"}},
            )
            with patch.dict(sys.modules, cron_modules):
                ensured = cron_runtime.ensure_system_dream_schedule(
                    profile,
                    enabled=True,
                    schedule="0 2 * * *",
                )
                user_jobs = cron_runtime.list_cron_jobs(profile)
                all_jobs = cron_runtime.list_cron_jobs(
                    profile,
                    include_system=True,
                )
                claimed = cron_runtime.claim_system_schedule(
                    profile,
                    job_id="dream-job",
                    revision="dream-revision",
                    occurrence_id="dream-revision",
                )
                self._prepare_dream_checkpoint(profile, claimed)
                completed = cron_runtime.complete_system_schedule(
                    profile,
                    job_id="dream-job",
                    revision="dream-revision",
                    occurrence_id="dream-revision",
                    success=True,
                    summary={"dream": {"recordCount": 1}},
                    owner_token=claimed["ownerToken"],
                )
                production_claim = cron_runtime.claim_system_schedule(
                    profile,
                    job_id="dream-job",
                    revision="dream-revision",
                    occurrence_id="dream-revision",
                )
            binding = cron_runtime.read_json_object(
                cron_runtime.cron_delivery_path(profile)
            )["dream-job"]

        self.assertTrue(ensured["enabled"])
        self.assertEqual(job["name"], cron_runtime.SYSTEM_DREAM_JOB_NAME)
        self.assertEqual(job["prompt"], cron_runtime.SYSTEM_DREAM_PROMPT)
        self.assertEqual(user_jobs, [])
        self.assertEqual(all_jobs[0]["systemType"], "dream")
        self.assertEqual(all_jobs[0]["schedule"], {"kind": "cron", "expr": "0 2 * * *"})
        self.assertEqual(all_jobs[0]["repeat"], {"times": None, "completed": 2})
        self.assertEqual(binding, {"systemType": "dream"})
        self.assertEqual(claimed["status"], "claimed")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(production_claim["status"], "duplicate")
        self.assertEqual(marked, [("dream-job", True, None)])
        self.assertEqual(reconciled, [True])

    def test_system_completion_recovery_does_not_advance_twice(self):
        job = {
            "id": "dream-job",
            "last_run_at": "2026-07-24T07:00:00+00:00",
        }
        marked = []
        reconciled = []
        cron_modules = {
            "cron": types.ModuleType("cron"),
            "cron.jobs": types.ModuleType("cron.jobs"),
            "cron.scheduler_provider": types.ModuleType("cron.scheduler_provider"),
            "azure_cron_provider": types.ModuleType("azure_cron_provider"),
        }
        cron_modules["cron.jobs"].get_job = lambda job_id: job
        cron_modules["cron.jobs"].claim_job_for_fire = lambda job_id: True
        cron_modules["cron.jobs"].mark_job_run = (
            lambda *args, **kwargs: marked.append((args, kwargs))
        )
        cron_modules["cron.scheduler_provider"].resolve_cron_scheduler = (
            lambda: SimpleNamespace(
                reconcile=lambda: reconciled.append(True)
            )
        )
        cron_modules["azure_cron_provider"].schedule_revision = (
            lambda value: "new-revision"
        )

        with tempfile.TemporaryDirectory(dir=RUNTIME_DIR.parent.parent) as temp_dir:
            profile = Path(temp_dir)
            cron_runtime.atomic_write_json(
                cron_runtime.system_schedule_receipts_path(profile),
                {
                    "dream-job:old-revision": {
                        "state": "completing",
                        "success": True,
                        "lastRunAtBefore": None,
                        "summary": {},
                    }
                },
            )
            with patch.dict(sys.modules, cron_modules):
                result = cron_runtime.claim_system_schedule(
                    profile,
                    job_id="dream-job",
                    revision="old-revision",
                    occurrence_id="old-revision",
                )
            receipt = cron_runtime.read_json_object(
                cron_runtime.system_schedule_receipts_path(profile)
            )["dream-job:old-revision"]

        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(marked, [])
        self.assertEqual(reconciled, [True])
        self.assertEqual(receipt["state"], "completed")

    def test_expired_running_dream_is_not_executed_twice(self):
        cron_modules = {
            "cron": types.ModuleType("cron"),
            "cron.jobs": types.ModuleType("cron.jobs"),
            "azure_cron_provider": types.ModuleType("azure_cron_provider"),
        }
        cron_modules["cron.jobs"].get_job = lambda job_id: (_ for _ in ()).throw(
            AssertionError("must not reclaim an executing occurrence")
        )
        cron_modules["cron.jobs"].claim_job_for_fire = lambda job_id: (
            (_ for _ in ()).throw(
                AssertionError("must not reclaim an executing occurrence")
            )
        )
        cron_modules["azure_cron_provider"].schedule_revision = (
            lambda value: "dream-revision"
        )

        with tempfile.TemporaryDirectory(dir=RUNTIME_DIR.parent.parent) as temp_dir:
            profile = Path(temp_dir)
            cron_runtime.atomic_write_json(
                cron_runtime.system_schedule_receipts_path(profile),
                {
                    "dream-job:manual-1": {
                        "state": "running",
                        "startedAtEpoch": 1,
                        "revision": "dream-revision",
                        "occurrenceId": "manual-1",
                    }
                },
            )
            with patch.dict(sys.modules, cron_modules):
                result = cron_runtime.claim_system_schedule(
                    profile,
                    job_id="dream-job",
                    revision="dream-revision",
                    occurrence_id="manual-1",
                )

        self.assertEqual(result["status"], "interrupted")

    def test_unbound_later_output_is_not_recovered_for_old_occurrence(self):
        with tempfile.TemporaryDirectory(dir=RUNTIME_DIR.parent.parent) as directory:
            profile = Path(directory)
            output_dir = profile / "cron" / "output" / "job-1"
            output_dir.mkdir(parents=True)
            (output_dir / "later.md").write_text(
                "# Cron Job\n\n## Response\n\nNot this occurrence", encoding="utf-8"
            )
            cron_runtime.atomic_write_json(cron_runtime.cron_delivery_receipts_path(profile), {
                "job-1:revision-1": {
                    "state": "executing", "executionId": "old-execution",
                    "startedAtEpoch": 1, "delivered": False,
                }
            })
            modules = {
                "cron.jobs": SimpleNamespace(get_job=lambda _: None),
                "cron.scheduler_provider": SimpleNamespace(
                    resolve_cron_scheduler=lambda: SimpleNamespace(reconcile=lambda: None)
                ),
                "azure_cron_provider": SimpleNamespace(schedule_revision=lambda _: "revision-1"),
            }
            with patch.dict(sys.modules, modules):
                result = cron_runtime.fire_cron_job(profile, job_id="job-1", revision="revision-1")
        self.assertEqual(result["status"], "pending_delivery")
        self.assertEqual(result["lastStatus"], "error")
        self.assertNotIn("Not this occurrence", result["output"])
        self.assertIn("no unambiguous output", result["output"])

    def test_native_output_checkpoint_survives_provider_reconcile_failure(self):
        job = {
            "id": "job-1", "next_run_at": "2026-09-01T00:00:00+00:00",
            "last_run_at": None, "enabled": True,
        }
        native_runs = []
        with tempfile.TemporaryDirectory(dir=RUNTIME_DIR.parent.parent) as directory:
            profile = Path(directory)
            def save_output(job_id, output):
                path = profile / "cron" / "output" / job_id / "native-output.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(output, encoding="utf-8")
                return path

            def native_run(job, **kwargs):
                scheduler.save_job_output(
                    job["id"], "# Cron Job\n\n## Response\n\nNative owned output"
                )
                return True
            scheduler = SimpleNamespace(save_job_output=save_output, run_one_job=native_run)

            class NativeCronScheduler:
                def fire_due(self, job_id, **kwargs):
                    native_runs.append(job_id)
                    scheduler.run_one_job(
                        {"id": job_id, "execution_id": "native-execution"},
                    )
                    job["last_status"] = "success"
                    return True

            native_module = SimpleNamespace(
                CronScheduler=NativeCronScheduler, resolve_cron_scheduler=lambda: provider
            )
            modules = {
                "cron": SimpleNamespace(scheduler=scheduler),
                "cron.scheduler_provider": native_module,
                "cron.jobs": SimpleNamespace(get_job=lambda _: job),
            }
            with patch.dict(sys.modules, modules):
                spec = importlib.util.spec_from_file_location(
                    "_test_azure_provider", RUNTIME_DIR / "azure_cron_provider.py"
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                provider = module.AzureCronScheduler()
                with (
                    patch.dict(sys.modules, {"azure_cron_provider": module}),
                    patch.object(provider, "reconcile", side_effect=[RuntimeError("broker unavailable"), None]),
                ):
                    revision = module.schedule_revision(job)
                    with self.assertRaisesRegex(RuntimeError, "broker unavailable"):
                        cron_runtime.fire_cron_job(profile, job_id="job-1", revision=revision)
                    recovered = cron_runtime.fire_cron_job(
                        profile, job_id="job-1", revision=revision
                    )
                    scheduler.save_job_output(
                        "job-1", "# Cron Job\n\n## Response\n\nAnother native run"
                    )
                    stored = cron_runtime.read_json_object(
                        cron_runtime.cron_delivery_receipts_path(profile)
                    )[f"job-1:{revision}"]
        self.assertEqual(native_runs, ["job-1"])
        self.assertEqual(recovered["output"], "Native owned output")
        self.assertEqual(stored["output"], "Native owned output")
        self.assertTrue(stored["nativeOutputPath"])
        self.assertTrue(stored["nativeOutputSha256"])
        self.assertEqual(stored["nativeExecutionId"], "native-execution")

    def test_atomic_receipt_transitions_preserve_concurrent_updates(self):
        with tempfile.TemporaryDirectory(dir=RUNTIME_DIR.parent.parent) as directory:
            profile = Path(directory)
            path = cron_runtime.cron_delivery_receipts_path(profile)
            cron_runtime.atomic_write_json(path, {
                f"job-{index}:revision": {
                    "state": "pending_delivery", "delivered": False, "output": str(index)
                } for index in range(30)
            })
            def acknowledge(index):
                return cron_runtime.acknowledge_cron_delivery(
                    profile, job_id=f"job-{index}", revision="revision",
                    delivery_activity_id=f"activity-{index}",
                )
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(acknowledge, range(30)))
            receipts = cron_runtime.read_json_object(path)
        self.assertEqual(len(receipts), 30)
        self.assertTrue(all(value["delivered"] for value in receipts.values()))

    def test_native_provider_injects_producer_trace_for_each_occurrence(self):
        sent = []

        class Sender:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def send_messages(self, message):
                span = trace.get_current_span()
                sent.append((message, span.get_span_context(), span.kind, dict(span.attributes)))

            def schedule_messages(self, message, due_at):
                self.send_messages(message)
                return [42]

        modules = {"cron.scheduler_provider": SimpleNamespace(CronScheduler=object)}
        with patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location(
                "_test_traced_azure_provider", RUNTIME_DIR / "azure_cron_provider.py"
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        provider = module.AzureCronScheduler()
        provider._client = SimpleNamespace(get_queue_sender=lambda _: Sender())
        tracer = TracerProvider().get_tracer("scheduler-test")
        with (
            patch.dict(os.environ, {"WORKER_ID": "worker", "SCHEDULER_SERVICEBUS_QUEUE": "queue"}),
            patch("bridge.telemetry.trace.get_tracer", return_value=tracer),
            tracer.start_as_current_span("upstream") as parent,
        ):
            job = {"id": "dream-job", "next_run_at": "2026-09-07T02:00:00Z"}
            self.assertEqual(provider._schedule(job, "revision", {"systemType": "dream"}), 42)
            provider.enqueue_now(job, "revision", {"systemType": "dream"}, occurrence_id="manual")
            parent_context = parent.get_span_context()
        for message, context, kind, _ in sent:
            traceparent = message.application_properties["traceparent"].split("-")
            self.assertEqual(int(traceparent[1], 16), parent_context.trace_id)
            self.assertEqual(int(traceparent[2], 16), context.span_id)
            self.assertNotEqual(context.span_id, parent_context.span_id)
            self.assertEqual(kind, module.SpanKind.PRODUCER)
            self.assertNotIn("baggage", message.application_properties)
        self.assertNotEqual(
            sent[0][3]["autopilots.operation.id"], sent[1][3]["autopilots.operation.id"],
        )

    def test_manual_dream_preserves_actual_production_next_run(self):
        job = {
            "id": "dream-job", "name": cron_runtime.SYSTEM_DREAM_JOB_NAME,
            "prompt": cron_runtime.SYSTEM_DREAM_PROMPT, "last_run_at": None,
            "next_run_at": "2026-09-06T02:00:00+00:00",
        }
        scheduled_next_run = job["next_run_at"]
        def mutate_production(*args, **kwargs):
            job["next_run_at"] = "2026-09-07T02:00:00+00:00"
            raise AssertionError("Ad-hoc execution must not claim or complete the production job.")
        enqueued = []
        def enqueue(job, revision, binding, *, occurrence_id):
            enqueued.append(occurrence_id)
            return {"occurrenceId": occurrence_id, "messageId": "manual-message"}
        modules = {
            "cron.jobs": SimpleNamespace(
                get_job=lambda _: job, list_jobs=lambda **_: [job],
                claim_job_for_fire=mutate_production, mark_job_run=mutate_production,
            ),
            "cron.scheduler_provider": SimpleNamespace(
                resolve_cron_scheduler=lambda: SimpleNamespace(
                    enqueue_now=enqueue, reconcile=mutate_production
                )
            ),
            "azure_cron_provider": SimpleNamespace(schedule_revision=lambda _: "revision-1"),
        }
        with tempfile.TemporaryDirectory(dir=RUNTIME_DIR.parent.parent) as directory:
            profile = Path(directory)
            cron_runtime.atomic_write_json(
                cron_runtime.cron_delivery_path(profile), {"dream-job": {"systemType": "dream"}}
            )
            with patch.dict(sys.modules, modules):
                queued = cron_runtime.enqueue_system_dream_now(profile)
                claimed = cron_runtime.claim_system_schedule(
                    profile, job_id="dream-job", revision="revision-1",
                    occurrence_id=queued["occurrenceId"],
                )
                self._prepare_dream_checkpoint(profile, claimed)
                with patch.object(cron_runtime.time, "time", return_value=1788660600):
                    completed = cron_runtime.complete_system_schedule(
                        profile, job_id="dream-job", revision="revision-1",
                        occurrence_id=queued["occurrenceId"], success=True,
                        owner_token=claimed["ownerToken"],
                    )
                repeated = cron_runtime.claim_system_schedule(
                    profile, job_id="dream-job", revision="revision-1",
                    occurrence_id=queued["occurrenceId"],
                )
        self.assertEqual(queued["scheduledNextRunAt"], scheduled_next_run)
        self.assertEqual(completed["nextRunAt"], scheduled_next_run)
        self.assertEqual(job["next_run_at"], scheduled_next_run)
        self.assertIsNone(job["last_run_at"])
        self.assertEqual(claimed["operationKind"], "adhoc")
        self.assertEqual(repeated["status"], "duplicate")

    @staticmethod
    def _prepare_dream_checkpoint(profile, operation):
        phases = ["pending", "dream_started", "dream_completed", "status_completed", "prepared"]
        for before, after in zip(phases, phases[1:]):
            cron_runtime.checkpoint_system_schedule(
                profile, job_id=operation["jobId"], revision=operation["revision"],
                occurrence_id=operation["occurrenceId"], owner_token=operation["ownerToken"],
                expected_phase=before, phase=after, payload={},
            )

    def test_checkpoint_commit_retry_is_idempotent_and_stale_owner_is_fenced(self):
        with tempfile.TemporaryDirectory(dir=RUNTIME_DIR.parent.parent) as directory:
            profile = Path(directory)
            receipt = cron_runtime._new_system_operation(
                {"id": "dream-job"}, "revision", "occurrence", "adhoc"
            )
            receipt["state"] = "running"
            cron_runtime.atomic_write_json(
                cron_runtime.system_schedule_receipts_path(profile),
                {"dream-job:occurrence": receipt},
            )
            arguments = {
                "job_id": "dream-job", "revision": "revision", "occurrence_id": "occurrence",
                "owner_token": receipt["ownerToken"],
                "expected_phase": "pending", "phase": "dream_started", "payload": {},
            }
            first = cron_runtime.checkpoint_system_schedule(profile, **arguments)
            retry = cron_runtime.checkpoint_system_schedule(profile, **arguments)
            self.assertEqual(first, retry)
            with self.assertRaisesRegex(RuntimeError, "prepared checkpoint"):
                cron_runtime.complete_system_schedule(
                    profile, job_id="dream-job", revision="revision", occurrence_id="occurrence",
                    owner_token=receipt["ownerToken"], success=True,
                )
            with self.assertRaisesRegex(RuntimeError, "ownership"):
                cron_runtime.checkpoint_system_schedule(
                    profile, **{**arguments, "owner_token": "old-owner"}
                )
            with self.assertRaisesRegex(RuntimeError, "conflicting"):
                cron_runtime.checkpoint_system_schedule(
                    profile, **{**arguments, "payload": {"different": "payload"}}
                )

    def test_expired_completed_dream_resumes_with_new_owner_and_same_session(self):
        receipt = cron_runtime._new_system_operation(
            {"id": "dream-job"}, "revision", "occurrence", "adhoc"
        )
        receipt.update({"state": "running", "phase": "dream_completed", "startedAtEpoch": 1})
        modules = {
            "cron.jobs": SimpleNamespace(
                get_job=lambda _: None,
                claim_job_for_fire=lambda _: self.fail("Must not reclaim native job"),
            ),
            "azure_cron_provider": SimpleNamespace(schedule_revision=lambda _: ""),
        }
        with tempfile.TemporaryDirectory(dir=RUNTIME_DIR.parent.parent) as directory:
            profile = Path(directory)
            cron_runtime.atomic_write_json(cron_runtime.system_schedule_receipts_path(profile), {
                "dream-job:occurrence": receipt,
            })
            with patch.dict(sys.modules, modules):
                result = cron_runtime.claim_system_schedule(
                    profile, job_id="dream-job", revision="revision", occurrence_id="occurrence"
                )
        self.assertEqual(result["status"], "claimed")
        self.assertEqual(result["phase"], "dream_completed")
        self.assertEqual(result["sessionId"], receipt["sessionId"])
        self.assertNotEqual(result["ownerToken"], receipt["ownerToken"])


if __name__ == "__main__":
    unittest.main()
