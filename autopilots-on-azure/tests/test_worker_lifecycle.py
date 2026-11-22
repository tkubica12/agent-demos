import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import worker_lifecycle
from scripts.worker_lifecycle import (
    bootstrap_agent365_config,
    endpoint_registration_needed,
    inheritance_complete,
    owned_sandbox_groups,
    redact_secrets,
    release_pin_arguments,
    CHANNEL_ONLY_RESOURCES,
    RELEASE_PIN_FLAGS,
    strip_blueprint_secrets,
    validate_state_name,
    worker_display_name,
    worker_settings_pin_arguments,
)


class WorkerLifecycleTests(unittest.TestCase):
    def test_state_name_is_a_safe_worker_identifier(self):
        self.assertEqual(validate_state_name("g08b"), "g08b")
        self.assertEqual(validate_state_name("demo-anna"), "demo-anna")
        for invalid in ("G08", "8worker", "ab", "worker_1", "worker-", "a" * 25):
            with self.assertRaises(ValueError):
                validate_state_name(invalid)
        self.assertEqual(worker_display_name("g08b"), "G08b")

    def test_bootstrap_config_defers_endpoint_and_pins_cli_app(self):
        config = bootstrap_agent365_config(
            state_name="g08b",
            agent_name="G08b",
            tenant_id="tenant-1",
            sponsor_upn="admin@example.com",
            agent_upn="g08b@example.onmicrosoft.com",
            client_app_id="cli-app",
        )

        self.assertNotIn("messagingEndpoint", config)
        self.assertTrue(config["aiteammate"])
        self.assertEqual(config["agentBlueprintDisplayName"], "G08b Blueprint")
        self.assertEqual(config["managerEmail"], "admin@example.com")
        self.assertEqual(config["agentUserPrincipalName"], "g08b@example.onmicrosoft.com")
        self.assertEqual(config["clientAppId"], "cli-app")

    def test_release_pins_copy_every_release_and_image_value(self):
        tfvars = {key: f"value-{index}" for index, key in enumerate(RELEASE_PIN_FLAGS)}
        arguments = release_pin_arguments(tfvars)

        self.assertEqual(len(arguments), 2 * len(RELEASE_PIN_FLAGS))
        self.assertEqual(arguments[:2], ["--role-blueprint", "value-0"])
        self.assertNotIn("--bridge-image", arguments)
        del tfvars["runtime_image"]
        with self.assertRaisesRegex(ValueError, "runtime_image"):
            release_pin_arguments(tfvars)

    def test_existing_worker_settings_need_no_pin_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = {name: root / name / "tfvars.json" for name in ("worker3", "hermes2")}
            settings["worker3"].parent.mkdir()
            settings["worker3"].write_text("{}", encoding="utf-8")
            with mock.patch.object(worker_lifecycle, "runtime_app_tfvars_path", lambda _runtime, state: settings[state]):
                self.assertIsNone(worker_settings_pin_arguments("worker3", "hermes2"))
                self.assertIsNone(worker_settings_pin_arguments("worker3", "worker3"))

    def test_new_worker_copies_pins_from_another_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = {name: root / name / "tfvars.json" for name in ("worker3", "hermes2")}
            settings["hermes2"].parent.mkdir()
            pins = {key: f"value-{index}" for index, key in enumerate(RELEASE_PIN_FLAGS)}
            settings["hermes2"].write_text(json.dumps(pins), encoding="utf-8")
            with mock.patch.object(worker_lifecycle, "runtime_app_tfvars_path", lambda _runtime, state: settings[state]):
                self.assertEqual(worker_settings_pin_arguments("worker3", "hermes2"), release_pin_arguments(pins))
                with self.assertRaisesRegex(ValueError, "another"):
                    worker_settings_pin_arguments("worker3", "worker3")

    def test_inheritance_requires_every_resource(self):
        self.assertTrue(inheritance_complete("Summary: 7 of 7 resources have inheritance"))
        self.assertFalse(inheritance_complete("Summary: 6 of 7 resources have inheritance"))
        self.assertFalse(inheritance_complete("Summary: 0 of 0 resources"))
        self.assertFalse(inheritance_complete("no summary"))

    def test_inheritance_without_channel_ignores_bot_and_power_platform(self):
        def block(name, app_id, effective):
            return f"Resource: {name} ({app_id})\n  Scopes: OK\n  Effective inheritance: {effective}\n\n"

        tools = block("Microsoft Graph", "00000003-0000-0000-c000-000000000000", "OK") + block(
            "Work IQ Mail MCP", "16b1878d-62c7-4009-aa25-68989d63bbad", "OK"
        )
        bot = block("Messaging Bot API", "5a807f24-c9de-44ee-a3a7-329e88a00ffc", "NONE - not granted")
        power = block("Power Platform API", "8578e004-a5c6-46e7-913e-12f58912df43", "NONE - not granted")
        output = tools + bot + power + "Summary: 2 of 4 resource(s) have effective inheritance"

        self.assertFalse(inheritance_complete(output))
        self.assertTrue(inheritance_complete(output, ignored_resources=CHANNEL_ONLY_RESOURCES))
        missing_tool = tools.replace("Effective inheritance: OK\n\nResource: Work IQ", "Effective inheritance: NONE\n\nResource: Work IQ")
        missing_tool = missing_tool.rsplit("Effective inheritance: OK", 1)[0] + "Effective inheritance: NONE\n\n"
        self.assertFalse(
            inheritance_complete(missing_tool + bot + power + "Summary: 1 of 4 resource(s)", ignored_resources=CHANNEL_ONLY_RESOURCES)
        )
        self.assertFalse(inheritance_complete(bot + power + "Summary: 0 of 2 resource(s)", ignored_resources=CHANNEL_ONLY_RESOURCES))
        self.assertFalse(inheritance_complete(tools + "Summary: 2 of 3 resource(s)", ignored_resources=CHANNEL_ONLY_RESOURCES))

    def test_endpoint_is_registered_once_and_never_replaced(self):
        endpoint = "https://worker.example/api/messages"
        self.assertTrue(endpoint_registration_needed("", endpoint))
        self.assertFalse(endpoint_registration_needed(endpoint + "/", endpoint))
        with self.assertRaisesRegex(RuntimeError, "different messaging endpoint"):
            endpoint_registration_needed("https://other.example/api/messages", endpoint)

    def test_blueprint_secret_is_removed_from_generated_config(self):
        generated = {
            "agentBlueprintId": "blueprint-1",
            "agentBlueprintClientSecret": "abc8Q~secret",
            "agentBlueprintClientSecretProtected": False,
        }

        self.assertEqual(
            strip_blueprint_secrets(generated),
            {"agentBlueprintId": "blueprint-1", "agentBlueprintClientSecretProtected": False},
        )

    def test_command_output_redacts_client_secrets_and_tokens(self):
        line = (
            "secret abc8Q~AbCdEfGhIjKlMnOpQrStUvWxYz0123 token "
            "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl blueprint "
            "7ec96f90-136f-4614-96d6-626bfb1da608"
        )

        redacted = redact_secrets(line)

        self.assertNotIn("AbCdEf", redacted)
        self.assertNotIn("eyJhbGci", redacted)
        self.assertIn("7ec96f90-136f-4614-96d6-626bfb1da608", redacted)

    def test_teardown_refuses_sandbox_groups_of_other_workers(self):
        outputs = {
            "sandbox_groups": {
                "runtime": {"name": "sbg-autopilots-g08b-runtime"},
                "gateway": {"name": "sbg-autopilots-g08b-gateway"},
            }
        }
        self.assertEqual(
            owned_sandbox_groups(outputs, "g08b"),
            {"runtime": "sbg-autopilots-g08b-runtime", "gateway": "sbg-autopilots-g08b-gateway"},
        )
        outputs["sandbox_groups"]["gateway"]["name"] = "sbg-autopilots-hermes2-gateway"
        with self.assertRaisesRegex(ValueError, "hermes2"):
            owned_sandbox_groups(outputs, "g08b")
        self.assertEqual(owned_sandbox_groups({}, "g08b"), {})


if __name__ == "__main__":
    unittest.main()
