from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bridge.proactive_delivery import delivery_reference_key, send_proactive_activity
from bridge.servicebus_scheduler import ServiceBusScheduleConsumer


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
        async def run():
            previous = os.environ.get("WORKER_ID")
            os.environ["WORKER_ID"] = "hermes2"
            try:
                consumer = ServiceBusScheduleConsumer(
                    handler=lambda payload: asyncio.sleep(
                        0,
                        result={"status": "completed"},
                    )
                )
                consumer._loop = asyncio.get_running_loop()
                receiver = FakeReceiver()
                message = FakeReceivedMessage({
                    "version": "1.0",
                    "type": "hermes.cron.fire",
                    "workerId": "hermes2",
                    "jobId": "job-1",
                    "revision": "revision-1",
                })
                await asyncio.to_thread(consumer._process, receiver, message)
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

        with tempfile.TemporaryDirectory() as temp_dir:
            profile = Path(temp_dir)
            cron_modules = {
                "cron": types.ModuleType("cron"),
                "cron.jobs": types.ModuleType("cron.jobs"),
                "cron.scheduler_provider": types.ModuleType("cron.scheduler_provider"),
                "azure_cron_provider": types.ModuleType("azure_cron_provider"),
            }
            cron_modules["cron.jobs"].get_job = lambda job_id: job

            class Provider:
                def fire_due(self, job_id):
                    nonlocal fire_count
                    fire_count += 1
                    output_dir = profile / "cron" / "output" / job_id
                    output_dir.mkdir(parents=True)
                    (output_dir / "2026-07-22_17-00-00.md").write_text(
                        "# Cron Job\n\n## Prompt\n\nprivate scheduled prompt"
                        "\n\n## Response\n\nScheduled hello",
                        encoding="utf-8",
                    )
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

        with tempfile.TemporaryDirectory() as temp_dir:
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
                "outputFingerprintBefore": cron_runtime._output_fingerprint(previous),
            }
            cron_runtime.atomic_write_json(
                cron_runtime.cron_delivery_receipts_path(profile),
                {"job-1:revision-1": receipt},
            )
            (output_dir / "2026-07-22_17-03-00.md").write_text(
                "# Cron Job\n\n## Response\n\nRecovered output",
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
            lambda: SimpleNamespace(fire_due=lambda job_id: False)
        )
        cron_modules["azure_cron_provider"].schedule_revision = lambda value: "revision-1"

        with tempfile.TemporaryDirectory() as temp_dir:
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

        with tempfile.TemporaryDirectory() as temp_dir:
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
            20,
        )
        self.assertIn("job-2:executing", receipts)

    def test_system_dream_schedule_claims_completes_and_rearms(self):
        job = {
            "id": "dream-job",
            "name": "Renamed by user",
            "prompt": "Changed by user",
            "schedule": {"kind": "cron", "expr": "0 2 * * *"},
            "schedule_display": "0 2 * * *",
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

        with tempfile.TemporaryDirectory() as temp_dir:
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
                    occurrence_id="manual-1",
                )
                completed = cron_runtime.complete_system_schedule(
                    profile,
                    job_id="dream-job",
                    revision="dream-revision",
                    occurrence_id="manual-1",
                    success=True,
                    summary={"dream": {"recordCount": 1}},
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
        self.assertEqual(binding, {"systemType": "dream"})
        self.assertEqual(claimed["status"], "claimed")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(production_claim["status"], "claimed")
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

        with tempfile.TemporaryDirectory() as temp_dir:
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

        with tempfile.TemporaryDirectory() as temp_dir:
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


if __name__ == "__main__":
    unittest.main()
