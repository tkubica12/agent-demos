import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
from zipfile import ZipFile

import scripts.setup_agent365 as agent365
from scripts.setup_agent365 import (
    Agent365Branding,
    agent365_config_payload,
    agent365_workspace,
    bump_manifest_patch_version,
    build_metadata,
    customize_manifest,
    default_branding,
    developer_portal_url,
    metadata_file_name,
    merge_config,
    messaging_endpoint_from_outputs,
    missing_tooling_permissions,
    non_secret_generated_fields,
    normalize_messaging_endpoint,
    publish_command,
    remove_unsupported_agentic_bot_capability,
    resolve_messaging_endpoint,
    setup_command,
    update_endpoint_command,
)
from scripts.setup_identity import ensure_federated_credential
from scripts.provision_agent365_instance import GraphClient, GraphError


class Agent365SetupTests(unittest.TestCase):
    def test_endpoint_owner_preflight_resolves_application_object_and_checks_direct_owner(self):
        graph = Mock()
        graph.request.side_effect = [
            {"id": "owner-id", "userPrincipalName": "owner@example.com"},
            {"value": [{"id": "blueprint-object-id", "appId": "blueprint-client-id"}]},
            {"value": [{"id": "OWNER-ID"}]},
        ]
        with (
            patch.object(agent365, "load_json", return_value={"agentBlueprintId": "blueprint-client-id"}),
            patch.object(GraphClient, "from_az_cli", return_value=graph),
        ):
            agent365.require_endpoint_update_owner(Path("worker"))
        calls = graph.request.call_args_list
        self.assertEqual(calls[0].args, ("GET", "/me?$select=id,userPrincipalName"))
        self.assertIn("blueprint-client-id", calls[1].args[1])
        self.assertEqual(
            calls[2].args,
            ("GET", "/applications/blueprint-object-id/microsoft.graph.agentIdentityBlueprint/owners?$select=id"),
        )
        self.assertTrue(all(call.args[0] == "GET" for call in calls))

    def test_endpoint_owner_preflight_follows_owner_pages(self):
        next_page = "https://graph.microsoft.com/v1.0/applications/blueprint-object-id/microsoft.graph.agentIdentityBlueprint/owners?$skiptoken=next"
        graph = Mock()
        graph.request.side_effect = [
            {"id": "owner-id"},
            {"value": [{"id": "blueprint-object-id"}]},
            {"value": [{"id": "other-owner"}], "@odata.nextLink": next_page},
            {"value": [{"id": "owner-id"}]},
        ]
        with (
            patch.object(agent365, "load_json", return_value={"agentBlueprintId": "blueprint-client-id"}),
            patch.object(GraphClient, "from_az_cli", return_value=graph),
        ):
            agent365.require_endpoint_update_owner(Path("worker"))
        self.assertEqual(graph.request.call_args.args, ("GET", next_page))

    def test_endpoint_owner_preflight_blocks_nonowner_actionably(self):
        graph = Mock()
        graph.request.side_effect = [
            {"id": "operator-id", "userPrincipalName": "operator@example.com"},
            {"value": [{"id": "blueprint-object-id"}]},
            {"value": [{"id": "admin-owner-id"}]},
        ]
        with (
            patch.object(agent365, "load_json", return_value={"agentBlueprintId": "blueprint-client-id"}),
            patch.object(GraphClient, "from_az_cli", return_value=graph),
        ):
            with self.assertRaisesRegex(PermissionError, "operator@example.com.*not a direct owner") as error:
                agent365.require_endpoint_update_owner(Path("worker"))
        self.assertIn("isolated Azure CLI session", str(error.exception))
        self.assertIn("No endpoint was changed", str(error.exception))
        self.assertTrue(all(call.args[0] == "GET" for call in graph.request.call_args_list))

    def test_endpoint_owner_preflight_fails_closed_on_graph_errors(self):
        for replies in (
            [GraphError("GET", "/me", 403, "Forbidden")],
            [{"id": "owner-id"}, {"value": [{"id": "blueprint-object-id"}]},
             GraphError("GET", "/owners", 403, "Forbidden")],
            [{}],
            [{"id": "owner-id"}, {"value": []}],
        ):
            with self.subTest(replies=replies):
                graph = Mock()
                graph.request.side_effect = replies
                with (
                    patch.object(agent365, "load_json", return_value={"agentBlueprintId": "blueprint-client-id"}),
                    patch.object(GraphClient, "from_az_cli", return_value=graph),
                ):
                    with self.assertRaisesRegex(RuntimeError, "unable to verify direct Agent Blueprint ownership") as error:
                        agent365.require_endpoint_update_owner(Path("worker"))
                self.assertIn("a365 update was not invoked", str(error.exception))

    def test_endpoint_owner_preflight_blocks_before_local_config_or_cli_changes(self):
        with (
            patch("sys.argv", ["setup_agent365", "--state-name", "hermes", "--tenant-id", "tenant",
                               "--messaging-endpoint", "https://native.example", "--update-endpoint"]),
            patch.object(agent365.Path, "mkdir"),
            patch.object(agent365, "endpoint_update_config", return_value={"tenantId": "tenant"}),
            patch.object(agent365, "require_endpoint_update_owner", side_effect=PermissionError("not an owner")) as preflight,
            patch.object(agent365, "write_json") as write,
            patch.object(agent365, "maybe_run") as run,
        ):
            with self.assertRaisesRegex(PermissionError, "not an owner"):
                agent365.main()
        preflight.assert_called_once_with(agent365_workspace("hermes"))
        write.assert_not_called()
        run.assert_not_called()

    def test_endpoint_update_runs_only_after_owner_preflight_and_preview_does_not_check(self):
        for update in (False, True):
            with self.subTest(update=update):
                events = []
                argv = ["setup_agent365", "--state-name", "hermes", "--tenant-id", "tenant",
                        "--messaging-endpoint", "https://native.example"]
                if update:
                    argv.append("--update-endpoint")
                with (
                    patch("sys.argv", argv),
                    patch.object(agent365.Path, "mkdir"),
                    patch.object(agent365.Path, "exists", return_value=False),
                    patch.object(agent365, "endpoint_update_config", return_value={"tenantId": "tenant"}),
                    patch.object(agent365, "require_endpoint_update_owner", side_effect=lambda _: events.append("owner")) as preflight,
                    patch.object(agent365, "write_json", side_effect=lambda *_: events.append("write")),
                    patch.object(agent365, "print_command"),
                    patch.object(agent365, "maybe_run", side_effect=lambda command, **kwargs: events.append(command) if kwargs["enabled"] else None),
                ):
                    agent365.main()
                if update:
                    self.assertEqual(events, [
                        "owner", "write",
                        ["a365", "setup", "blueprint", "--update-endpoint", "https://native.example/api/messages"],
                    ])
                else:
                    preflight.assert_not_called()
                    self.assertEqual(events, ["write"])

    def test_endpoint_update_preserves_existing_blueprint_and_agent_metadata(self):
        config = {"tenantId": "tenant", "agentName": "Existing worker", "messagingEndpoint": "https://old.example/api/messages",
                  "agentUserPrincipalName": "worker@example.com", "custom": "preserved"}
        with patch.object(agent365, "load_json", side_effect=[config, {"agentBlueprintId": "blueprint"}]):
            updated = agent365.endpoint_update_config(
                Path("worker"), tenant_id="tenant", messaging_endpoint="https://native-sandbox.example")
        self.assertEqual(updated, {**config, "messagingEndpoint": "https://native-sandbox.example/api/messages"})
        self.assertEqual(config["messagingEndpoint"], "https://old.example/api/messages")

    def test_endpoint_update_refuses_missing_blueprint_or_wrong_tenant(self):
        for config, generated in (({"tenantId": "tenant"}, {}), ({"tenantId": "other"}, {"agentBlueprintId": "blueprint"})):
            with patch.object(agent365, "load_json", side_effect=[config, generated]):
                with self.assertRaises(ValueError):
                    agent365.endpoint_update_config(Path("worker"), tenant_id="tenant", messaging_endpoint="https://native.example")

    def test_dry_run_cannot_accidentally_execute_endpoint_update(self):
        with (
            patch("sys.argv", ["setup_agent365", "--state-name", "hermes", "--dry-run", "--update-endpoint"]),
            patch.object(agent365, "maybe_run") as run,
            patch("sys.stderr"),
        ):
            with self.assertRaises(SystemExit):
                agent365.main()
        run.assert_not_called()

    def test_messaging_endpoint_rejects_non_deployment_urls(self):
        for endpoint in ("http://example.com", "relative", "https://user:password@example.com", "https://example.com?q=x"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                normalize_messaging_endpoint(endpoint)

    def test_missing_worker_endpoint_never_uses_active_terraform_workspace(self):
        with patch("scripts.setup_agent365.Path.exists", return_value=False):
            with self.assertRaisesRegex(FileNotFoundError, "Sandbox services"):
                resolve_messaging_endpoint(
                    runtime_kind="hermes", state_name="worker-one",
                    explicit_endpoint="", outputs_file="",
                )

    def test_federated_credential_is_replaced_when_sandbox_identity_changes(self):
        calls = []

        class Graph:
            def request(self, method, path, body=None):
                calls.append((method, path, body))
                if method == "GET":
                    return {
                        "value": [
                            {
                                "id": "credential-1",
                                "name": "identity-hermes-sandbox",
                                "issuer": "https://login.microsoftonline.com/tenant-1/v2.0",
                                "subject": "old-sandbox-principal",
                            }
                        ]
                    }
                return {}

        ensure_federated_credential(
            Graph(),
            blueprint_object_id="blueprint-object-1",
            tenant_id="tenant-1",
            name="identity-hermes-sandbox",
            managed_identity_principal_id="new-sandbox-principal",
        )

        self.assertEqual(calls[1][0], "DELETE")
        self.assertEqual(calls[2][0], "POST")
        self.assertEqual(calls[2][2]["subject"], "new-sandbox-principal")

    def test_config_payload_marks_external_hosting(self):
        payload = agent365_config_payload(
            autopilot_name="hermes",
            runtime_kind="hermes",
            agent_name="Hermes",
            tenant_id="tenant-1",
            messaging_endpoint="https://bridge.example/api/messages",
            ai_teammate=True,
            manager_email="manager@example.com",
            agent_user_principal_name="hermes@example.com",
        )

        self.assertEqual(payload["agentName"], "Hermes")
        self.assertEqual(payload["autopilotName"], "hermes")
        self.assertEqual(payload["agentRuntime"], "hermes")
        self.assertEqual(payload["agentIdentityDisplayName"], "Hermes Agent")
        self.assertEqual(payload["agentBlueprintDisplayName"], "Hermes Blueprint")
        self.assertEqual(payload["messagingEndpoint"], "https://bridge.example/api/messages")
        self.assertFalse(payload["needDeployment"])
        self.assertEqual(payload["deploymentProjectPath"], ".")
        self.assertTrue(payload["aiteammate"])
        self.assertEqual(payload["managerEmail"], "manager@example.com")
        self.assertEqual(payload["agentUserPrincipalName"], "hermes@example.com")

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
            {"managerEmail": "manager@example.com", "agentUserPrincipalName": "hermes@example.com"},
            {"agentName": "Hermes", "messagingEndpoint": "https://bridge.example/api/messages"},
        )

        self.assertEqual(merged["managerEmail"], "manager@example.com")
        self.assertEqual(merged["agentUserPrincipalName"], "hermes@example.com")
        self.assertEqual(merged["agentName"], "Hermes")

    def test_metadata_includes_portal_links_and_endpoint(self):
        metadata = build_metadata(
            {
                "agentName": "Hermes",
                "autopilotName": "hermes",
                "agentRuntime": "hermes",
                "tenantId": "tenant-1",
                "messagingEndpoint": "https://from-config/api/messages",
            },
            {"agentBlueprintId": "blueprint-1", "messagingEndpoint": "https://from-generated/api/messages"},
        )

        self.assertEqual(metadata["agentName"], "Hermes")
        self.assertEqual(metadata["autopilotName"], "hermes")
        self.assertEqual(metadata["agentRuntime"], "hermes")
        self.assertEqual(metadata["tenantId"], "tenant-1")
        self.assertEqual(metadata["messagingEndpoint"], "https://from-generated/api/messages")
        self.assertEqual(
            metadata["developerPortalConfigurationUrl"],
            "https://dev.teams.microsoft.com/tools/agent-blueprint/blueprint-1/configuration",
        )

    def test_commands_use_existing_bridge_endpoint(self):
        endpoint = "https://bridge.example/api/messages"

        self.assertEqual(
            setup_command(agent_name="Hermes", tenant_id="tenant-1", messaging_endpoint=endpoint, ai_teammate=True, authmode="obo"),
            [
                "a365",
                "setup",
                "all",
                "--agent-name",
                "Hermes",
                "--tenant-id",
                "tenant-1",
                "--aiteammate",
                "--m365",
                "--messaging-endpoint",
                endpoint,
            ],
        )
        self.assertEqual(
            setup_command(agent_name="Hermes", tenant_id="tenant-1", messaging_endpoint=endpoint, ai_teammate=False, authmode="both"),
            [
                "a365",
                "setup",
                "all",
                "--agent-name",
                "Hermes",
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
        self.assertEqual(publish_command(agent_name="Hermes", ai_teammate=True), ["a365", "publish", "--agent-name", "Hermes", "--aiteammate"])
        self.assertEqual(
            publish_command(agent_name="Hermes", ai_teammate=False),
            ["a365", "publish", "--agent-name", "Hermes", "--use-blueprint"],
        )

    def test_setup_command_supports_safe_execution_flags(self):
        self.assertEqual(
            setup_command(
                agent_name="Hermes",
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
                    runtime_kind="hermes",
                    explicit_endpoint="https://explicit.example/api/messages",
                    outputs_file=str(outputs_path),
                ),
                "https://explicit.example/api/messages",
            )

    def test_resolve_messaging_endpoint_uses_worker_state_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs_path = (
                Path(temp_dir)
                / ".local"
                / "hermes2"
                / "apps"
                / "terraform-outputs.json"
            )
            outputs_path.parent.mkdir(parents=True)
            outputs_path.write_text(
                '{"bridge_url":"https://hermes2.example"}',
                encoding="utf-8",
            )
            with patch(
                "scripts.setup_agent365.REPO_ROOT",
                Path(temp_dir),
            ):
                endpoint = resolve_messaging_endpoint(
                    runtime_kind="hermes",
                    state_name="hermes2",
                    explicit_endpoint="",
                    outputs_file="",
                )

        self.assertEqual(
            endpoint,
            "https://hermes2.example/api/messages",
        )

    def test_developer_portal_url_is_empty_without_blueprint_id(self):
        self.assertEqual(developer_portal_url(""), "")

    def test_customize_manifest_updates_branding_and_zip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_dir = Path(temp_dir) / "manifest"
            manifest_dir.mkdir()
            (manifest_dir / "manifest.json").write_text(
                (
                    '{"id":"blueprint-1","name":{"short":"Hermes Blueprint","full":"Hermes Blueprint"},'
                    '"description":{"short":"x","full":"y"},"developer":{},"version":"1.2.3"}'
                ),
                encoding="utf-8",
            )
            (manifest_dir / "color.png").write_bytes(b"color")
            (manifest_dir / "outline.png").write_bytes(b"outline")

            package_path = customize_manifest(Path(temp_dir), default_branding())

            self.assertTrue(package_path.exists())
            manifest = (manifest_dir / "manifest.json").read_text(encoding="utf-8")
            self.assertIn('"short": "Hermes Autopilot"', manifest)
            self.assertIn('"full": "Hermes Autopilot on Azure"', manifest)
            self.assertIn('"version": "1.2.4"', manifest)
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
                (
                    '{"id":"11111111-1111-1111-1111-111111111111",'
                    '"name":{"short":"Old","full":"Old Full"},'
                    '"description":{"short":"x","full":"y"},'
                    '"developer":{},"version":"2.0.0"}'
                ),
                encoding="utf-8",
            )
            (manifest_dir / "color.png").write_bytes(b"color")

            customize_manifest(Path(temp_dir), default_branding("hermes"))

            manifest = (manifest_dir / "manifest.json").read_text(encoding="utf-8")
            self.assertIn('"short": "Hermes Autopilot"', manifest)
            self.assertIn('"full": "Hermes Autopilot on Azure"', manifest)
            self.assertIn('"version": "2.0.1"', manifest)

    def test_missing_tooling_permissions_detects_stale_worker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "agent365"
            workspace.mkdir()
            tooling_manifest = root / "ToolingManifest.json"
            tooling_manifest.write_text(
                (
                    '{"mcpServers":['
                    '{"mcpServerName":"Mail","audience":"mail",'
                    '"scope":"Tools.ListInvoke.All"},'
                    '{"mcpServerName":"Word","audience":"word",'
                    '"scope":"Tools.ListInvoke.All"}]}'
                ),
                encoding="utf-8",
            )
            (workspace / "a365.generated.config.json").write_text(
                (
                    '{"resourceConsents":[{"resourceAppId":"mail",'
                    '"consentGranted":true,'
                    '"inheritablePermissionsConfigured":true,'
                    '"scopes":["Tools.ListInvoke.All"]}]}'
                ),
                encoding="utf-8",
            )

            missing = missing_tooling_permissions(
                workspace,
                tooling_manifest,
            )

        self.assertEqual(missing, ["Word"])

    def test_agentic_package_removes_unsupported_bot_capability(self):
        manifest = {
            "agenticUserTemplates": [
                {"id": "template-1", "file": "agentic.json"}
            ],
            "bots": [
                {
                    "botId": "11111111-1111-1111-1111-111111111111",
                    "supportsTargetedMessages": True,
                }
            ],
        }

        remove_unsupported_agentic_bot_capability(manifest)

        self.assertNotIn("bots", manifest)


if __name__ == "__main__":
    unittest.main()
