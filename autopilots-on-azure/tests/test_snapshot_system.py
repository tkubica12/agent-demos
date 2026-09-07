import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.snapshot_system as snapshot

from scripts.snapshot_system import redact, safe_name


class SnapshotSystemTests(unittest.TestCase):
    def test_snapshot_captures_only_native_worker_sandbox_resources(self):
        outputs = {
            "subscription_id": "sub", "resource_group_name": "rg", "sandbox_location": "swedencentral",
            "sandbox_groups": {
                role: {"name": f"worker-{role}", "id": f"/groups/{role}",
                       "identity_resource_id": f"/identities/{role}"}
                for role in snapshot.SANDBOX_ROLES
            },
        }
        with (
            patch.object(snapshot, "read_local_json", return_value=outputs),
            patch.object(snapshot, "run_json", return_value={}) as run,
            patch.object(snapshot, "write_json") as write,
        ):
            snapshot.capture_azure(Path("snapshots"), ["hermes"], "worker-one")
        commands = [call.args[0] for call in run.call_args_list]
        self.assertFalse(any("containerapp" in command for command in commands))
        self.assertEqual(sum("identity" in command for command in commands), 5)
        self.assertEqual(sum("rest" in command for command in commands), 15)
        self.assertTrue(any("worker-one" in str(call.args[0]) for call in write.call_args_list))

    def test_snapshot_redacts_sandbox_environment_and_runtime_key(self):
        self.assertEqual(redact({"environment": {"API_SERVER_KEY": "hidden"}, "api_server_key": "hidden"}),
                         {"environment": "<redacted>", "api_server_key": "<redacted>"})

    def test_redact_removes_secret_values_recursively(self):
        payload = {
            "clientSecret": "secret-value",
            "nested": {
                "accessToken": "token-value",
                "safe": "visible",
            },
            "items": [{"password": "hidden"}],
        }

        self.assertEqual(
            redact(payload),
            {
                "clientSecret": "<redacted>",
                "nested": {
                    "accessToken": "<redacted>",
                    "safe": "visible",
                },
                "items": [{"password": "<redacted>"}],
            },
        )

    def test_safe_name_replaces_path_hostile_characters(self):
        self.assertEqual(safe_name("app/name:with spaces"), "app_name_with_spaces")


if __name__ == "__main__":
    unittest.main()
