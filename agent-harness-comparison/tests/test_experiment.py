import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from harness_lab.experiment import before_deadline, wait_for_slot
from harness_lab.verify import cooldown_gaps


class SchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def test_cooldown_uses_completion_not_elapsed_time(self):
        jobs = {"second": (1060.01, 1100), "suspended": (10, 1000)}
        self.assertAlmostEqual(cooldown_gaps(jobs)[0], 60.01)

    async def test_persistent_cooldown_is_respected(self):
        state = {"cooldown_seconds": 60, "last_model_job_finished": 100}
        with (
            patch("harness_lab.experiment.time.time", return_value=120),
            patch("harness_lab.experiment.before_deadline") as check,
            patch("harness_lab.experiment.asyncio.sleep", new_callable=AsyncMock) as sleep,
        ):
            await wait_for_slot(state)
        sleep.assert_awaited_once_with(40)
        self.assertEqual(check.call_count, 2)

    async def test_deadline_is_checked_again_after_sleep(self):
        state = {"cooldown_seconds": 60, "last_model_job_finished": 100}
        with (
            patch("harness_lab.experiment.time.time", return_value=120),
            patch("harness_lab.experiment.before_deadline", side_effect=[None, RuntimeError("CAPPED")]),
            patch("harness_lab.experiment.asyncio.sleep", new_callable=AsyncMock),
        ):
            with self.assertRaisesRegex(RuntimeError, "CAPPED"):
                await wait_for_slot(state)

    async def test_elapsed_cooldown_never_sleeps(self):
        with (
            patch("harness_lab.experiment.time.time", return_value=200),
            patch("harness_lab.experiment.before_deadline"),
            patch("harness_lab.experiment.asyncio.sleep", new_callable=AsyncMock) as sleep,
        ):
            await wait_for_slot({"cooldown_seconds": 60, "last_model_job_finished": 100})
        sleep.assert_not_awaited()

    async def test_expired_deadline_cannot_start_a_job(self):
        state = {"stop_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}
        with self.assertRaisesRegex(RuntimeError, "CAPPED"):
            before_deadline(state)
