import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import scripts.demo_cohort as cohort

from scripts.demo_cohort import (
    matching_sandboxes,
    require_demo_branch,
    require_demo_owned,
)


class DemoCohortTests(unittest.TestCase):
    def test_reset_uses_captured_worker_runtime_group_not_platform_shared_group(self):
        config = {"autopilot_name": "demo-worker", "runtime_data_volume_name": "demo-worker-data",
                  "hermes_role_release": "3.0.0", "hermes_role_release_commit": "a" * 40}
        outputs = {"worker_id": "demo-worker", "runtime_data_volume_name": "demo-worker-data",
                   "terraform_workspace": "demo-worker",
                   "sandbox_location": "swedencentral", "subscription_id": "subscription",
                   "resource_group_name": "rg-worker",
                   "sandbox_groups": {"runtime": {"name": "worker-runtime-group"}}}
        client = MagicMock()
        client._dp_get.return_value = {"value": []}
        args = SimpleNamespace(state_name="demo-worker", workspace="demo-worker",
                               baseline_release="3.0.0", baseline_commit="a" * 40, execute=False)
        with (
            patch.object(cohort, "load_json", side_effect=[config, outputs]),
            patch.object(cohort, "SandboxGroupClient", return_value=client) as constructor,
            patch.object(cohort, "DefaultAzureCredential"),
            patch("builtins.print"),
        ):
            cohort.reset(args)
        self.assertEqual(constructor.call_args.kwargs["sandbox_group"], "worker-runtime-group")
        self.assertEqual(constructor.call_args.kwargs["subscription_id"], "subscription")
        client.begin_delete_sandbox.assert_not_called()

    def test_reset_requires_every_resource_to_be_demo_owned(self):
        require_demo_owned(
            state_name="demo-hermes-a",
            worker_id="demo-hermes-a",
            volume_name="demo-hermes-a-data",
            workspace="demo-hermes-a",
        )
        with self.assertRaisesRegex(ValueError, "Data Disk"):
            require_demo_owned(
                state_name="demo-hermes-a",
                worker_id="demo-hermes-a",
                volume_name="hermes-data",
                workspace="demo-hermes-a",
            )

    def test_only_exact_worker_and_volume_sandboxes_match(self):
        sandboxes = [
            {
                "id": "sandbox-1",
                "labels": {"worker": "demo-hermes-a"},
                "volumes": [{"volumeName": "demo-hermes-a-data"}],
            },
            {
                "id": "sandbox-2",
                "labels": {"worker": "hermes"},
                "volumes": [{"volumeName": "demo-hermes-a-data"}],
            },
            {
                "id": "sandbox-3",
                "labels": {"worker": "demo-hermes-a"},
                "volumes": [{"volumeName": "hermes-data"}],
            },
        ]

        matches = matching_sandboxes(
            sandboxes,
            worker_id="demo-hermes-a",
            volume_name="demo-hermes-a-data",
        )

        self.assertEqual([item["id"] for item in matches], ["sandbox-1"])

    def test_disposable_git_base_requires_demo_namespace(self):
        require_demo_branch("demo/collective-learning-class")
        with self.assertRaisesRegex(ValueError, "demo"):
            require_demo_branch("main")


if __name__ == "__main__":
    unittest.main()
