"""Stopping limits cover infrastructure and runtime attempts together."""

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from remote import ExecutionLimit, check_limits, deployment_count


class RemoteLimitsTests(unittest.TestCase):
    def setUp(self):
        self.at = datetime(2026, 9, 22, 5, 20, tzinfo=UTC)
        self.state = {
            "runtime_gate_started_at": (self.at - timedelta(minutes=30)).isoformat(),
            "entra_ssh": "PASS_VIA_BASTION",
            "terraform_applies": 3,
            "runtime_stages": 8,
            "repair_cycles": 4,
        }

    def test_combined_attempts(self):
        self.assertEqual(deployment_count(self.state), 11)
        check_limits(self.state, self.at)

    def test_twelfth_attempt_consumes_remaining_permission(self):
        self.state["runtime_stages"] = 9
        with self.assertRaisesRegex(ExecutionLimit, "Combined"):
            check_limits(self.state, self.at)

    def test_explicit_recorded_ceiling(self):
        self.state.update(maximum_deployments=20, runtime_stages=9)
        check_limits(self.state, self.at)
        self.state["runtime_stages"] = 17
        with self.assertRaisesRegex(ExecutionLimit, "20"):
            check_limits(self.state, self.at)

    def test_expired_clock(self):
        with self.assertRaisesRegex(ExecutionLimit, "elapsed"):
            check_limits(self.state, self.at + timedelta(hours=2))

    def test_repairs_do_not_reset(self):
        self.state["repair_cycles"] = 6
        with self.assertRaisesRegex(ExecutionLimit, "Repair"):
            check_limits(self.state, self.at)

    def test_requires_verified_access(self):
        self.state["entra_ssh"] = "NOT_RUN"
        with self.assertRaisesRegex(RuntimeError, "Bastion"):
            check_limits(self.state, self.at)


if __name__ == "__main__":
    unittest.main()
