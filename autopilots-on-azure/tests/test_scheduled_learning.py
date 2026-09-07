import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bridge.app as bridge_app
from bridge.runtime.base import AgentResponse, DreamResponse
from bridge.scheduled_learning import (
    ScheduledLearningCoordinator,
    ScheduledLearningSettings,
)

RUNTIME_DIR = Path(__file__).resolve().parents[1] / "runtimes" / "hermes"
sys.path.insert(0, str(RUNTIME_DIR))
import cron_runtime


class ScheduledLearningTests(unittest.TestCase):
    def test_system_dream_message_claims_runs_and_completes_schedule(self):
        calls = []
        runs = []

        class Adapter:
            runtime_kind = "hermes"

            async def claim_system_schedule(self, **kwargs):
                calls.append(("claim", kwargs))
                return {"status": "claimed", "ownerToken": "owner-1", "sessionId": "stable-session"}

            async def complete_system_schedule(self, **kwargs):
                calls.append(("complete", kwargs))
                return {"status": "completed", "nextRunAt": "tomorrow"}

        class Coordinator:
            async def run_once(self, **kwargs):
                runs.append(kwargs)
                return {
                    "dream": {"recordCount": 1},
                    "packet": {"approvalRequired": True},
                }

        with (
            patch.object(bridge_app, "runtime_adapter", return_value=Adapter()),
            patch.object(bridge_app, "scheduled_learning", Coordinator()),
        ):
            result = asyncio.run(bridge_app.process_scheduled_message({
                "type": "system.dream",
                "jobId": "dream-job",
                "revision": "dream-revision",
                "occurrenceId": "dream-occurrence",
            }))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(calls[0][0], "claim")
        self.assertEqual(calls[1][0], "complete")
        self.assertTrue(calls[1][1]["success"])
        self.assertEqual(calls[1][1]["owner_token"], "owner-1")
        self.assertEqual(runs[0]["operation"]["sessionId"], "stable-session")

    def test_system_dream_records_nonretryable_failure_and_rearms(self):
        calls = []

        class Adapter:
            runtime_kind = "hermes"

            async def claim_system_schedule(self, **kwargs):
                return {"status": "claimed"}

            async def complete_system_schedule(self, **kwargs):
                calls.append(kwargs)
                return {"status": "completed", "nextRunAt": "tomorrow"}

        class Coordinator:
            async def run_once(self, **kwargs):
                raise ValueError("invalid Dreaming result")

        with (
            patch.object(bridge_app, "runtime_adapter", return_value=Adapter()),
            patch.object(bridge_app, "scheduled_learning", Coordinator()),
        ):
            result = asyncio.run(bridge_app.process_scheduled_message({
                "type": "system.dream",
                "jobId": "dream-job",
                "revision": "dream-revision",
                "occurrenceId": "dream-occurrence",
            }))

        self.assertEqual(result["status"], "failed")
        self.assertFalse(calls[0]["success"])
        self.assertIn("ValueError", calls[0]["error"])

    def test_interrupted_system_dream_is_not_rerun(self):
        calls = []

        class Adapter:
            runtime_kind = "hermes"

            async def claim_system_schedule(self, **kwargs):
                return {"status": "interrupted"}

            async def complete_system_schedule(self, **kwargs):
                calls.append(kwargs)
                return {"status": "completed", "nextRunAt": "tomorrow"}

        class Coordinator:
            async def run_once(self, **kwargs):
                raise AssertionError("interrupted Dreaming must not rerun")

        with (
            patch.object(bridge_app, "runtime_adapter", return_value=Adapter()),
            patch.object(bridge_app, "scheduled_learning", Coordinator()),
        ):
            result = asyncio.run(bridge_app.process_scheduled_message({
                "type": "system.dream",
                "jobId": "dream-job",
                "revision": "dream-revision",
                "occurrenceId": "manual-1",
            }))

        self.assertEqual(result["status"], "uncertain")
        self.assertFalse(calls[0]["success"])
        self.assertIn("interrupted", calls[0]["error"].lower())
        self.assertIn("will not be rerun automatically", result["reason"])
    def test_dream_prepares_packet_when_records_exist(self):
        class Adapter:
            runtime_kind = "hermes"

            async def dream(self, request):
                self.request = request
                return DreamResponse(
                    agent=AgentResponse(text="done", raw={}),
                    learning_status={
                        "records": [{"recordId": "lr-1"}],
                        "rejectedRecords": [],
                        "roleRelease": {"release": "3.2.0"},
                    },
                )

            async def prepare_collective_learning(self):
                return {
                    "packetDigest": "a" * 64,
                    "improvements": [{"artifactPath": "skills/candidates/example"}],
                    "roleRelease": {"release": "3.2.0"},
                    "approvalRequired": True,
                }

        adapter = Adapter()
        coordinator = ScheduledLearningCoordinator(
            adapter_factory=lambda: adapter,
            settings=ScheduledLearningSettings(
                enabled=True,
                initial_delay_seconds=0,
                interval_seconds=300,
                focus="recent work",
                max_records=2,
                retry_limit=0,
                retry_backoff_seconds=1,
                prepare_packet=True,
            ),
            worker_id="worker-1",
        )

        result = asyncio.run(coordinator.run_once())

        self.assertEqual(adapter.request.focus, "recent work")
        self.assertEqual(result["dream"]["recordCount"], 1)
        self.assertEqual(result["packet"]["packetDigest"], "a" * 64)
        self.assertTrue(result["packet"]["approvalRequired"])
        self.assertEqual(coordinator.status()["successCount"], 1)

    def test_no_packet_is_prepared_without_records(self):
        class Adapter:
            runtime_kind = "hermes"

            async def dream(self, request):
                return DreamResponse(
                    agent=AgentResponse(text="done", raw={}),
                    learning_status={
                        "records": [],
                        "rejectedRecords": [],
                        "roleRelease": {"release": "3.2.0"},
                    },
                )

            async def prepare_collective_learning(self):
                raise AssertionError("An empty Dream must not prepare a packet.")

        coordinator = ScheduledLearningCoordinator(
            adapter_factory=Adapter,
            settings=ScheduledLearningSettings(
                enabled=False,
                initial_delay_seconds=0,
                interval_seconds=300,
                focus="recent work",
                max_records=2,
                retry_limit=0,
                retry_backoff_seconds=1,
                prepare_packet=True,
            ),
            worker_id="worker-1",
        )

        result = asyncio.run(coordinator.run_once())

        self.assertIsNone(result["packet"])
        self.assertEqual(coordinator.status()["lastDream"]["recordCount"], 0)

    def test_uncheckpointed_failure_does_not_blindly_repeat_inference(self):
        attempts = 0
        delays = []
        sessions = []

        class Adapter:
            runtime_kind = "hermes"

            async def dream(self, request):
                nonlocal attempts
                attempts += 1
                sessions.append(request.session_id)
                if attempts == 1:
                    raise RuntimeError("temporary")
                return DreamResponse(
                    agent=AgentResponse(text="done", raw={}),
                    learning_status={
                        "records": [],
                        "rejectedRecords": [],
                        "roleRelease": {"release": "3.2.0"},
                    },
                )

        async def sleep(delay):
            delays.append(delay)

        coordinator = ScheduledLearningCoordinator(
            adapter_factory=Adapter,
            settings=ScheduledLearningSettings(
                enabled=True,
                initial_delay_seconds=0,
                interval_seconds=300,
                focus="recent work",
                max_records=2,
                retry_limit=1,
                retry_backoff_seconds=7,
                prepare_packet=True,
            ),
            worker_id="worker-1",
            sleep=sleep,
        )

        with self.assertRaisesRegex(RuntimeError, "unknown outcome"):
            asyncio.run(coordinator.run_once())

        self.assertEqual(attempts, 1)
        self.assertEqual(delays, [7])
        self.assertEqual(len(set(sessions)), 1)

    def test_prepare_retry_does_not_repeat_successful_dream(self):
        calls = {"dream": 0, "prepare": 0}

        class Adapter:
            runtime_kind = "hermes"

            async def dream(self, request):
                calls["dream"] += 1
                return DreamResponse(
                    agent=AgentResponse(text="done", raw={}),
                    learning_status={"records": [{"recordId": "one"}]},
                )

            async def prepare_collective_learning(self):
                calls["prepare"] += 1
                if calls["prepare"] == 1:
                    raise RuntimeError("prepare unavailable")
                return {"packetDigest": "digest", "approvalRequired": True}

        coordinator = ScheduledLearningCoordinator(
            adapter_factory=Adapter, worker_id="worker",
            settings=ScheduledLearningSettings(True, 0, 300, "review", 2, 1, 1, True),
            sleep=lambda _: asyncio.sleep(0),
        )
        result = asyncio.run(coordinator.run_once())
        self.assertEqual(calls, {"dream": 1, "prepare": 2})
        self.assertEqual(result["packet"]["packetDigest"], "digest")

    def test_durable_phase_survives_status_failure_and_coordinator_restart(self):
        calls = {"model": 0, "status": 0, "prepare": 0}
        with tempfile.TemporaryDirectory(dir=RUNTIME_DIR.parent.parent) as directory:
            profile = Path(directory)
            receipt = cron_runtime._new_system_operation(
                {"id": "dream-job"}, "revision", "occurrence", "adhoc"
            )
            receipt["state"] = "running"
            cron_runtime.atomic_write_json(cron_runtime.system_schedule_receipts_path(profile), {
                "dream-job:occurrence": receipt
            })
            operation = cron_runtime._system_operation(receipt, "dream-job")

            class Adapter:
                runtime_kind = "hermes"

                async def get_system_schedule_checkpoint(self, **kwargs):
                    return cron_runtime.get_system_schedule_checkpoint(profile, **kwargs)

                async def checkpoint_system_schedule(self, **kwargs):
                    return cron_runtime.checkpoint_system_schedule(profile, **kwargs)

                async def dream(self, request, *, operation):
                    identity = {
                        "job_id": operation["jobId"], "revision": operation["revision"],
                        "occurrence_id": operation["occurrenceId"],
                    }
                    current = await self.get_system_schedule_checkpoint(**identity)
                    if current["phase"] == "pending":
                        await self.checkpoint_system_schedule(
                            **identity, owner_token=operation["ownerToken"],
                            expected_phase="pending", phase="dream_started", payload={},
                        )
                        calls["model"] += 1
                        await self.checkpoint_system_schedule(
                            **identity, owner_token=operation["ownerToken"],
                            expected_phase="dream_started", phase="dream_completed",
                            payload={"agent": {"text": "done", "raw": {}}},
                        )
                    calls["status"] += 1
                    if calls["status"] == 1:
                        raise RuntimeError("status unavailable after model checkpoint")
                    return DreamResponse(
                        agent=AgentResponse(text="done", raw={}),
                        learning_status={"records": [{"recordId": "one"}]},
                    )

                async def prepare_collective_learning(self):
                    calls["prepare"] += 1
                    if calls["prepare"] == 1:
                        raise RuntimeError("prepare unavailable")
                    return {"packetDigest": "digest", "approvalRequired": True}

            def coordinator():
                return ScheduledLearningCoordinator(
                    adapter_factory=Adapter, worker_id="worker",
                    settings=ScheduledLearningSettings(True, 0, 300, "review", 2, 0, 1, True),
                )

            with self.assertRaisesRegex(RuntimeError, "status unavailable"):
                asyncio.run(coordinator().run_once(operation=operation))
            with self.assertRaisesRegex(RuntimeError, "prepare unavailable"):
                asyncio.run(coordinator().run_once(operation=operation))
            result = asyncio.run(coordinator().run_once(operation=operation))
            replay = asyncio.run(coordinator().run_once(operation=operation))
        self.assertEqual(calls, {"model": 1, "status": 2, "prepare": 2})
        self.assertEqual(result["packet"], replay["packet"])
        self.assertEqual(result["dream"]["sessionId"], operation["sessionId"])


if __name__ == "__main__":
    unittest.main()
