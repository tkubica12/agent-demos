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
from unittest.mock import AsyncMock, MagicMock, patch

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

import bridge.app as bridge_app
from bridge.proactive_delivery import delivery_reference_key, send_proactive_activity
from bridge.servicebus_scheduler import ServiceBusScheduleSender


RUNTIME_DIR = Path(__file__).resolve().parents[1] / "runtimes" / "hermes"
sys.path.insert(0, str(RUNTIME_DIR))

import cron_runtime  # noqa: E402


class UserSchedulingTests(unittest.TestCase):
    def test_completed_dream_retires_consumed_schedule_before_reconciling_next_occurrence(self):
        calls = []

        def forget(*args):
            calls.append(("retire", args))

        def reconcile():
            self.assertEqual(calls, [("retire", ("dream", "revision", "2026-11-13T02:00:00+00:00"))])
            calls.append(("reconcile",))

        provider = SimpleNamespace(forget_fired_occurrence=forget, reconcile=reconcile)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(sys.modules, {
                "cron.jobs": SimpleNamespace(
                    get_job=lambda job_id: {"last_run_at": "already-marked", "next_run_at": "tomorrow"},
                    mark_job_run=lambda *args: self.fail("Completed Dream was marked twice"),
                ),
                "cron.scheduler_provider": SimpleNamespace(resolve_cron_scheduler=lambda: provider),
            }),
        ):
            home = Path(directory)
            cron_runtime.atomic_write_json(cron_runtime.system_schedule_receipts_path(home), {
                "dream:revision": {
                    "state": "completing", "operationKind": "scheduled", "revision": "revision",
                    "scheduledNextRunAt": "2026-11-13T02:00:00+00:00", "lastRunAtBefore": None,
                    "success": True,
                },
            })
            result = cron_runtime._finish_system_schedule_completion(
                home, job_id="dream", receipt_key="dream:revision",
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(calls[-1], ("reconcile",))

    def test_owner_known_result_delivery_never_calls_native_fire(self):
        from unittest.mock import AsyncMock

        adapter = SimpleNamespace(
            known_cron_result=AsyncMock(return_value={
                "status": "pending_delivery", "output": "Stored reminder",
                "deliveryReference": {"boundary": "one_to_one", "conversation": {}},
            }),
            acknowledge_cron_delivery=AsyncMock(),
            fire_cron_job=AsyncMock(),
        )

        async def effect(_name, operation):
            return await operation()

        with (
            patch.object(bridge_app, "runtime_adapter", return_value=adapter),
            patch.object(bridge_app.event_worker, "external_effect", side_effect=effect),
            patch.object(bridge_app, "send_proactive_activity", return_value={"activityId": "teams-ack"}) as send,
        ):
            result = asyncio.run(bridge_app.deliver_known_schedule_result(
                {"jobId": "job", "revision": "revision"},
                {"executionId": "execution", "outputSha256": "a" * 64},
            ))
        self.assertEqual(result["deliveryActivityId"], "teams-ack")
        adapter.fire_cron_job.assert_not_awaited()
        adapter.acknowledge_cron_delivery.assert_awaited_once_with(
            job_id="job", revision="revision", delivery_activity_id="teams-ack",
        )
        self.assertEqual(send.await_args.args[2], "Stored reminder")

    def test_known_result_never_claims_or_executes_and_checks_exact_native_output(self):
        output = "Stored reminder"
        receipt = {
            "nativeExecutionId": "native-execution", "nativeOutputSha256": "file-digest",
            "executionId": "receipt-execution", "output": output, "state": "pending_delivery",
        }
        digest = hashlib.sha256(output.encode()).hexdigest()
        with (
            patch.object(cron_runtime, "read_json_object", return_value={"job:revision": receipt}),
            patch.object(cron_runtime, "_cron_execution_outcome", return_value={"state": "pending_delivery"}),
            patch.object(cron_runtime, "_reconcile_delivery", return_value=receipt) as reconciled,
            patch.object(cron_runtime, "fire_cron_job") as inference,
        ):
            result = cron_runtime.known_cron_result(
                Path("profile"), job_id="job", revision="revision",
                execution_id="native-execution", output_sha256=digest,
            )
            self.assertEqual(result["status"], "pending_delivery")
            for identity, output_hash in (("other", digest), ("native-execution", "wrong")):
                with self.assertRaises(ValueError):
                    cron_runtime.known_cron_result(
                        Path("profile"), job_id="job", revision="revision",
                        execution_id=identity, output_sha256=output_hash,
                    )
            inference.assert_not_called()
            reconciled.assert_called_once()

    def test_acknowledged_known_result_retires_only_its_consumed_broker_occurrence(self):
        from unittest.mock import Mock

        provider = Mock()
        receipt = {
            "delivered": True, "nativeExecutionId": "execution", "outputSha256": "digest",
            "scheduledNextRunAt": "due", "deliveryActivityId": "ack",
        }
        with (
            patch.object(cron_runtime, "read_json_object", return_value={"job:revision": receipt}),
            patch.dict(sys.modules, {"cron.scheduler_provider": SimpleNamespace(
                resolve_cron_scheduler=lambda: provider,
            )}),
            patch.object(cron_runtime, "fire_cron_job") as inference,
        ):
            result = cron_runtime.known_cron_result(
                Path("profile"), job_id="job", revision="revision", execution_id="execution", output_sha256="digest",
            )
        self.assertEqual(result["status"], "delivered")
        provider.forget_fired_occurrence.assert_called_once_with("job", "revision", "due")
        provider.reconcile.assert_called_once()
        inference.assert_not_called()

    def test_completed_native_occurrence_retires_consumed_trigger_and_recovers_only_delivery(self):
        due_at = "2026-11-12T19:17:50.906717+00:00"
        native_id = "completed-native-execution"
        job = {
            "id": "job", "enabled": False, "state": "completed", "next_run_at": None,
            "last_run_at": "2026-11-12T19:18:28+00:00", "last_status": "ok",
            "deliver": "local", "origin": {"platform": "autopilots", "chat_id": "trusted-reference"},
        }
        with tempfile.TemporaryDirectory(dir=RUNTIME_DIR.parent.parent) as directory:
            profile = Path(directory)
            modules = {
                "cron": types.ModuleType("cron"),
                "cron.jobs": SimpleNamespace(
                    CRON_DIR=profile / "cron", load_jobs=lambda: [job], get_job=lambda _: job,
                ),
                "cron.scheduler_provider": SimpleNamespace(CronScheduler=object, resolve_cron_scheduler=lambda: provider),
                "cron.executions": SimpleNamespace(get_execution=lambda _: {
                    "id": native_id, "job_id": "job", "status": "completed",
                    "scheduled_instant": due_at, "delivery_outcome": "suppressed",
                }),
            }
            spec = importlib.util.spec_from_file_location(
                "azure_provider_completed_delivery_test", RUNTIME_DIR / "azure_cron_provider.py",
            )
            module = importlib.util.module_from_spec(spec)
            with patch.dict(sys.modules, modules):
                spec.loader.exec_module(module)
                provider = module.AzureCronScheduler()
                provider._client = MagicMock()
                provider._write_state({"job": {
                    "revision": "revision", "fireAt": due_at, "sequenceNumber": 120,
                }})
                cron_runtime.upsert_delivery_reference(
                    profile, reference_key="trusted-reference", conversation={}, boundary="one_to_one",
                )
                receipt_path = cron_runtime.cron_delivery_receipts_path(profile)
                cron_runtime.atomic_write_json(receipt_path, {"job:revision": {
                    "executionId": "gateway-execution", "state": "executing", "delivered": False,
                    "scheduledNextRunAt": due_at, "startedAtEpoch": 1, "reconciled": False,
                }})
                output_path = profile / "cron" / "output" / "job" / "native-owned-output.md"
                output_path.parent.mkdir(parents=True)
                output_path.write_text("# Cron Job\n\n## Response\n\nCompleted reminder", encoding="utf-8")
                cron_runtime._record_cron_output(
                    profile, job_id="job", revision="revision", execution_id="gateway-execution",
                    output_path=output_path, native_execution_id=native_id,
                )
                with (
                    patch.dict(sys.modules, {"azure_cron_provider": module}),
                    patch.object(provider, "fire_due", side_effect=AssertionError("must never rerun inference")) as fire,
                ):
                    result = cron_runtime.fire_cron_job(profile, job_id="job", revision="revision")
                    status = cron_runtime.cron_delivery_receipt_status(profile, job_id="job", revision="revision")
                self.assertEqual(result["status"], "pending_delivery")
                self.assertEqual(result["output"], "Completed reminder")
                self.assertEqual(result["lastStatus"], "ok")
                self.assertIsNone(result["nextRunAt"])
                self.assertEqual(status["status"], "pending_delivery")
                self.assertEqual(provider._load_state(), {})
                provider._client.get_queue_sender.assert_not_called()
                fire.assert_not_called()
                provider._write_state({"job": {
                    "revision": "newer-revision", "fireAt": due_at, "sequenceNumber": 121,
                }})
                provider.forget_fired_occurrence("job", "revision", due_at)
                self.assertEqual(provider._load_state()["job"]["sequenceNumber"], 121)

    def test_unverifiable_native_results_are_unknown_not_pending_or_reexecuted(self):
        due_at = "2026-11-12T19:17:50+00:00"
        valid = {"id": "native", "job_id": "job", "status": "completed", "scheduled_instant": due_at}
        with tempfile.TemporaryDirectory(dir=RUNTIME_DIR.parent.parent) as directory:
            profile = Path(directory)
            output_path = profile / "cron" / "output" / "job" / "owned.md"
            output_path.parent.mkdir(parents=True)
            content = "# Cron Job\n\n## Response\n\nKnown output"
            modules = {
                "cron": types.ModuleType("cron"),
                "cron.jobs": SimpleNamespace(get_job=lambda _: {"id": "job", "last_status": "ok"}),
                "cron.scheduler_provider": SimpleNamespace(resolve_cron_scheduler=MagicMock()),
                "azure_cron_provider": SimpleNamespace(schedule_revision=lambda _: "revision"),
            }
            for native, corrupt in (
                (None, False), ({**valid, "job_id": "another-job"}, False),
                ({**valid, "scheduled_instant": "2026-11-12T19:20:00+00:00"}, False),
                ({**valid, "scheduled_instant": "invalid"}, False), (valid, True),
            ):
                with self.subTest(native=native, corrupt=corrupt):
                    output_path.write_text(content, encoding="utf-8")
                    cron_runtime.atomic_write_json(cron_runtime.cron_delivery_receipts_path(profile), {"job:revision": {
                        "executionId": "gateway-execution", "state": "executing", "delivered": False,
                        "scheduledNextRunAt": due_at, "startedAtEpoch": 1,
                    }})
                    cron_runtime._record_cron_output(
                        profile, job_id="job", revision="revision", execution_id="gateway-execution",
                        output_path=output_path, native_execution_id="native",
                    )
                    if corrupt:
                        output_path.write_text("Another execution's output", encoding="utf-8")
                    with patch.dict(sys.modules, {
                        **modules, "cron.executions": SimpleNamespace(get_execution=lambda _: native),
                    }):
                        status = cron_runtime.cron_delivery_receipt_status(profile, job_id="job", revision="revision")
                        result = cron_runtime.fire_cron_job(profile, job_id="job", revision="revision")
                    self.assertEqual(status["status"], "unknown")
                    self.assertEqual(result["status"], "unknown")
            modules["cron.scheduler_provider"].resolve_cron_scheduler.assert_not_called()

    def test_native_change_hook_replaces_pauses_resumes_and_removes_broker_occurrences(self):
        events = []

        class Sender:
            sequence = 100
            cancellation_error = None

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def schedule_messages(self, message, due_at):
                self.sequence += 1
                events.append(("schedule", self.sequence))
                return [self.sequence]

            def cancel_scheduled_messages(self, sequence):
                if self.cancellation_error:
                    raise self.cancellation_error
                events.append(("cancel", sequence))

        with tempfile.TemporaryDirectory(dir=RUNTIME_DIR.parent.parent) as directory:
            profile = Path(directory)
            job = {
                "id": "job", "prompt": "original reminder", "enabled": True, "state": "scheduled",
                "next_run_at": "2030-01-01T10:00:00+00:00", "deliver": "local",
                "origin": {"platform": "autopilots", "chat_id": "trusted-reference"},
            }
            native_jobs = [job]
            modules = {
                "cron": types.ModuleType("cron"),
                "cron.jobs": SimpleNamespace(
                    CRON_DIR=profile / "cron", load_jobs=lambda: native_jobs,
                    get_job=lambda job_id: next((job for job in native_jobs if job["id"] == job_id), None),
                ),
                "cron.scheduler_provider": SimpleNamespace(CronScheduler=object),
            }
            spec = importlib.util.spec_from_file_location(
                "azure_provider_lifecycle_test", RUNTIME_DIR / "azure_cron_provider.py",
            )
            module = importlib.util.module_from_spec(spec)
            sender = Sender()
            with (
                patch.dict(sys.modules, modules),
                patch.dict(os.environ, {"WORKER_ID": "worker", "SCHEDULER_SERVICEBUS_QUEUE": "schedule"}),
            ):
                spec.loader.exec_module(module)
                provider = module.AzureCronScheduler()
                provider._client = SimpleNamespace(get_queue_sender=lambda _: sender)
                cron_runtime.upsert_delivery_reference(
                    profile, reference_key="trusted-reference", conversation={}, boundary="one_to_one",
                )
                with patch.object(provider, "_ensure_system_dream"):
                    provider.register_job(job)
                    job.update(prompt="edited reminder", next_run_at="2030-01-01T11:00:00+00:00")
                    provider.on_jobs_changed()
                    self.assertEqual(provider._load_state()["job"]["sequenceNumber"], 102)
                    job.update(enabled=False, state="paused")
                    sender.cancellation_error = ConnectionError("broker unavailable")
                    with self.assertRaisesRegex(ConnectionError, "broker unavailable"):
                        provider.on_jobs_changed()
                    self.assertEqual(provider._load_state()["job"]["sequenceNumber"], 102)
                    sender.cancellation_error = None
                    provider.on_jobs_changed()
                    self.assertEqual(provider._load_state(), {})
                    provider.on_jobs_changed()
                    job.update(enabled=True, state="scheduled", next_run_at="2030-01-01T12:00:00+00:00")
                    provider.on_jobs_changed()
                    self.assertEqual(provider._load_state()["job"]["sequenceNumber"], 103)
                    native_jobs.clear()
                    provider.on_jobs_changed()
                    self.assertEqual(provider._load_state(), {})
            self.assertEqual(events, [
                ("schedule", 101), ("cancel", 101), ("schedule", 102),
                ("cancel", 102), ("schedule", 103), ("cancel", 103),
            ])

    def test_provider_forwards_the_pinned_native_force_and_manual_fire_contract(self):
        native_fire = MagicMock(return_value=False)

        class NativeScheduler:
            fire_due = native_fire

        spec = importlib.util.spec_from_file_location(
            "azure_provider_fire_contract_test", RUNTIME_DIR / "azure_cron_provider.py",
        )
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"cron.scheduler_provider": SimpleNamespace(CronScheduler=NativeScheduler)}):
            spec.loader.exec_module(module)
        provider = module.AzureCronScheduler()
        with patch.object(module, "_install_output_capture"), patch.object(provider, "reconcile") as reconcile:
            self.assertFalse(provider.fire_due("job", force=True, manual=True))
        native_fire.assert_called_once_with("job", adapters=None, loop=None, force=True, manual=True)
        reconcile.assert_not_called()

    def test_cancellation_tolerates_only_the_native_missing_message_race(self):
        from azure.servicebus.exceptions import MessageNotFoundError

        spec = importlib.util.spec_from_file_location(
            "azure_provider_cancel_test", RUNTIME_DIR / "azure_cron_provider.py",
        )
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"cron.scheduler_provider": SimpleNamespace(CronScheduler=object)}):
            spec.loader.exec_module(module)
        provider = module.AzureCronScheduler()
        sender = MagicMock()
        sender.__enter__.return_value = sender
        provider._client = SimpleNamespace(get_queue_sender=lambda _: sender)
        with patch.dict(os.environ, {"SCHEDULER_SERVICEBUS_QUEUE": "schedule"}):
            sender.cancel_scheduled_messages.side_effect = MessageNotFoundError()
            provider._cancel(42)
            sender.cancel_scheduled_messages.side_effect = ConnectionError("broker unavailable")
            with self.assertRaisesRegex(ConnectionError, "broker unavailable"):
                provider._cancel(42)

    def test_operator_can_finish_an_expired_completed_dream_but_not_ambiguous_inference(self):
        modules = {
            "cron.jobs": SimpleNamespace(get_job=lambda _: None, mark_job_run=MagicMock()),
            "cron.scheduler_provider": SimpleNamespace(resolve_cron_scheduler=lambda: None),
        }
        with tempfile.TemporaryDirectory(dir=RUNTIME_DIR.parent.parent) as directory:
            profile = Path(directory)
            path = cron_runtime.system_schedule_receipts_path(profile)
            receipt = {
                "state": "running", "phase": "dream_started", "operationKind": "adhoc",
                "revision": "revision", "ownerToken": "owner", "startedAtEpoch": 1,
            }
            kwargs = dict(
                job_id="dream", revision="revision", occurrence_id="occurrence",
                operator="owner", reason="inspected completed inference; skip lost delivery", kind="dream",
            )
            cron_runtime.atomic_write_json(path, {"dream:occurrence": receipt})
            with self.assertRaisesRegex(ValueError, "inference-ambiguous"):
                cron_runtime.resolve_schedule(profile, **kwargs)
            receipt["phase"] = "status_completed"
            cron_runtime.atomic_write_json(path, {"dream:occurrence": receipt})
            with patch.dict(sys.modules, modules):
                result = cron_runtime.resolve_schedule(profile, **kwargs)
            self.assertEqual(result["status"], "skipped")
            stored = cron_runtime.read_json_object(path)["dream:occurrence"]
            self.assertEqual(stored["state"], "completed")
            self.assertEqual(stored["summary"]["outcome"], "skipped")
            modules["cron.jobs"].mark_job_run.assert_not_called()

    def test_completed_cron_delivery_recovery_returns_a_completed_transport_outcome(self):
        adapter = SimpleNamespace(
            fire_cron_job=AsyncMock(return_value={
                "status": "pending_delivery", "output": "Saved output", "deliveryMode": "local",
            }),
            acknowledge_cron_delivery=AsyncMock(return_value={"status": "delivered"}),
        )
        with patch.object(bridge_app, "runtime_adapter", return_value=adapter):
            outcome = asyncio.run(bridge_app.process_scheduled_message({
                "type": "hermes.cron.fire", "jobId": "job", "revision": "revision",
            }))
        self.assertEqual(outcome["status"], "completed")
        adapter.acknowledge_cron_delivery.assert_awaited_once()

    def test_unknown_dream_digest_is_terminalized_without_repeating_inference(self):
        adapter = SimpleNamespace(
            claim_system_schedule=AsyncMock(return_value={"status": "claimed", "ownerToken": "owner"}),
            complete_system_schedule=AsyncMock(),
        )
        with (
            patch.object(bridge_app, "runtime_adapter", return_value=adapter),
            patch.object(bridge_app.scheduled_learning, "run_once", return_value={"dream": {}}),
            patch.object(bridge_app.event_worker, "external_effect", side_effect=bridge_app.event_worker.AmbiguousEffectError("unknown digest")),
        ):
            with self.assertRaises(bridge_app.event_worker.AmbiguousEffectError):
                asyncio.run(bridge_app.process_scheduled_message({
                    "type": "system.dream", "jobId": "dream", "revision": "revision",
                }))
        call = adapter.complete_system_schedule.await_args.kwargs
        self.assertFalse(call["success"])
        self.assertEqual(call["summary"], {"outcome": "unknown"})

    def test_native_preclaim_failure_can_retry_only_while_occurrence_is_unchanged(self):
        job = {
            "id": "job", "next_run_at": "2026-01-01T00:00:00+00:00",
            "enabled": True, "state": "scheduled", "last_run_at": None,
        }
        provider = SimpleNamespace(
            fire_due=MagicMock(side_effect=ConnectionError("failed before native claim")),
            rearm=MagicMock(return_value={"messageId": "repair", "occurrenceId": "revision"}),
        )
        modules = {
            "cron.jobs": SimpleNamespace(get_job=lambda _: job),
            "cron.scheduler_provider": SimpleNamespace(resolve_cron_scheduler=lambda: provider),
            "azure_cron_provider": SimpleNamespace(schedule_revision=lambda _: "revision"),
        }
        with tempfile.TemporaryDirectory(dir=RUNTIME_DIR.parent.parent) as directory:
            profile = Path(directory)
            cron_runtime.atomic_write_json(cron_runtime.cron_delivery_path(profile), {"job": {"local": True}})
            with patch.dict(sys.modules, modules):
                with self.assertRaises(ConnectionError):
                    cron_runtime.fire_cron_job(profile, job_id="job", revision="revision")
                receipt = cron_runtime.read_json_object(cron_runtime.cron_delivery_receipts_path(profile))["job:revision"]
                self.assertEqual(receipt["state"], "preclaim_failed")
                cron_runtime.rearm_preclaim_schedule(
                    profile, job_id="job", revision="revision", repair_id="ticket", operator="owner", reason="preclaim",
                )
                with self.assertRaises(ConnectionError):
                    cron_runtime.fire_cron_job(profile, job_id="job", revision="revision")
                self.assertEqual(provider.fire_due.call_count, 2)
                job["fire_claim"] = {"at": "2020-01-01T00:00:00+00:00"}
                outcome = cron_runtime.fire_cron_job(profile, job_id="job", revision="revision")
                self.assertEqual(outcome["status"], "unknown")
                self.assertEqual(provider.fire_due.call_count, 2)

    def test_dream_preclaim_failure_retries_native_claim_not_inference(self):
        job = {
            "id": "dream", "next_run_at": "2026-01-01T00:00:00+00:00",
            "enabled": True, "state": "scheduled", "last_run_at": None,
        }
        claim = MagicMock(side_effect=[ConnectionError("before claim"), True])
        modules = {
            "cron.jobs": SimpleNamespace(get_job=lambda _: job, claim_job_for_fire=claim),
            "azure_cron_provider": SimpleNamespace(schedule_revision=lambda _: "revision"),
        }
        with tempfile.TemporaryDirectory(dir=RUNTIME_DIR.parent.parent) as directory:
            profile = Path(directory)
            cron_runtime.atomic_write_json(
                cron_runtime.cron_delivery_path(profile), {"dream": {"systemType": "dream"}},
            )
            with patch.dict(sys.modules, modules):
                with self.assertRaises(ConnectionError):
                    cron_runtime.claim_system_schedule(
                        profile, job_id="dream", revision="revision", occurrence_id="revision",
                    )
                result = cron_runtime.claim_system_schedule(
                    profile, job_id="dream", revision="revision", occurrence_id="revision",
                )
            self.assertEqual(claim.call_count, 2)
            self.assertEqual(result["status"], "claimed")
            self.assertEqual(result["phase"], "pending")

    def test_provider_binds_trusted_native_origin_and_refuses_changed_destination(self):
        with tempfile.TemporaryDirectory(dir=RUNTIME_DIR.parent.parent) as directory:
            profile = Path(directory)
            job = {
                "id": "job", "next_run_at": "2026-11-12T14:25:06.723496+00:00",
                "enabled": True, "state": "scheduled",
                "schedule": {
                    "kind": "once", "run_at": "2026-11-12T14:25:06.723496+00:00",
                    "display": "once in 2m",
                },
                "schedule_display": "once in 2m", "repeat": {"times": 1, "completed": 0},
                "created_at": "2026-11-12T14:23:06.723631+00:00",
                "last_run_at": None, "last_status": None, "script": None, "no_agent": False,
                "deliver": "local",
                "origin": {
                    "platform": "autopilots", "chat_id": "trusted-reference",
                    "chat_name": None, "thread_id": None, "user_id": None, "scope_id": None,
                },
            }
            modules = {
                "cron": types.ModuleType("cron"),
                "cron.jobs": SimpleNamespace(
                    CRON_DIR=profile / "cron", load_jobs=lambda: [job], get_job=lambda _: job,
                ),
                "cron.scheduler_provider": SimpleNamespace(CronScheduler=object),
            }
            spec = importlib.util.spec_from_file_location(
                "azure_provider_origin_test", RUNTIME_DIR / "azure_cron_provider.py",
            )
            module = importlib.util.module_from_spec(spec)
            with patch.dict(sys.modules, modules):
                spec.loader.exec_module(module)
                provider = module.AzureCronScheduler()
                cron_runtime.upsert_delivery_reference(
                    profile, reference_key="trusted-reference", conversation={}, boundary="one_to_one",
                )
                with patch.object(provider, "_schedule", return_value=42) as schedule:
                    provider.register_job(job)
                self.assertEqual(
                    cron_runtime.read_json_object(cron_runtime.cron_delivery_path(profile))["job"],
                    {"referenceKey": "trusted-reference"},
                )
                schedule.assert_called_once()
                schedule.assert_called_once_with(
                    job, module.schedule_revision(job), {"referenceKey": "trusted-reference"},
                )
                self.assertEqual(provider._load_state()["job"], {
                    "revision": module.schedule_revision(job),
                    "fireAt": job["next_run_at"], "sequenceNumber": 42,
                })
                with patch.object(provider, "reconcile"):
                    provider._write_state({})
                    with self.assertRaisesRegex(RuntimeError, "was not armed"):
                        provider.register_job(job)
                cron_runtime.atomic_write_json(
                    cron_runtime.cron_delivery_path(profile), {"job": {"referenceKey": "different-reference"}},
                )
                with self.assertRaisesRegex(ValueError, "different reference"):
                    provider.register_job(job)
                cron_runtime.atomic_write_json(
                    cron_runtime.cron_delivery_path(profile), {},
                )
                with patch.object(provider, "_schedule", side_effect=ConnectionError("broker unavailable")):
                    with self.assertRaisesRegex(ConnectionError, "broker unavailable"):
                        provider.register_job(job)
                cron_runtime.atomic_write_json(
                    cron_runtime.delivery_references_path(profile), {},
                )
                with self.assertRaisesRegex(ValueError, "trusted proactive delivery reference"):
                    provider.register_job(job)
                cron_runtime.upsert_delivery_reference(
                    profile, reference_key="trusted-reference", conversation={}, boundary="one_to_one",
                )
                cron_runtime.atomic_write_json(
                    cron_runtime.cron_delivery_path(profile), {"job": {"local": True}},
                )
                with patch.object(provider, "_schedule", return_value=43):
                    provider.register_job(job)
                self.assertEqual(
                    cron_runtime.read_json_object(cron_runtime.cron_delivery_path(profile))["job"],
                    {"local": True},
                )

    def test_provider_repair_bypasses_broker_dedup_without_changing_native_occurrence(self):
        sent = []

        class Sender:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def send_messages(self, message):
                sent.append(message)

        modules = {"cron.scheduler_provider": SimpleNamespace(CronScheduler=object)}
        spec = importlib.util.spec_from_file_location(
            "azure_provider_repair_test", RUNTIME_DIR / "azure_cron_provider.py",
        )
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, modules):
            spec.loader.exec_module(module)
        provider = module.AzureCronScheduler()
        client = SimpleNamespace(get_queue_sender=lambda _: Sender())
        job = {"id": "job", "next_run_at": "2026-01-01T00:00:00+00:00"}
        with (
            patch.dict(os.environ, {"WORKER_ID": "worker", "SCHEDULER_SERVICEBUS_QUEUE": "schedule"}),
            patch.object(provider, "_servicebus_client", return_value=client),
        ):
            first = provider.rearm(job, "native-revision", {"local": True}, repair_id="ticket-42")
            second = provider.rearm(job, "native-revision", {"local": True}, repair_id="ticket-42")
        self.assertEqual(first, second)
        self.assertNotEqual(first["messageId"], "worker:job:native-revision")
        self.assertEqual(sent[0].message_id, sent[1].message_id)
        payload = json.loads(b"".join(bytes(part) for part in sent[0].body))
        self.assertEqual(payload["revision"], "native-revision")
        self.assertEqual(payload["occurrenceId"], "native-revision")

    def test_preclaim_rearm_preserves_occurrence_identity_and_is_idempotent(self):
        job = {
            "id": "job", "next_run_at": "2026-01-01T00:00:00+00:00",
            "enabled": True, "state": "scheduled",
        }
        provider = SimpleNamespace(rearm=MagicMock(return_value={
            "messageId": "worker:repair:stable", "occurrenceId": "revision",
        }))
        modules = {
            "cron.jobs": SimpleNamespace(get_job=lambda _: job),
            "cron.scheduler_provider": SimpleNamespace(resolve_cron_scheduler=lambda: provider),
            "azure_cron_provider": SimpleNamespace(schedule_revision=lambda _: "revision"),
        }
        with tempfile.TemporaryDirectory(dir=RUNTIME_DIR.parent.parent) as directory:
            profile = Path(directory)
            cron_runtime.atomic_write_json(cron_runtime.cron_delivery_path(profile), {"job": {"local": True}})
            kwargs = dict(job_id="job", revision="revision", repair_id="ticket-42", operator="owner", reason="preclaim failure")
            with patch.dict(sys.modules, modules):
                first = cron_runtime.rearm_preclaim_schedule(profile, **kwargs)
                second = cron_runtime.rearm_preclaim_schedule(profile, **kwargs)
                self.assertEqual(first["status"], "rearmed")
                self.assertEqual(second["status"], "duplicate")
                self.assertEqual(first["occurrenceId"], "revision")
                provider.rearm.assert_called_once_with(job, "revision", {"local": True}, repair_id="ticket-42")
                job["fire_claim"] = {"at": "native admission after successful repair"}
                self.assertEqual(cron_runtime.rearm_preclaim_schedule(profile, **kwargs)["status"], "duplicate")
                job.pop("fire_claim")
                cron_runtime.atomic_write_json(
                    cron_runtime.cron_delivery_receipts_path(profile),
                    {"job:revision": {"state": "unknown", "startedAtEpoch": 1}},
                )
                with self.assertRaisesRegex(ValueError, "already admitted"):
                    cron_runtime.rearm_preclaim_schedule(profile, **{**kwargs, "repair_id": "ticket-43"})

    def test_native_claim_age_is_never_permission_to_rearm(self):
        job = {
            "id": "job", "next_run_at": "2026-01-01T00:00:00+00:00", "enabled": True,
            "state": "scheduled", "fire_claim": {"at": "2020-01-01T00:00:00+00:00"},
        }
        modules = {
            "cron.jobs": SimpleNamespace(get_job=lambda _: job),
            "cron.scheduler_provider": SimpleNamespace(resolve_cron_scheduler=lambda: None),
            "azure_cron_provider": SimpleNamespace(schedule_revision=lambda _: "revision"),
        }
        with patch.dict(sys.modules, modules):
            with self.assertRaisesRegex(ValueError, "no native fire claim"):
                cron_runtime.rearm_preclaim_schedule(
                    Path.cwd(), job_id="job", revision="revision",
                    repair_id="ticket", operator="owner", reason="old lock",
                )

    def test_failed_dream_receipt_remains_unknown_until_explicit_skip(self):
        with tempfile.TemporaryDirectory(dir=RUNTIME_DIR.parent.parent) as directory:
            profile = Path(directory)
            path = cron_runtime.system_schedule_receipts_path(profile)
            receipt = {
                "state": "completed", "revision": "revision", "success": False,
                "summary": {"outcome": "unknown"}, "error": "native outcome unknown",
            }
            cron_runtime.atomic_write_json(path, {"dream:revision": receipt})
            self.assertEqual(cron_runtime._terminal_system_outcome(receipt, "dream")["status"], "unknown")
            resolved = cron_runtime.resolve_schedule(
                profile, job_id="dream", revision="revision", occurrence_id="revision",
                operator="owner", reason="inspected run and explicitly skipped", kind="dream",
            )
            self.assertEqual(resolved["status"], "skipped")
            self.assertFalse(resolved["replayed"])
            updated = cron_runtime.read_json_object(path)["dream:revision"]
            self.assertEqual(cron_runtime._terminal_system_outcome(updated, "dream")["status"], "duplicate")

    def test_dream_pending_native_run_is_not_completed_as_a_failed_occurrence(self):
        adapter = SimpleNamespace(
            claim_system_schedule=AsyncMock(return_value={
                "status": "claimed", "ownerToken": "owner", "jobId": "dream",
                "revision": "revision", "occurrenceId": "revision",
            }),
            complete_system_schedule=AsyncMock(),
        )
        with (
            patch.object(bridge_app, "runtime_adapter", return_value=adapter),
            patch.object(bridge_app.scheduled_learning, "run_once", side_effect=bridge_app.event_worker.InvocationPendingError("native run continues")),
        ):
            with self.assertRaises(bridge_app.event_worker.InvocationPendingError):
                asyncio.run(bridge_app.process_scheduled_message({
                    "type": "system.dream", "jobId": "dream", "revision": "revision",
                }))
        adapter.complete_system_schedule.assert_not_awaited()

    def test_cron_provider_is_a_separate_exclusive_plugin_and_autopilots_is_standalone(self):
        plugins = RUNTIME_DIR / "plugins"
        self.assertEqual(sorted(path.name for path in plugins.iterdir() if path.is_dir()), ["autopilots", "azure"])
        provider_source = (plugins / "azure" / "__init__.py").read_text(encoding="utf-8")
        general_source = (plugins / "autopilots" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("register_cron_scheduler", provider_source)
        for marker in ("register_cron_scheduler", "CronScheduler", "MemoryProvider"):
            self.assertNotIn(marker, general_source)
        self.assertIn("kind: standalone", (plugins / "autopilots" / "plugin.yaml").read_text(encoding="utf-8"))

        calls = []

        class Context:
            def register_platform_handler(self, platform, factory):
                calls.append(("handler", platform))

        modules = {
            "runtime_keepalive": types.SimpleNamespace(register=lambda ctx: calls.append(("keepalive", None))),
            "bridge.hermes_telemetry": types.SimpleNamespace(register=lambda ctx: calls.append(("telemetry", None))),
            "runtime_routes": types.SimpleNamespace(register_routes=lambda app, home: None),
            "native_learning": types.SimpleNamespace(
                register_attachment_privacy=lambda ctx: calls.append(("privacy", None)),
            ),
            "cron_origin": types.SimpleNamespace(register=lambda ctx: calls.append(("origin", None))),
        }
        spec = importlib.util.spec_from_file_location("autopilots_plugin_under_test", plugins / "autopilots" / "__init__.py")
        module = importlib.util.module_from_spec(spec)
        import bridge

        with (
            patch.dict(sys.modules, modules),
            patch.object(bridge, "hermes_telemetry", modules["bridge.hermes_telemetry"], create=True),
            patch.dict(os.environ, {"HERMES_HOME": "/data/hermes", "AUTOPILOTS_PLUGIN_PATH": ""}),
        ):
            spec.loader.exec_module(module)
            module.register(Context())
        self.assertEqual(calls, [("telemetry", None), ("keepalive", None), ("origin", None), ("privacy", None), ("handler", "api_server")])
        dockerfile = (RUNTIME_DIR / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("ENV AUTOPILOTS_PLUGIN_PATH=/app:/opt/autopilots/hermes-site", dockerfile)

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
            sender.schedule_document_retry(
                operation_id="a" * 24,
                attempt=2000,
                due_at_unix=time.time() + 60,
                delivery_activity_id="activity-1",
                delivery_attempt_id="attempt-1",
            )

        body = json.loads(
            b"".join(
                bytes(part) for part in scheduled[0][0].body
            )
        )
        ack_body = json.loads(
            b"".join(
                bytes(part) for part in scheduled[1][0].body
            )
        )
        self.assertNotIn("deliveryActivityId", body)
        self.assertEqual(ack_body["deliveryActivityId"], "activity-1")
        self.assertEqual(ack_body["deliveryAttemptId"], "attempt-1")
        self.assertEqual(
            scheduled[1][0].message_id,
            f"hermes2:document:{'a' * 24}:2000",
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

    def test_scheduled_document_source_change_rearms_retry(self):
        class Adapter:
            runtime_kind = "hermes"

            async def process_document_background(self, operation_id):
                return {
                    "status": "source_changed",
                    "operationId": operation_id,
                    "attempt": 3,
                    "nextAttemptUnix": time.time() + 60,
                }

        with (
            patch.object(
                bridge_app,
                "runtime_adapter",
                return_value=Adapter(),
            ),
            patch.object(
                bridge_app.schedule_sender,
                "schedule_document_retry",
                return_value={"sequenceNumber": 43},
            ) as schedule,
        ):
            result = asyncio.run(
                bridge_app.process_scheduled_message(
                    {
                        "type": "document.publish.retry",
                        "operationId": "a" * 24,
                        "attempt": 2,
                    }
                )
            )

        self.assertEqual(result["status"], "scheduled")
        self.assertEqual(schedule.call_args.kwargs["attempt"], 3)

    def test_unacknowledged_document_delivery_schedules_ack_without_resend(self):
        sent = []

        class Adapter:
            async def claim_document_delivery(self, operation_id):
                return {"status": "ready", "deliveryAttemptId": "attempt-1"}

            async def acknowledge_document_delivery(self, **kwargs):
                raise RuntimeError("worker unavailable")

            async def fail_document_delivery(self, **kwargs):
                raise AssertionError("A sent activity must not be failed.")

        async def send_activity(activity):
            sent.append(activity)
            return {"id": "activity-1"}

        with (
            patch.object(bridge_app.asyncio, "sleep", return_value=None),
            patch.object(
                bridge_app.schedule_sender,
                "schedule_document_retry",
                return_value={"sequenceNumber": 44},
            ) as schedule,
        ):
            result = asyncio.run(
                bridge_app.deliver_or_schedule_reconciliation(
                    adapter=Adapter(),
                    operation_id="a" * 24,
                    result={
                        "status": "completed",
                        "driveItem": {"webUrl": "https://contoso/doc"},
                    },
                    send_activity=send_activity,
                )
            )

        self.assertEqual(len(sent), 1)
        self.assertEqual(
            result,
            {
                "status": "delivered",
                "activityId": "activity-1",
                "acknowledged": False,
            },
        )
        kwargs = schedule.call_args.kwargs
        self.assertEqual(kwargs["attempt"], bridge_app.DOCUMENT_ACK_FIRST_ATTEMPT)
        self.assertEqual(kwargs["delivery_activity_id"], "activity-1")
        self.assertEqual(kwargs["delivery_attempt_id"], "attempt-1")

    def test_scheduled_ack_reconciliation_never_resends_teams_result(self):
        class Adapter:
            runtime_kind = "hermes"

            def __init__(self, failures):
                self.failures = failures
                self.acknowledged = []

            async def process_document_background(self, operation_id):
                raise AssertionError("Ack reconciliation must not reprocess.")

            async def acknowledge_document_delivery(self, **kwargs):
                if self.failures:
                    self.failures -= 1
                    raise RuntimeError("worker unavailable")
                self.acknowledged.append(kwargs)
                return {"status": "completed"}

        def payload(attempt):
            return {
                "type": "document.publish.retry",
                "operationId": "a" * 24,
                "attempt": attempt,
                "deliveryActivityId": "activity-1",
                "deliveryAttemptId": "attempt-1",
            }

        recovering = Adapter(failures=0)
        failing = Adapter(failures=2)
        with (
            patch.object(
                bridge_app,
                "send_proactive_activity",
                side_effect=AssertionError("Teams result was resent."),
            ),
            patch.object(
                bridge_app.schedule_sender,
                "schedule_document_retry",
                return_value={"sequenceNumber": 45},
            ) as schedule,
        ):
            with patch.object(
                bridge_app, "runtime_adapter", return_value=recovering
            ):
                completed = asyncio.run(
                    bridge_app.process_scheduled_message(payload(2000))
                )
            with patch.object(
                bridge_app, "runtime_adapter", return_value=failing
            ):
                rescheduled = asyncio.run(
                    bridge_app.process_scheduled_message(payload(2000))
                )
                with self.assertRaises(RuntimeError):
                    asyncio.run(
                        bridge_app.process_scheduled_message(
                            payload(bridge_app.DOCUMENT_ACK_LAST_ATTEMPT)
                        )
                    )

        self.assertEqual(completed["status"], "completed")
        self.assertEqual(
            recovering.acknowledged[0]["delivery_attempt_id"],
            "attempt-1",
        )
        self.assertEqual(rescheduled["status"], "ack_scheduled")
        schedule.assert_called_once()
        self.assertEqual(schedule.call_args.kwargs["attempt"], 2001)

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
                    output_callback(output_path, "native-execution")
                    return True

                def forget_fired_occurrence(self, *args):
                    pass

                def reconcile(self):
                    pass

            cron_modules["cron.scheduler_provider"].resolve_cron_scheduler = Provider
            cron_modules["azure_cron_provider"].schedule_revision = lambda value: revision
            cron_modules["cron.executions"] = SimpleNamespace(get_execution=lambda native_id: {
                "id": native_id, "job_id": "job-1", "status": "completed",
                "scheduled_instant": job["next_run_at"],
            })
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
                "scheduledNextRunAt": job["next_run_at"],
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
                execution_id="execution-1", output_path=output_path, native_execution_id="native-execution",
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
                forget_fired_occurrence=lambda *args: None,
                fire_due=lambda job_id: (_ for _ in ()).throw(
                    AssertionError("must not rerun")
                ),
            )
            cron_modules["azure_cron_provider"].schedule_revision = lambda value: revision
            cron_modules["cron.executions"] = SimpleNamespace(get_execution=lambda native_id: {
                "id": native_id, "job_id": "job-1", "status": "completed",
                "scheduled_instant": job["next_run_at"],
            })

            with patch.dict(sys.modules, cron_modules):
                recovered = cron_runtime.fire_cron_job(
                    profile,
                    job_id="job-1",
                    revision=revision,
                )

        self.assertEqual(recovered["status"], "pending_delivery")
        self.assertEqual(recovered["output"], "Recovered output")

    def test_finished_execution_without_identity_or_output_is_unknown_not_a_fake_result(self):
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

        self.assertEqual(result["status"], "unknown")
        self.assertIn("without an authenticated execution/output binding", result["reason"])

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

    def test_delivered_receipts_retain_old_dlq_replay_tombstones(self):
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
        self.assertEqual(len(delivered), 25)
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
                forget_fired_occurrence=lambda *args: None,
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
                forget_fired_occurrence=lambda *args: None,
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
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["lastStatus"], "error")
        self.assertNotIn("Not this occurrence", result.get("output", ""))
        self.assertIn("no unambiguous output", result["reason"])

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
                "cron.executions": SimpleNamespace(get_execution=lambda native_id: {
                    "id": native_id, "job_id": "job-1", "status": "completed",
                    "scheduled_instant": "2026-09-01T00:00:00+00:00",
                }),
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
                    patch.object(provider, "forget_fired_occurrence"),
                ):
                    revision = module.schedule_revision(job)
                    first = cron_runtime.fire_cron_job(profile, job_id="job-1", revision=revision)
                    self.assertEqual(first["status"], "pending_delivery")
                    self.assertFalse(first["reconciled"])
                    self.assertIn("broker unavailable", first["reconciliationError"])
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
        phases = ["pending", "dream_started", "dream_completed", "status_completed"]
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
            with self.assertRaisesRegex(RuntimeError, "status checkpoint"):
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
