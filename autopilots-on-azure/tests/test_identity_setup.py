from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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
