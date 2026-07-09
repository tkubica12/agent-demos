import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import scripts.setup_app_tfvars as setup_app_tfvars
from scripts.setup_app_tfvars import (
    build_tfvars,
    default_data_volume_name,
    existing_app_tfvars,
    reusable_data_volume_name,
    runtime_app_tfvars_path,
    runtime_outputs_path,
    runtime_workspace,
)


class SetupAppTfvarsTests(unittest.TestCase):
    def test_openclaw_tfvars_include_pairing_values(self):
        tfvars = build_tfvars(
            runtime="openclaw",
            autopilot_name="openclaw",
            data_volume_name="openclaw-kind-data",
            previous={},
            device={"privateKeyPem": "private-key", "deviceId": "device-1"},
            gateway_token="gateway-token",
            approved_device_token="device-token",
        )

        self.assertEqual(tfvars["agent_runtime"], "openclaw")
        self.assertEqual(tfvars["autopilot_name"], "openclaw")
        self.assertEqual(tfvars["runtime_data_volume_name"], "openclaw-kind-data")
        self.assertEqual(tfvars["openclaw_gateway_token"], "gateway-token")
        self.assertEqual(tfvars["openclaw_bridge_device_private_key_pem"], "private-key")
        self.assertEqual(tfvars["openclaw_bridge_device_token"], "device-token")
        self.assertNotIn("api_server_key", tfvars)

    def test_hermes_tfvars_include_api_server_key_without_openclaw_pairing(self):
        tfvars = build_tfvars(
            runtime="hermes",
            autopilot_name="hermes",
            data_volume_name="hermes-data",
            previous={},
            api_server_key="api-key",
            runtime_image="registry.example/hermes-runtime@sha256:test",
            agent365_client_id="blueprint-id",
            agent365_client_secret="blueprint-secret",
            agent365_tenant_id="tenant-id",
        )

        self.assertEqual(tfvars["agent_runtime"], "hermes")
        self.assertEqual(tfvars["autopilot_name"], "hermes")
        self.assertEqual(tfvars["runtime_data_volume_name"], "hermes-data")
        self.assertEqual(tfvars["api_server_key"], "api-key")
        self.assertEqual(tfvars["runtime_image"], "registry.example/hermes-runtime@sha256:test")
        self.assertEqual(tfvars["runtime_disk_image_name"], "hermes-api-server-image")
        self.assertEqual(tfvars["agent365_client_id"], "blueprint-id")
        self.assertEqual(tfvars["agent365_client_secret"], "blueprint-secret")
        self.assertEqual(tfvars["agent365_tenant_id"], "tenant-id")
        self.assertNotIn("openclaw_gateway_token", tfvars)
        self.assertNotIn("openclaw_bridge_device_private_key_pem", tfvars)

    def test_agent365_auth_values_are_reused_from_previous_tfvars(self):
        tfvars = build_tfvars(
            runtime="hermes",
            autopilot_name="hermes",
            data_volume_name="hermes-data",
            previous={
                "agent365_client_id": "previous-client",
                "agent365_client_secret": "previous-secret",
                "agent365_tenant_id": "previous-tenant",
            },
            api_server_key="api-key",
        )

        self.assertEqual(tfvars["agent365_client_id"], "previous-client")
        self.assertEqual(tfvars["agent365_client_secret"], "previous-secret")
        self.assertEqual(tfvars["agent365_tenant_id"], "previous-tenant")

    def test_runtime_defaults_keep_side_by_side_state_distinct(self):
        self.assertEqual(default_data_volume_name("openclaw"), "openclaw-kind-data")
        self.assertEqual(default_data_volume_name("hermes"), "hermes-data")
        self.assertEqual(runtime_workspace("openclaw"), Path.cwd() / ".local" / "openclaw" / "apps")
        self.assertEqual(runtime_workspace("hermes"), Path.cwd() / ".local" / "hermes" / "apps")
        self.assertEqual(runtime_app_tfvars_path("hermes"), Path.cwd() / ".local" / "hermes" / "apps" / "generated.app.auto.tfvars.json")
        self.assertEqual(runtime_outputs_path("hermes"), Path.cwd() / ".local" / "hermes" / "apps" / "terraform-outputs.json")

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
