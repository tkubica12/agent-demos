import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import scripts.setup_app_tfvars as setup_app_tfvars
from scripts.setup_app_tfvars import (
    build_tfvars,
    default_bot_display_name,
    default_data_volume_name,
    existing_app_tfvars,
    reusable_data_volume_name,
    runtime_workspace,
)


class SetupAppTfvarsTests(unittest.TestCase):
    def test_openclaw_tfvars_include_pairing_values(self):
        tfvars = build_tfvars(
            runtime="openclaw",
            autopilot_name="openclaw",
            bot_display_name="OpenClaw Autopilot",
            data_volume_name="openclaw-kind-data",
            previous={},
            device={"privateKeyPem": "private-key", "deviceId": "device-1"},
            gateway_token="gateway-token",
            approved_device_token="device-token",
        )

        self.assertEqual(tfvars["agent_runtime"], "openclaw")
        self.assertEqual(tfvars["autopilot_name"], "openclaw")
        self.assertEqual(tfvars["bot_display_name"], "OpenClaw Autopilot")
        self.assertEqual(tfvars["runtime_data_volume_name"], "openclaw-kind-data")
        self.assertEqual(tfvars["openclaw_gateway_token"], "gateway-token")
        self.assertEqual(tfvars["openclaw_bridge_device_private_key_pem"], "private-key")
        self.assertEqual(tfvars["openclaw_bridge_device_token"], "device-token")
        self.assertNotIn("api_server_key", tfvars)

    def test_hermes_tfvars_include_api_server_key_without_openclaw_pairing(self):
        tfvars = build_tfvars(
            runtime="hermes",
            autopilot_name="hermes",
            bot_display_name="Hermes Autopilot",
            data_volume_name="hermes-data",
            previous={},
            api_server_key="api-key",
            runtime_image="registry.example/hermes-runtime@sha256:test",
        )

        self.assertEqual(tfvars["agent_runtime"], "hermes")
        self.assertEqual(tfvars["autopilot_name"], "hermes")
        self.assertEqual(tfvars["bot_display_name"], "Hermes Autopilot")
        self.assertEqual(tfvars["runtime_data_volume_name"], "hermes-data")
        self.assertEqual(tfvars["api_server_key"], "api-key")
        self.assertEqual(tfvars["runtime_image"], "registry.example/hermes-runtime@sha256:test")
        self.assertEqual(tfvars["runtime_disk_image_name"], "hermes-api-server-image")
        self.assertNotIn("openclaw_gateway_token", tfvars)
        self.assertNotIn("openclaw_bridge_device_private_key_pem", tfvars)

    def test_runtime_defaults_keep_side_by_side_state_distinct(self):
        self.assertEqual(default_bot_display_name("openclaw"), "OpenClaw Autopilot")
        self.assertEqual(default_bot_display_name("hermes"), "Hermes Autopilot")
        self.assertEqual(default_data_volume_name("openclaw"), "openclaw-kind-data")
        self.assertEqual(default_data_volume_name("hermes"), "hermes-data")
        self.assertEqual(runtime_workspace("openclaw"), Path.cwd() / ".local" / "openclaw" / "apps")
        self.assertEqual(runtime_workspace("hermes"), Path.cwd() / ".local" / "hermes" / "apps")

    def test_hermes_existing_tfvars_ignore_active_openclaw_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            apps_dir = root / "terraform" / "apps"
            apps_dir.mkdir(parents=True)
            (apps_dir / "generated.runtime.auto.tfvars.json").write_text(
                '{"agent_runtime":"openclaw","runtime_data_volume_name":"openclaw-kind-data"}',
                encoding="utf-8",
            )
            hermes_dir = root / ".local" / "hermes" / "apps"
            hermes_dir.mkdir(parents=True)

            with patch.object(setup_app_tfvars, "REPO_ROOT", root), patch.object(setup_app_tfvars, "APPS_DIR", apps_dir):
                self.assertEqual(existing_app_tfvars("hermes"), {})

    def test_runtime_does_not_reuse_other_runtime_default_volume(self):
        self.assertEqual(reusable_data_volume_name("hermes", "openclaw-kind-data"), "")
        self.assertEqual(reusable_data_volume_name("openclaw", "hermes-data"), "")
        self.assertEqual(reusable_data_volume_name("hermes", "hermes-custom-data"), "hermes-custom-data")


if __name__ == "__main__":
    unittest.main()
