import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import scripts.provision_agent365_instance as provision


class ProvisionAgent365InstanceTests(unittest.TestCase):
    def test_missing_license_payload_only_adds_missing_skus(self):
        user = {"assignedLicenses": [{"skuId": "SKU-ALREADY"}]}
        skus = {"AGENT_365": "sku-already", "Microsoft_365_Copilot": "sku-new"}
        self.assertEqual(
            provision.missing_license_payload(user, skus, list(skus)),
            [{"skuId": "sku-new", "disabledPlans": []}],
        )

    def test_missing_license_payload_fails_when_tenant_lacks_sku(self):
        with self.assertRaises(KeyError):
            provision.missing_license_payload({"assignedLicenses": []}, {}, ["AGENT_365"])

    def test_state_file_uses_named_worker_workspace(self):
        self.assertEqual(
            provision.state_file("hermes2", "project-manager"),
            Path.cwd() / ".local" / "hermes2" / "agent365" / "instance.project-manager.json",
        )

    def test_parse_csv_strips_empty_values(self):
        self.assertEqual(provision.parse_csv("A, B,,C "), ["A", "B", "C"])

    def test_agent_identity_reuses_recorded_identity(self):
        graph = Mock()
        graph.request.return_value = {"id": "identity-id", "appId": "identity-app-id"}
        actual = provision.ensure_agent_identity(
            graph, state={"agentIdentityId": "identity-id"}, display_name="Worker",
            blueprint_id="blueprint", sponsor_user_id="sponsor",
        )
        self.assertEqual(actual["appId"], "identity-app-id")
        graph.request.assert_called_once_with(
            "GET", "/servicePrincipals/identity-id?$select=id,appId,displayName",
        )

    def test_new_identity_and_user_keep_real_parent_and_sponsor(self):
        graph = Mock()
        graph.request.side_effect = [{"id": "identity-id"}, {"id": "user-id"}]
        provision.ensure_agent_identity(
            graph, state={}, display_name="Worker", blueprint_id="blueprint", sponsor_user_id="sponsor",
        )
        provision.ensure_agent_user(
            graph, state={}, display_name="Worker", mail_nickname="worker",
            user_principal_name="worker@example.com", agent_identity_id="identity-id",
        )
        identity_call, user_call = graph.request.call_args_list
        self.assertEqual(identity_call.args, ("POST", "/servicePrincipals/microsoft.graph.agentIdentity"))
        self.assertEqual(identity_call.kwargs["body"]["agentIdentityBlueprintId"], "blueprint")
        self.assertEqual(identity_call.kwargs["body"]["sponsors@odata.bind"],
                         ["https://graph.microsoft.com/v1.0/users/sponsor"])
        self.assertEqual(user_call.args, ("POST", "/users/microsoft.graph.agentUser"))
        self.assertEqual(user_call.kwargs["body"]["identityParentId"], "identity-id")
        self.assertNotIn("passwordProfile", user_call.kwargs["body"])

    def test_agent_user_reuses_recorded_user(self):
        graph = Mock()
        graph.request.return_value = {"id": "user-id", "userPrincipalName": "worker@example.com"}
        actual = provision.ensure_agent_user(
            graph, state={"agentUserId": "user-id"}, display_name="Worker", mail_nickname="worker",
            user_principal_name="worker@example.com", agent_identity_id="identity-id",
        )
        self.assertEqual(actual["id"], "user-id")
        self.assertEqual(graph.request.call_args.args[0], "GET")
        self.assertTrue(graph.request.call_args.args[1].startswith("/users/user-id?"))

    def test_graph_dry_run_never_sends_write_requests(self):
        with patch.object(provision.urllib.request, "urlopen") as send:
            result = provision.GraphClient("test-token", dry_run=True).request(
                "POST", "/servicePrincipals/microsoft.graph.agentIdentity", body={"displayName": "Worker"},
            )
        self.assertEqual(result, {})
        send.assert_not_called()

    def test_provision_dry_run_never_saves_fake_instance_ids(self):
        for state, identity in (({}, {}), ({"agentIdentityId": "existing"}, {"id": "existing"})):
            with self.subTest(state=state):
                args = SimpleNamespace(
                    state_file="", state_name="hermes2", mail_nickname="worker",
                    dry_run=True, owner_id="sponsor", owner_upn="", agent_blueprint_id="blueprint",
                    identity_display_name="", display_name="Worker", agent_upn="worker@example.com",
                )
                with (
                    patch.object(provision, "load_state", return_value=dict(state)),
                    patch.object(provision.GraphClient, "from_az_cli"),
                    patch.object(provision, "ensure_agent_identity", return_value=identity),
                    patch.object(provision, "ensure_agent_user", return_value={}) as user,
                    patch.object(provision, "save_state") as save,
                    patch.object(provision, "update_usage_location") as location,
                ):
                    provision.provision_command(args)
                save.assert_not_called()
                location.assert_not_called()
                self.assertEqual(user.call_count, int(bool(identity)))

    def test_cleanup_deletes_only_recorded_instance_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instance.json"
            path.write_text(json.dumps({
                "agentIdentityId": "identity-id", "agentUserId": "user-id",
                "agentBlueprintId": "shared-blueprint",
            }), encoding="utf-8")
            graph = Mock()
            args = SimpleNamespace(state_file=str(path), dry_run=False, remove_state=True)
            with patch.object(provision.GraphClient, "from_az_cli", return_value=graph):
                provision.cleanup_command(args)
            self.assertEqual([call.args[:2] for call in graph.request.call_args_list], [
                ("DELETE", "/users/user-id"), ("DELETE", "/servicePrincipals/identity-id"),
            ])
            self.assertFalse(path.exists())

    def test_cleanup_dry_run_preserves_instance_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instance.json"
            path.write_text('{"agentIdentityId":"identity-id"}', encoding="utf-8")
            args = SimpleNamespace(state_file=str(path), dry_run=True, remove_state=True)
            with (
                patch.object(provision.GraphClient, "from_az_cli",
                             return_value=provision.GraphClient("test-token", dry_run=True)),
                patch.object(provision.urllib.request, "urlopen") as send,
            ):
                provision.cleanup_command(args)
            self.assertTrue(path.exists())
            send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
