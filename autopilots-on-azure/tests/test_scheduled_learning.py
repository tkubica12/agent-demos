import asyncio
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import mock_open, patch
from contextlib import redirect_stdout

import bridge.app as bridge_app
from bridge.runtime.base import AgentResponse, DreamResponse
from bridge.runtime.hermes import NativeRunFailedError
from bridge.scheduled_learning import (
    ScheduledLearningCoordinator,
    ScheduledLearningSettings,
)
from scripts.servicebus_dream_smoke import MANAGEMENT_DREAM

RUNTIME_DIR = Path(__file__).resolve().parents[1] / "runtimes" / "hermes"
sys.path.insert(0, str(RUNTIME_DIR))
import cron_runtime


class ScheduledLearningTests(unittest.TestCase):
    def test_management_dream_uses_native_account_before_accessing_cron_state(self):
        calls = []

        def diagnostics(home):
            calls.append(("diagnostics", str(home)))
            return {"jobs": [{"id": "dream", "name": "Platform Dreaming", "schedule": "0 2 * * *"}]}

        environment = {
            "HERMES_HOME": "/data/hermes",
            "AUTOPILOTS_APPLICATION_ROOT": str(RUNTIME_DIR),
        }
        account = SimpleNamespace(pw_uid=1019, pw_gid=1019)
        with (
            patch.dict(os.environ, {}),
            patch.dict(sys.modules, {
                "pwd": SimpleNamespace(getpwnam=lambda name: account),
                "cron_runtime": SimpleNamespace(
                    cron_diagnostics=diagnostics, enqueue_system_dream_now=lambda home: None,
                ),
            }),
            patch.object(sys, "path", list(sys.path)),
            patch.object(sys, "argv", ["management", "schedule"]),
            patch("builtins.open", mock_open(read_data=json.dumps({"environment": environment}))),
            patch.object(os, "geteuid", return_value=0, create=True),
            patch.object(os, "setgroups", side_effect=lambda value: calls.append(("groups", value)), create=True),
            patch.object(os, "setgid", side_effect=lambda value: calls.append(("gid", value)), create=True),
            patch.object(os, "setuid", side_effect=lambda value: calls.append(("uid", value)), create=True),
            redirect_stdout(io.StringIO()),
        ):
            exec(compile(MANAGEMENT_DREAM, "<management-dream>", "exec"), {})
        self.assertEqual(calls, [
            ("groups", []), ("gid", 1019), ("uid", 1019), ("diagnostics", str(Path("/data/hermes"))),
        ])

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
                raise NativeRunFailedError("native Dream run failed")

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
        self.assertIn("NativeRunFailedError", calls[0]["error"])

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
    def test_dream_does_not_prepare_packet_even_for_owner_shared_changes(self):
        class Adapter:
            runtime_kind = "hermes"

            async def dream(self, request):
                self.request = request
                return DreamResponse(
                    agent=AgentResponse(text="done", raw={}),
                    learning_digest={
                        "changes": [{"entryId": "e1", "new": True}],
                        "pending": [],
                        "governed": [{"artifactPath": "skills/candidates/example",
                                      "choice": {"choice": "share", "current": True}}],
                        "worker": {"workerId": "worker-1", "roleRelease": "3.2.0"},
                    },
                )

            async def prepare_collective_learning(self):
                raise AssertionError("Dreaming must not prepare a Learning Packet.")

        adapter = Adapter()
        coordinator = ScheduledLearningCoordinator(
            adapter_factory=lambda: adapter,
            settings=ScheduledLearningSettings(
                focus="recent work",
                max_records=2,
                retry_limit=0,
                retry_backoff_seconds=1,
            ),
            worker_id="worker-1",
        )

        result = asyncio.run(coordinator.run_once())

        self.assertEqual(adapter.request.focus, "recent work")
        self.assertEqual(result["dream"]["sharedChangeCount"], 1)
        self.assertEqual(result["dream"]["skillChangeCount"], 1)
        self.assertNotIn("packet", result)
        self.assertEqual(coordinator.status()["successCount"], 1)

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
                    learning_digest={"changes": [], "pending": [], "governed": []},
                )

        async def sleep(delay):
            delays.append(delay)

        coordinator = ScheduledLearningCoordinator(
            adapter_factory=Adapter,
            settings=ScheduledLearningSettings(
                focus="recent work",
                max_records=2,
                retry_limit=1,
                retry_backoff_seconds=7,
            ),
            worker_id="worker-1",
            sleep=sleep,
        )

        with self.assertRaisesRegex(RuntimeError, "unknown outcome"):
            asyncio.run(coordinator.run_once())

        self.assertEqual(attempts, 1)
        self.assertEqual(delays, [7])
        self.assertEqual(len(set(sessions)), 1)

    def test_durable_phase_survives_status_failure_and_coordinator_restart(self):
        calls = {"model": 0, "status": 0}
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
                        learning_digest={"governed": [{"choice": {"choice": "share", "current": True}}]},
                    )

                async def prepare_collective_learning(self):
                    raise AssertionError("Scheduled Dreaming must not prepare Learning Packets.")

            def coordinator():
                return ScheduledLearningCoordinator(
                    adapter_factory=Adapter, worker_id="worker",
                    settings=ScheduledLearningSettings("review", 2, 0, 1),
                )

            with self.assertRaisesRegex(RuntimeError, "status unavailable"):
                asyncio.run(coordinator().run_once(operation=operation))
            result = asyncio.run(coordinator().run_once(operation=operation))
            replay = asyncio.run(coordinator().run_once(operation=operation))
        self.assertEqual(calls, {"model": 1, "status": 2})
        self.assertNotIn("packet", result)
        self.assertNotIn("packet", replay)
        self.assertEqual(result["dream"]["sessionId"], operation["sessionId"])


if __name__ == "__main__":
    unittest.main()
