import unittest

from scripts.demo_cohort import (
    matching_sandboxes,
    require_demo_branch,
    require_demo_owned,
)


class DemoCohortTests(unittest.TestCase):
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
