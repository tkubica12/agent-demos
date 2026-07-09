import unittest

from scripts.provision_agent365_instance import (
    agent_registration_payload,
    catalog_app_summary,
    graph_app_role_ids,
    graph_oauth_scope_ids,
    missing_license_payload,
    package_summary,
    parse_csv,
    registration_summary,
    state_file,
    teams_app_filter,
)


class ProvisionAgent365InstanceTests(unittest.TestCase):
    def test_graph_app_role_ids_selects_application_roles(self):
        roles = {
            "appRoles": [
                {"value": "AgentRegistration.ReadWrite.All", "id": "role-1", "allowedMemberTypes": ["Application"]},
                {"value": "AgentRegistration.ReadWrite.All", "id": "delegated", "allowedMemberTypes": ["User"]},
            ]
        }

        self.assertEqual(
            graph_app_role_ids(roles, ["AgentRegistration.ReadWrite.All"]),
            {"AgentRegistration.ReadWrite.All": "role-1"},
        )

    def test_graph_app_role_ids_fails_for_missing_role(self):
        with self.assertRaises(KeyError):
            graph_app_role_ids({"appRoles": []}, ["AgentRegistration.ReadWrite.All"])

    def test_graph_oauth_scope_ids_selects_enabled_delegated_scope(self):
        scopes = {
            "oauth2PermissionScopes": [
                {"value": "AppCatalog.ReadWrite.All", "id": "scope-1", "isEnabled": True},
                {"value": "Disabled.Scope", "id": "scope-2", "isEnabled": False},
            ]
        }

        self.assertEqual(
            graph_oauth_scope_ids(scopes, ["AppCatalog.ReadWrite.All"]),
            {"AppCatalog.ReadWrite.All": "scope-1"},
        )

    def test_graph_oauth_scope_ids_fails_for_disabled_scope(self):
        with self.assertRaises(KeyError):
            graph_oauth_scope_ids(
                {"oauth2PermissionScopes": [{"value": "AppCatalog.ReadWrite.All", "id": "scope-1", "isEnabled": False}]},
                ["AppCatalog.ReadWrite.All"],
            )

    def test_missing_license_payload_only_adds_missing_skus(self):
        user = {"assignedLicenses": [{"skuId": "sku-already"}]}
        skus = {"AGENT_365": "sku-already", "Microsoft_365_Copilot": "sku-new"}

        self.assertEqual(
            missing_license_payload(user, skus, ["AGENT_365", "Microsoft_365_Copilot"]),
            [{"skuId": "sku-new", "disabledPlans": []}],
        )

    def test_missing_license_payload_fails_when_tenant_lacks_sku(self):
        with self.assertRaises(KeyError):
            missing_license_payload({"assignedLicenses": []}, {}, ["AGENT_365"])

    def test_agent_registration_payload_contains_required_graph_fields(self):
        payload = agent_registration_payload(
            display_name="hermes1",
            description="Hermes Autopilot",
            owner_id_value="owner-1",
            agent_upn="hermes1@example.com",
            agent_identity_id="identity-1",
            blueprint_id="blueprint-1",
        )

        self.assertEqual(payload["displayName"], "hermes1")
        self.assertEqual(payload["createdBy"], "owner-1")
        self.assertEqual(payload["ownerIds"], ["owner-1"])
        self.assertEqual(payload["sourceAgentId"], "hermes1@example.com")
        self.assertEqual(payload["agentIdentityId"], "identity-1")
        self.assertEqual(payload["agentIdentityBlueprintId"], "blueprint-1")
        self.assertEqual(payload["agentCard"]["provider"]["organization"], "Autopilots on Azure")
        self.assertEqual(payload["agentCard"]["skills"][0]["id"], "chat")
        self.assertTrue(payload["sourceCreatedDateTime"].endswith("Z"))

    def test_state_file_uses_runtime_workspace(self):
        self.assertTrue(str(state_file("hermes", "hermes1")).endswith(".local\\hermes\\agent365\\instance.hermes1.json"))

    def test_parse_csv_strips_empty_values(self):
        self.assertEqual(parse_csv("A, B,,C "), ["A", "B", "C"])

    def test_teams_app_filter_escapes_quotes(self):
        self.assertEqual(teams_app_filter("Tom's Agent"), "displayName eq 'Tom''s Agent'")

    def test_catalog_app_summary_uses_latest_definition(self):
        summary = catalog_app_summary(
            {
                "id": "teams-app-1",
                "displayName": "Hermes Autopilot",
                "externalId": "external-1",
                "distributionMethod": "organization",
                "appDefinitions": [{"id": "definition-1", "publishingState": "rejected", "version": "1.0.0"}],
            }
        )

        self.assertEqual(summary["id"], "teams-app-1")
        self.assertEqual(summary["displayName"], "Hermes Autopilot")
        self.assertEqual(summary["publishingState"], "rejected")
        self.assertEqual(summary["teamsAppDefinitionId"], "definition-1")

    def test_registration_summary_keeps_deletion_fields(self):
        summary = registration_summary(
            {
                "id": "registration-1",
                "displayName": "Hermes Autopilot",
                "agentIdentityId": "identity-1",
                "agentIdentityBlueprintId": "blueprint-1",
                "sourceAgentId": "source-1",
                "originatingStore": "store-1",
            }
        )

        self.assertEqual(summary["id"], "registration-1")
        self.assertEqual(summary["displayName"], "Hermes Autopilot")
        self.assertEqual(summary["agentIdentityBlueprintId"], "blueprint-1")

    def test_package_summary_keeps_block_fields(self):
        summary = package_summary(
            {
                "id": "package-1",
                "displayName": "hermes-foundry-a365dev",
                "isBlocked": True,
                "supportedHosts": ["Copilot"],
                "manifestId": "manifest-1",
            }
        )

        self.assertEqual(summary["id"], "package-1")
        self.assertEqual(summary["displayName"], "hermes-foundry-a365dev")
        self.assertTrue(summary["isBlocked"])
        self.assertEqual(summary["supportedHosts"], ["Copilot"])


if __name__ == "__main__":
    unittest.main()
