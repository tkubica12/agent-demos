import unittest
import tempfile
from pathlib import Path
from zipfile import ZipFile

from scripts.setup_agent365 import (
    agent365_config_payload,
    build_metadata,
    customize_manifest,
    developer_portal_url,
    merge_config,
    non_secret_generated_fields,
    publish_command,
    setup_command,
    update_endpoint_command,
)


class Agent365SetupTests(unittest.TestCase):
    def test_config_payload_marks_external_hosting(self):
        payload = agent365_config_payload(
            agent_name="OpenClaw",
            tenant_id="tenant-1",
            messaging_endpoint="https://bridge.example/api/messages",
            ai_teammate=True,
            manager_email="manager@example.com",
            agent_user_principal_name="openclaw@example.com",
        )

        self.assertEqual(payload["agentName"], "OpenClaw")
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
            {"agentName": "OpenClaw", "tenantId": "tenant-1", "messagingEndpoint": "https://from-config/api/messages"},
            {"agentBlueprintId": "blueprint-1", "messagingEndpoint": "https://from-generated/api/messages"},
        )

        self.assertEqual(metadata["agentName"], "OpenClaw")
        self.assertEqual(metadata["tenantId"], "tenant-1")
        self.assertEqual(metadata["messagingEndpoint"], "https://from-generated/api/messages")
        self.assertEqual(
            metadata["developerPortalConfigurationUrl"],
            "https://dev.teams.microsoft.com/tools/agent-blueprint/blueprint-1/configuration",
        )

    def test_commands_use_existing_bridge_endpoint(self):
        endpoint = "https://bridge.example/api/messages"

        self.assertEqual(
            setup_command(agent_name="OpenClaw", messaging_endpoint=endpoint, ai_teammate=True, authmode="obo"),
            ["a365", "setup", "all", "--agent-name", "OpenClaw", "--aiteammate", "--m365", "--messaging-endpoint", endpoint],
        )
        self.assertEqual(
            setup_command(agent_name="OpenClaw", messaging_endpoint=endpoint, ai_teammate=False, authmode="both"),
            [
                "a365",
                "setup",
                "all",
                "--agent-name",
                "OpenClaw",
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

            package_path = customize_manifest(Path(temp_dir))

            self.assertTrue(package_path.exists())
            manifest = (manifest_dir / "manifest.json").read_text(encoding="utf-8")
            self.assertIn('"short": "OpenClaw Autopilot"', manifest)
            self.assertIn('"full": "OpenClaw Autopilot on Azure"', manifest)
            with ZipFile(package_path) as archive:
                self.assertIn("manifest.json", archive.namelist())
                self.assertIn("color.png", archive.namelist())


if __name__ == "__main__":
    unittest.main()
