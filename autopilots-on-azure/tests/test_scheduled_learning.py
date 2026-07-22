import asyncio
import unittest
from types import SimpleNamespace

from bridge.runtime.base import AgentResponse, DreamResponse
from bridge.scheduled_learning import (
    ScheduledLearningCoordinator,
    ScheduledLearningSettings,
)


class ScheduledLearningTests(unittest.TestCase):
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

    def test_retryable_failure_uses_backoff(self):
        attempts = 0
        delays = []

        class Adapter:
            runtime_kind = "hermes"

            async def dream(self, request):
                nonlocal attempts
                attempts += 1
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

        asyncio.run(coordinator.run_once())

        self.assertEqual(attempts, 2)
        self.assertEqual(delays, [7])


if __name__ == "__main__":
    unittest.main()
