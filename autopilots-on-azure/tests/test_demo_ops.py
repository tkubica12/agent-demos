import unittest

from scripts.demo_ops import (
    SANDBOX_DATA_OWNER_ROLE,
    az_log_command,
    invoke_body,
    missing_expected_markers,
    role_assignment_command,
    runtime_list,
    runtime_sandbox_selector,
    sandbox_matches,
)


class DemoOpsTests(unittest.TestCase):
    def test_runtime_list_expands_both_in_stable_order(self):
        self.assertEqual(runtime_list("both"), ["openclaw", "hermes"])

    def test_invoke_body_uses_runtime_default_prompt(self):
        body = invoke_body("hermes")

        self.assertEqual(body["conversationId"], "hermes-operator-smoke")
        self.assertEqual(body["message"], "Reply with exactly: Hermes bridge OK")

    def test_missing_expected_markers_detects_failed_openclaw_smoke(self):
        missing = missing_expected_markers("openclaw", {"response": "core_banking only"})

        self.assertIn("card_payments", missing)

    def test_log_command_can_include_follow(self):
        command = az_log_command("app-1", "rg-1", tail=25, follow=True)

        self.assertEqual(command[:6], ["az", "containerapp", "logs", "show", "--name", "app-1"])
        self.assertIn("--follow", command)

    def test_runtime_sandbox_selector_uses_captured_runtime_state(self):
        selector = runtime_sandbox_selector(
            "hermes",
            {"runtime_disk_image_name": "hermes-image", "runtime_data_volume_name": "hermes-data"},
        )

        self.assertEqual(selector["labels"]["kind"], "hermes")
        self.assertEqual(selector["dataVolume"], "hermes-data")

    def test_sandbox_matches_labels_and_data_volume(self):
        self.assertTrue(
            sandbox_matches(
                {
                    "labels": {"app": "autopilots-on-azure", "kind": "hermes"},
                    "volumes": [{"volumeName": "hermes-data"}],
                },
                {"labels": {"app": "autopilots-on-azure", "kind": "hermes"}, "dataVolume": "hermes-data"},
            )
        )

    def test_role_assignment_command_grants_sandbox_data_owner(self):
        command = role_assignment_command("scope-1", "user-1")

        self.assertEqual(command[:4], ["az", "role", "assignment", "create"])
        self.assertIn(SANDBOX_DATA_OWNER_ROLE, command)
        self.assertIn("user-1", command)


if __name__ == "__main__":
    unittest.main()
