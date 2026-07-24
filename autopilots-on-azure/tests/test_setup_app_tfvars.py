import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import scripts.setup_app_tfvars as setup_app_tfvars
from scripts.setup_app_tfvars import (
    build_tfvars,
    default_data_volume_name,
    existing_app_tfvars,
    load_or_create_collective_approval_identity,
    reusable_data_volume_name,
    runtime_app_tfvars_path,
    runtime_outputs_path,
    runtime_workspace,
)


class SetupAppTfvarsTests(unittest.TestCase):
    def test_collective_learning_approval_identity_is_stable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "approval.json"
            first = load_or_create_collective_approval_identity(path)
            second = load_or_create_collective_approval_identity(path)

        self.assertEqual(first, second)
        self.assertEqual(len(first["privateKey"]), 44)
        self.assertEqual(len(first["publicKey"]), 44)

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
            bridge_image="registry.example/bridge@sha256:test",
            private_mcp_image="registry.example/mcp@sha256:test",
            agent365_client_id="blueprint-id",
            agent365_client_secret="blueprint-secret",
            agent365_tenant_id="tenant-id",
            role_blueprint="junior-project-manager",
            role_blueprint_source="https://github.com/example/agent-demos.git",
            role_blueprint_path="autopilots-on-azure/blueprints/junior-project-manager",
            role_release="3.0.0",
            role_release_commit="a" * 40,
            assignment_scope="team-alpha",
            collective_approval_private_key="approval-private",
            collective_approval_public_key="approval-public",
        )

        self.assertEqual(tfvars["agent_runtime"], "hermes")
        self.assertEqual(tfvars["autopilot_name"], "hermes")
        self.assertEqual(tfvars["runtime_data_volume_name"], "hermes-data")
        self.assertEqual(tfvars["api_server_key"], "api-key")
        self.assertEqual(tfvars["runtime_image"], "registry.example/hermes-runtime@sha256:test")
        self.assertEqual(tfvars["bridge_image"], "registry.example/bridge@sha256:test")
        self.assertEqual(tfvars["private_mcp_image"], "registry.example/mcp@sha256:test")
        self.assertEqual(tfvars["runtime_disk_image_name"], "hermes-api-server-image")
        self.assertEqual(tfvars["agent365_client_id"], "blueprint-id")
        self.assertEqual(tfvars["agent365_client_secret"], "blueprint-secret")
        self.assertEqual(tfvars["agent365_tenant_id"], "tenant-id")
        self.assertEqual(tfvars["hermes_role_blueprint"], "junior-project-manager")
        self.assertEqual(tfvars["hermes_role_release"], "3.0.0")
        self.assertEqual(tfvars["hermes_role_release_commit"], "a" * 40)
        self.assertEqual(tfvars["worker_assignment_scope"], "team-alpha")
        self.assertEqual(tfvars["collective_learning_approval_private_key"], "approval-private")
        self.assertEqual(tfvars["collective_learning_approval_public_key"], "approval-public")
        self.assertFalse(tfvars["scheduled_learning_enabled"])
        self.assertEqual(tfvars["scheduled_learning_interval_seconds"], 86_400)
        self.assertTrue(tfvars["scheduled_learning_prepare_packet"])
        self.assertFalse(tfvars["servicebus_dream_enabled"])
        self.assertEqual(tfvars["servicebus_dream_cron_expression"], "0 2 * * *")
        self.assertNotIn("openclaw_gateway_token", tfvars)
        self.assertNotIn("openclaw_bridge_device_private_key_pem", tfvars)

    def test_scheduled_learning_values_override_and_then_persist(self):
        configured = build_tfvars(
            runtime="hermes",
            autopilot_name="hermes",
            data_volume_name="hermes-data",
            previous={},
            api_server_key="api-key",
            role_blueprint="junior-project-manager",
            role_blueprint_source="https://example.com/roles.git",
            role_release="3.2.0",
            role_release_commit="a" * 40,
            scheduled_learning_enabled=True,
            scheduled_learning_initial_delay_seconds=10,
            scheduled_learning_interval_seconds=3_600,
            scheduled_learning_focus="review delivery handoffs",
            scheduled_learning_max_records=2,
            scheduled_learning_retry_limit=4,
            scheduled_learning_retry_backoff_seconds=15,
            scheduled_learning_prepare_packet=False,
            servicebus_dream_enabled=True,
            servicebus_dream_cron_expression="15 3 * * *",
        )
        reused = build_tfvars(
            runtime="hermes",
            autopilot_name="hermes",
            data_volume_name="hermes-data",
            previous=configured,
            api_server_key="api-key",
            role_blueprint="junior-project-manager",
            role_blueprint_source="https://example.com/roles.git",
            role_release="3.2.0",
            role_release_commit="a" * 40,
        )

        self.assertTrue(reused["scheduled_learning_enabled"])
        self.assertEqual(reused["scheduled_learning_initial_delay_seconds"], 10)
        self.assertEqual(reused["scheduled_learning_interval_seconds"], 3_600)
        self.assertEqual(reused["scheduled_learning_focus"], "review delivery handoffs")
        self.assertEqual(reused["scheduled_learning_max_records"], 2)
        self.assertEqual(reused["scheduled_learning_retry_limit"], 4)
        self.assertEqual(reused["scheduled_learning_retry_backoff_seconds"], 15)
        self.assertFalse(reused["scheduled_learning_prepare_packet"])
        self.assertTrue(reused["servicebus_dream_enabled"])
        self.assertEqual(reused["servicebus_dream_cron_expression"], "15 3 * * *")

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
            role_blueprint="junior-project-manager",
            role_blueprint_source="https://example.com/roles.git",
            role_release="3.0.0",
            role_release_commit="a" * 40,
        )

        self.assertEqual(tfvars["agent365_client_id"], "previous-client")
        self.assertEqual(tfvars["agent365_client_secret"], "previous-secret")
        self.assertEqual(tfvars["agent365_tenant_id"], "previous-tenant")

    def test_hermes_tfvars_remove_pre_a10_blueprint_keys(self):
        tfvars = build_tfvars(
            runtime="hermes",
            autopilot_name="hermes",
            data_volume_name="hermes-data",
            previous={
                "hermes_blueprint_name": "legacy",
                "hermes_blueprint_source": "legacy",
                "hermes_blueprint_version": "2.3.0",
                "hermes_blueprint_commit": "a" * 40,
                "hermes_assignee_scope": "legacy",
            },
            api_server_key="api-key",
            role_blueprint="junior-project-manager",
            role_blueprint_source="https://github.com/example/agent-demos.git",
            role_release="3.0.0",
            role_release_commit="b" * 40,
            assignment_scope="team-alpha",
        )

        self.assertFalse(any(key.startswith("hermes_blueprint_") for key in tfvars))
        self.assertNotIn("hermes_assignee_scope", tfvars)

    def test_a9_tfvars_require_explicit_role_release_migration(self):
        with self.assertRaisesRegex(ValueError, "require explicit"):
            build_tfvars(
                runtime="hermes",
                autopilot_name="hermes",
                data_volume_name="hermes-data",
                previous={
                    "hermes_blueprint_name": "junior-project-manager",
                    "hermes_blueprint_source": "https://example.com/roles.git",
                    "hermes_blueprint_version": "2.3.0",
                    "hermes_blueprint_commit": "a" * 40,
                },
                api_server_key="api-key",
            )

    def test_api_server_key_rotation_preserves_previous_key_for_refresh_preflight(self):
        tfvars = build_tfvars(
            runtime="hermes",
            autopilot_name="hermes",
            data_volume_name="hermes-data",
            previous={"api_server_key": "old-key"},
            api_server_key="new-key",
            role_blueprint="junior-project-manager",
            role_blueprint_source="https://example.com/roles.git",
            role_release="3.0.0",
            role_release_commit="a" * 40,
        )

        self.assertEqual(tfvars["api_server_key"], "new-key")
        self.assertEqual(tfvars["previous_api_server_key"], "old-key")

    def test_runtime_tfvars_preserve_identity_and_mcp_values(self):
        tfvars = build_tfvars(
            runtime="hermes",
            autopilot_name="hermes",
            data_volume_name="hermes-data",
            previous={
                "agent365_agent_identity_client_id": "agent-id",
                "agent365_agent_user_id": "agent-user-id",
                "private_mcp_api_audience": "api://private",
                "public_shipments_mcp_api_audience": "api://shipments",
            },
            api_server_key="api-key",
            role_blueprint="junior-project-manager",
            role_blueprint_source="https://example.com/roles.git",
            role_release="3.0.0",
            role_release_commit="a" * 40,
        )

        self.assertEqual(tfvars["agent365_agent_identity_client_id"], "agent-id")
        self.assertEqual(tfvars["agent365_agent_user_id"], "agent-user-id")
        self.assertEqual(tfvars["private_mcp_api_audience"], "api://private")
        self.assertEqual(tfvars["public_shipments_mcp_api_audience"], "api://shipments")

    def test_hermes_role_release_requires_full_commit_pinning(self):
        with self.assertRaisesRegex(ValueError, "full 40-character"):
            build_tfvars(
                runtime="hermes",
                autopilot_name="hermes",
                data_volume_name="hermes-data",
                previous={},
                role_blueprint="junior-project-manager",
                role_blueprint_source="https://github.com/example/agent-demos.git",
                role_release="3.0.0",
                role_release_commit="main",
            )

    def test_runtime_defaults_keep_side_by_side_state_distinct(self):
        self.assertEqual(default_data_volume_name("openclaw"), "openclaw-kind-data")
        self.assertEqual(default_data_volume_name("hermes"), "hermes-data")
        self.assertEqual(default_data_volume_name("hermes", "jpm-team-alpha"), "hermes-jpm-team-alpha-data")
        self.assertEqual(runtime_workspace("openclaw"), Path.cwd() / ".local" / "openclaw" / "apps")
        self.assertEqual(runtime_workspace("hermes"), Path.cwd() / ".local" / "hermes" / "apps")
        self.assertEqual(runtime_app_tfvars_path("hermes"), Path.cwd() / ".local" / "hermes" / "apps" / "generated.app.auto.tfvars.json")
        self.assertEqual(runtime_outputs_path("hermes"), Path.cwd() / ".local" / "hermes" / "apps" / "terraform-outputs.json")
        self.assertEqual(
            runtime_app_tfvars_path("hermes", "hermes2"),
            Path.cwd() / ".local" / "hermes2" / "apps" / "generated.app.auto.tfvars.json",
        )

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
                self.assertEqual(existing_app_tfvars("hermes", "hermes2"), {})

    def test_runtime_does_not_reuse_other_runtime_default_volume(self):
        self.assertEqual(reusable_data_volume_name("hermes", "openclaw-kind-data"), "")
        self.assertEqual(reusable_data_volume_name("openclaw", "hermes-data"), "")
        self.assertEqual(reusable_data_volume_name("hermes", "hermes-custom-data"), "hermes-custom-data")


if __name__ == "__main__":
    unittest.main()
