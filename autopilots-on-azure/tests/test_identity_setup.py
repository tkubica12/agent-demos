from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import scripts.register_byo_mcp as byo
import scripts.setup_identity as identity


class FakeGraph:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.paths: list[str] = []

    def request(self, method: str, path: str, **_: object) -> dict:
        self.paths.append(f"{method} {path}")
        return self.payload


class IdentitySetupTests(unittest.TestCase):
    def test_identity_context_rejects_wrong_tenant_or_blueprint_before_graph_writes(self):
        outputs = {"worker_id": "worker-one", "agent_runtime": "hermes", "tenant_id": "tenant",
                   "sandbox_groups": {"gateway": {"identity_principal_id": "gateway"},
                                      "runtime": {"identity_principal_id": "runtime"}}}
        config = {"autopilot_name": "worker-one", "agent365_client_id": "blueprint"}
        identity.validate_worker_identity_context(
            outputs, config, runtime="hermes", tenant_id="tenant", blueprint_client_id="blueprint")
        with self.assertRaisesRegex(ValueError, "CLI tenant"):
            identity.validate_worker_identity_context(
                outputs, config, runtime="hermes", tenant_id="other", blueprint_client_id="blueprint")
        with self.assertRaisesRegex(ValueError, "blueprint IDs differ"):
            identity.validate_worker_identity_context(
                outputs, config, runtime="hermes", tenant_id="tenant", blueprint_client_id="other")

    def test_identity_tfvars_preserve_existing_agent_ids_and_remove_stored_secret(self):
        path = MagicMock()
        path.exists.return_value = True
        state = {"agentIdentityId": "agent-object", "agentIdentityAppId": "agent-client",
                 "agentUserId": "agent-user", "agentUserPrincipalName": "agent@example.com"}
        with (
            patch.object(identity, "runtime_app_tfvars_path", return_value=path),
            patch.object(identity, "load_json", return_value={"agent365_client_secret": "obsolete", "bridge_image": "pinned"}),
            patch.object(identity, "write_tfvars") as write,
        ):
            config = identity.update_runtime_tfvars(
                runtime="hermes", state_name="worker-one", identity_state=state,
                api_state={"audience": "api://private"}, public_api_state={"audience": "api://public"},
                tenant_id="tenant", blueprint_client_id="blueprint",
            )
        self.assertNotIn("agent365_client_secret", config)
        self.assertEqual(config["agent365_client_id"], "blueprint")
        self.assertEqual(config["agent365_agent_identity_client_id"], "agent-client")
        self.assertEqual(config["agent365_agent_user_id"], "agent-user")
        self.assertEqual(config["bridge_image"], "pinned")
        self.assertEqual(write.call_count, 3)

    def test_worker_federation_configures_gateway_and_runtime(self) -> None:
        graph = FakeGraph({})
        outputs = {"sandbox_groups": {
            "gateway": {"identity_principal_id": "worker-gateway"},
            "runtime": {"identity_principal_id": "worker-runtime"},
        }}
        with patch.object(identity, "ensure_federated_credential") as ensure:
            principals = identity.configure_worker_federation(
                graph, apps_outputs=outputs, blueprint_object_id="blueprint",
                tenant_id="tenant", state_name="worker-one",
            )
        self.assertEqual(principals, {"gateway": "worker-gateway", "runtime": "worker-runtime"})
        self.assertEqual([call.kwargs["name"] for call in ensure.call_args_list],
                         ["identity-worker-one-gateway", "identity-worker-one-runtime"])
        self.assertEqual([call.kwargs["managed_identity_principal_id"] for call in ensure.call_args_list],
                         ["worker-gateway", "worker-runtime"])

    def test_worker_federation_rejects_missing_or_shared_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "apps outputs"):
            identity.worker_federation_principals({"sandbox_group_principal_id": "legacy-shared"})
        with self.assertRaisesRegex(ValueError, "distinct"):
            identity.worker_federation_principals({"sandbox_groups": {
                "gateway": {"identity_principal_id": "shared"},
                "runtime": {"identity_principal_id": "shared"},
            }})

    def test_worker_federation_removes_only_its_obsolete_shared_trust(self) -> None:
        graph = FakeGraph({"value": [
            {"id": "obsolete", "name": "identity-worker-one-sandbox"},
            {"id": "other-worker", "name": "identity-worker-two-sandbox"},
        ]})
        with patch.object(identity, "ensure_federated_credential"):
            identity.configure_worker_federation(
                graph, apps_outputs={"sandbox_groups": {
                    "gateway": {"identity_principal_id": "gateway"},
                    "runtime": {"identity_principal_id": "runtime"},
                }}, blueprint_object_id="blueprint", tenant_id="tenant", state_name="worker-one",
            )
        self.assertEqual(graph.paths[-1], "DELETE /applications/blueprint/federatedIdentityCredentials/obsolete")
        self.assertEqual(len(graph.paths), 2)

    def test_named_worker_discovers_its_only_instance_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            expected = workspace / "instance.hermes2.json"
            expected.write_text("{}", encoding="utf-8")
            with patch.object(
                identity,
                "agent365_workspace",
                return_value=workspace,
            ):
                resolved = identity.resolve_instance_state_file(
                    "hermes",
                    "hermes2",
                )

        self.assertEqual(resolved, expected)

    def test_application_discovery_recovers_without_local_state(self) -> None:
        graph = FakeGraph(
            {
                "value": [
                    {
                        "id": "app-object-1",
                        "appId": "app-client-1",
                        "displayName": "Autopilots Private Incidents MCP",
                    }
                ]
            }
        )

        app = identity.application_by_display_name(graph, "Autopilots Private Incidents MCP")

        self.assertEqual(app["id"], "app-object-1")
        self.assertIn("$filter=displayName%20eq%20'Autopilots%20Private%20Incidents%20MCP'", graph.paths[0])

    def test_workiq_permission_detection_skips_reconsent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "agent365"
            workspace.mkdir()
            manifest = root / "ToolingManifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "mcpServers": [
                            {
                                "audience": "mail-resource",
                                "scope": "Tools.ListInvoke.All",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (workspace / "a365.generated.config.json").write_text(
                json.dumps(
                    {
                        "resourceConsents": [
                            {
                                "resourceAppId": "mail-resource",
                                "consentGranted": True,
                                "inheritablePermissionsConfigured": True,
                                "scopes": ["Tools.ListInvoke.All"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch.object(identity, "TOOLING_MANIFEST", manifest),
                patch.object(identity, "agent365_workspace", return_value=workspace),
            ):
                self.assertTrue(identity.workiq_permissions_configured("openclaw"))

    def test_catalog_detection_recovers_approved_byo_registration(self) -> None:
        output = """
          ext_Shipments
             URL: https://agent365.svc.cloud.microsoft/agents/servers/ext_Shipments
        """
        with patch.object(
            byo.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=0, stdout=output),
        ):
            self.assertTrue(byo.catalog_server_available("ext_Shipments"))
            self.assertFalse(byo.catalog_server_available("ext_Other"))

    def test_federated_credential_reuses_matching_subject_with_other_name(self) -> None:
        graph = FakeGraph(
            {
                "value": [
                    {
                        "id": "fic-1",
                        "name": "existing-name",
                        "issuer": "https://login.microsoftonline.com/tenant/v2.0",
                        "subject": "managed-identity",
                        "audiences": ["api://AzureADTokenExchange"],
                    }
                ]
            }
        )

        identity.ensure_federated_credential(
            graph,
            blueprint_object_id="blueprint",
            tenant_id="tenant",
            name="new-name",
            managed_identity_principal_id="managed-identity",
        )

        self.assertEqual(len(graph.paths), 1)
        self.assertTrue(graph.paths[0].startswith("GET "))

    def test_federated_credential_removes_stale_canonical_name(self) -> None:
        graph = FakeGraph(
            {
                "value": [
                    {
                        "id": "stale",
                        "name": "canonical",
                        "issuer": "https://login.microsoftonline.com/tenant/v2.0",
                        "subject": "old-identity",
                        "audiences": ["api://AzureADTokenExchange"],
                    },
                    {
                        "id": "matching",
                        "name": "alternate",
                        "issuer": "https://login.microsoftonline.com/tenant/v2.0",
                        "subject": "managed-identity",
                        "audiences": ["api://AzureADTokenExchange"],
                    },
                ]
            }
        )

        identity.ensure_federated_credential(
            graph,
            blueprint_object_id="blueprint",
            tenant_id="tenant",
            name="canonical",
            managed_identity_principal_id="managed-identity",
        )

        self.assertEqual(len(graph.paths), 2)
        self.assertIn("DELETE", graph.paths[1])
        self.assertIn("stale", graph.paths[1])


if __name__ == "__main__":
    unittest.main()
