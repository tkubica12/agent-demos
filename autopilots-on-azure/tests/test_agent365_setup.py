import unittest
import tempfile
from pathlib import Path
from zipfile import ZipFile

from scripts.setup_agent365 import (
    Agent365Branding,
    agent365_config_payload,
    agent365_workspace,
    build_metadata,
    customize_manifest,
    default_branding,
    developer_portal_url,
    metadata_file_name,
    merge_config,
    messaging_endpoint_from_outputs,
    non_secret_generated_fields,
    normalize_messaging_endpoint,
    publish_command,
    resolve_messaging_endpoint,
    setup_command,
    update_endpoint_command,
)


class Agent365SetupTests(unittest.TestCase):
    def test_config_payload_marks_external_hosting(self):
        payload = agent365_config_payload(
            autopilot_name="openclaw",
            runtime_kind="openclaw",
            agent_name="OpenClaw",
            tenant_id="tenant-1",
            messaging_endpoint="https://bridge.example/api/messages",
            ai_teammate=True,
            manager_email="manager@example.com",
            agent_user_principal_name="openclaw@example.com",
        )

        self.assertEqual(payload["agentName"], "OpenClaw")
        self.assertEqual(payload["autopilotName"], "openclaw")
        self.assertEqual(payload["agentRuntime"], "openclaw")
        self.assertEqual(payload["agentIdentityDisplayName"], "OpenClaw Agent")
        self.assertEqual(payload["agentBlueprintDisplayName"], "OpenClaw Blueprint")
        self.assertEqual(payload["messagingEndpoint"], "https://bridge.example/api/messages")
        self.assertFalse(payload["needDeployment"])
        self.assertEqual(payload["deploymentProjectPath"], ".")
        self.assertTrue(payload["aiteammate"])
        self.assertEqual(payload["managerEmail"], "manager@example.com")
        self.assertEqual(payload["agentUserPrincipalName"], "openclaw@example.com")

    def test_generated_metadata_excludes_secrets(self):
        generated = {
            "agentBlueprintId": "blueprint-1",
            "agentBlueprintClientSecret": "secret",
            "customSecretValue": "secret",
            "AgenticUserId": "user-1",
        }

        clean = non_secret_generated_fields(generated)

        self.assertEqual(clean, {"agentBlueprintId": "blueprint-1", "AgenticUserId": "user-1"})

    def test_merge_config_preserves_existing_optional_values(self):
        merged = merge_config(
            {"managerEmail": "manager@example.com", "agentUserPrincipalName": "openclaw@example.com"},
            {"agentName": "OpenClaw", "messagingEndpoint": "https://bridge.example/api/messages"},
        )

        self.assertEqual(merged["managerEmail"], "manager@example.com")
        self.assertEqual(merged["agentUserPrincipalName"], "openclaw@example.com")
        self.assertEqual(merged["agentName"], "OpenClaw")

    def test_metadata_includes_portal_links_and_endpoint(self):
        metadata = build_metadata(
            {
                "agentName": "OpenClaw",
                "autopilotName": "openclaw",
                "agentRuntime": "openclaw",
                "tenantId": "tenant-1",
                "messagingEndpoint": "https://from-config/api/messages",
            },
            {"agentBlueprintId": "blueprint-1", "messagingEndpoint": "https://from-generated/api/messages"},
        )

        self.assertEqual(metadata["agentName"], "OpenClaw")
        self.assertEqual(metadata["autopilotName"], "openclaw")
        self.assertEqual(metadata["agentRuntime"], "openclaw")
        self.assertEqual(metadata["tenantId"], "tenant-1")
        self.assertEqual(metadata["messagingEndpoint"], "https://from-generated/api/messages")
        self.assertEqual(
            metadata["developerPortalConfigurationUrl"],
            "https://dev.teams.microsoft.com/tools/agent-blueprint/blueprint-1/configuration",
        )

    def test_commands_use_existing_bridge_endpoint(self):
        endpoint = "https://bridge.example/api/messages"

        self.assertEqual(
            setup_command(agent_name="OpenClaw", tenant_id="tenant-1", messaging_endpoint=endpoint, ai_teammate=True, authmode="obo"),
            [
                "a365",
                "setup",
                "all",
                "--agent-name",
                "OpenClaw",
                "--tenant-id",
                "tenant-1",
                "--aiteammate",
                "--m365",
                "--messaging-endpoint",
                endpoint,
            ],
        )
        self.assertEqual(
            setup_command(agent_name="OpenClaw", tenant_id="tenant-1", messaging_endpoint=endpoint, ai_teammate=False, authmode="both"),
            [
                "a365",
                "setup",
                "all",
                "--agent-name",
                "OpenClaw",
                "--tenant-id",
                "tenant-1",
                "--m365",
                "--messaging-endpoint",
                endpoint,
                "--authmode",
                "both",
            ],
        )
        self.assertEqual(update_endpoint_command(endpoint), ["a365", "setup", "blueprint", "--update-endpoint", endpoint])
        self.assertEqual(publish_command(agent_name="OpenClaw", ai_teammate=True), ["a365", "publish", "--agent-name", "OpenClaw", "--aiteammate"])
        self.assertEqual(
            publish_command(agent_name="OpenClaw", ai_teammate=False),
            ["a365", "publish", "--agent-name", "OpenClaw", "--use-blueprint"],
        )

    def test_setup_command_supports_safe_execution_flags(self):
        self.assertEqual(
            setup_command(
                agent_name="OpenClaw",
                tenant_id="tenant-1",
                messaging_endpoint="https://bridge.example/api/messages",
                ai_teammate=False,
                authmode="obo",
                dry_run=True,
                skip_requirements=True,
                skip_sp_provisioning=True,
            )[-3:],
            ["--dry-run", "--skip-requirements", "--skip-sp-provisioning"],
        )

    def test_messaging_endpoint_normalization_accepts_bridge_base_url(self):
        self.assertEqual(
            normalize_messaging_endpoint("https://bridge.example"),
            "https://bridge.example/api/messages",
        )
        self.assertEqual(
            normalize_messaging_endpoint("https://bridge.example/api/messages"),
            "https://bridge.example/api/messages",
        )

    def test_messaging_endpoint_from_runtime_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs_path = Path(temp_dir) / "terraform-outputs.json"
            outputs_path.write_text('{"bridge_url":"https://runtime-bridge.example"}', encoding="utf-8")

            self.assertEqual(
                messaging_endpoint_from_outputs(outputs_path),
                "https://runtime-bridge.example/api/messages",
            )

    def test_explicit_messaging_endpoint_wins_over_outputs_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs_path = Path(temp_dir) / "terraform-outputs.json"
            outputs_path.write_text('{"bridge_url":"https://runtime-bridge.example"}', encoding="utf-8")

            self.assertEqual(
                resolve_messaging_endpoint(
                    runtime_kind="openclaw",
                    explicit_endpoint="https://explicit.example/api/messages",
                    outputs_file=str(outputs_path),
                ),
                "https://explicit.example/api/messages",
            )

    def test_developer_portal_url_is_empty_without_blueprint_id(self):
        self.assertEqual(developer_portal_url(""), "")

    def test_customize_manifest_updates_branding_and_zip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_dir = Path(temp_dir) / "manifest"
            manifest_dir.mkdir()
            (manifest_dir / "manifest.json").write_text(
                '{"name":{"short":"OpenClaw Blueprint","full":"OpenClaw Blueprint"},"description":{"short":"x","full":"y"},"developer":{}}',
                encoding="utf-8",
            )
            (manifest_dir / "color.png").write_bytes(b"color")
            (manifest_dir / "outline.png").write_bytes(b"outline")

            package_path = customize_manifest(Path(temp_dir), default_branding("openclaw"))

            self.assertTrue(package_path.exists())
            manifest = (manifest_dir / "manifest.json").read_text(encoding="utf-8")
            self.assertIn('"short": "OpenClaw Autopilot"', manifest)
            self.assertIn('"full": "OpenClaw Autopilot on Azure"', manifest)
            with ZipFile(package_path) as archive:
                self.assertIn("manifest.json", archive.namelist())
                self.assertIn("color.png", archive.namelist())

    def test_hermes_defaults_use_separate_branding_and_metadata(self):
        branding = default_branding("hermes")

        self.assertEqual(branding.autopilot_name, "hermes")
        self.assertEqual(branding.agent_name, "Hermes Autopilot")
        self.assertEqual(branding.manifest_short_name, "Hermes Autopilot")
        self.assertEqual(metadata_file_name("hermes"), "hermes-agent365-identifiers.json")
        self.assertEqual(agent365_workspace("hermes"), Path.cwd() / ".local" / "hermes" / "agent365")

    def test_customize_manifest_uses_hermes_branding(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_dir = Path(temp_dir) / "manifest"
            manifest_dir.mkdir()
            (manifest_dir / "manifest.json").write_text(
                '{"name":{"short":"Old","full":"Old Full"},"description":{"short":"x","full":"y"},"developer":{}}',
                encoding="utf-8",
            )
            (manifest_dir / "color.png").write_bytes(b"color")

            customize_manifest(Path(temp_dir), default_branding("hermes"))

            manifest = (manifest_dir / "manifest.json").read_text(encoding="utf-8")
            self.assertIn('"short": "Hermes Autopilot"', manifest)
            self.assertIn('"full": "Hermes Autopilot on Azure"', manifest)


if __name__ == "__main__":
    unittest.main()
