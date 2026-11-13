from __future__ import annotations

import copy
import json
import shutil
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import httpx

from scripts import simulator_identity as identity


def guid(label: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"simulator-test:{label}"))


def graph_error(status: int, method: str, path: str) -> httpx.HTTPStatusError:
    request = httpx.Request(method, identity.GRAPH + path)
    response = httpx.Response(status, request=request, json={"error": {"code": "Authorization_RequestDenied"}})
    return httpx.HTTPStatusError("Graph request failed", request=request, response=response)


class FakeGraph:
    def __init__(self) -> None:
        self.apps: dict[str, dict] = {}
        self.principals: dict[str, dict] = {}
        self.owners: dict[str, list[dict]] = {}
        self.fics: dict[str, list[dict]] = {}
        self.assignments: dict[str, list[dict]] = {}
        self.calls: list[dict] = []
        self.fail = None
        self.counter = 0

    def __enter__(self) -> FakeGraph:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def new_id(self) -> str:
        self.counter += 1
        return guid(f"graph:{self.counter}")

    def request(self, method: str, path: str, *, body: dict | None = None) -> dict:
        self.calls.append({"method": method, "path": path, "body": copy.deepcopy(body)})
        if self.fail:
            status = self.fail(method, path, body)
            if status:
                raise graph_error(status, method, path)
        uri = urlsplit(path)
        parts = uri.path.strip("/").split("/")
        if parts[0] == "organization" and method == "GET":
            return {"value": [{"id": guid("tenant")}]}
        if parts[0] in {"users", "groups"} and method == "GET":
            return {"id": parts[1]}
        resources = self.apps if parts[0] == "applications" else self.principals
        if len(parts) == 1:
            if method == "GET":
                expression = parse_qs(uri.query).get("$filter", [""])[0]
                matches = list(resources.values())
                if expression.startswith("displayName eq "):
                    name = expression.split("'", 1)[1][:-1].replace("''", "'")
                    matches = [item for item in matches if item["displayName"] == name]
                elif expression.startswith("appId eq "):
                    app_id = expression.split("'")[1]
                    matches = [item for item in matches if item["appId"] == app_id]
                elif expression.startswith("tags/any"):
                    tag = expression.split("'")[1]
                    matches = [item for item in matches if tag in item.get("tags", [])]
                return {"value": copy.deepcopy(matches)}
            if method == "POST":
                item = {"id": self.new_id(), **copy.deepcopy(body)}
                if parts[0] == "applications":
                    item["appId"] = self.new_id()
                else:
                    app = next(app for app in self.apps.values() if app["appId"] == body["appId"])
                    item.update(displayName=app["displayName"], appOwnerOrganizationId=guid("tenant"))
                resources[item["id"]] = item
                return copy.deepcopy(item)
        if len(parts) == 2:
            item = resources.get(parts[1])
            if item is None:
                raise graph_error(404, method, path)
            if method == "GET":
                return copy.deepcopy(item)
            if method == "PATCH":
                for key, value in copy.deepcopy(body).items():
                    if isinstance(value, dict) and isinstance(item.get(key), dict):
                        item[key].update(value)
                    else:
                        item[key] = value
                return {}
            if method == "DELETE":
                del resources[parts[1]]
                return {}
        if len(parts) >= 3:
            collection = {"owners": self.owners, "federatedIdentityCredentials": self.fics,
                          "appRoleAssignedTo": self.assignments}[parts[2]]
            items = collection.setdefault(parts[1], [])
            if method == "GET":
                return {"value": copy.deepcopy(items)}
            if method == "POST":
                if parts[2] == "owners":
                    items.append({"id": body["@odata.id"].rsplit("/", 1)[1]})
                    return {}
                item = {"id": self.new_id(), **copy.deepcopy(body)}
                items.append(item)
                return copy.deepcopy(item)
            if method == "DELETE":
                item = next((item for item in items if item["id"] == parts[3]), None)
                if item is None:
                    raise graph_error(404, method, path)
                items.remove(item)
                return {}
        raise AssertionError(f"Unhandled fake Graph request: {method} {path}")


class SimulatorIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path(__file__).parent / f".simulator-identity-tests-{uuid.uuid4().hex}"
        self.workspace.mkdir()
        self.addCleanup(shutil.rmtree, self.workspace)
        self.path = self.workspace / "identities.json"
        self.config = {
            "environment": "classroom", "tenant_id": guid("tenant"), "subscription_id": guid("subscription"),
            "owner_id": guid("owner"),
            "agents": [{"name": "worker-one", "display_name": "Worker One"}],
            "assignments": [
                {"principal_id": guid("user"), "principal_type": "User", "role": "User"},
                {"principal_id": guid("group"), "principal_type": "Group", "role": "Admin"},
            ],
        }
        self.bindings = {
            "channel": {"identity_client_id": guid("channel-client"), "identity_principal_id": guid("channel-mi")},
            "agents": {"worker-one": {"identity_client_id": guid("worker-client"),
                                      "identity_principal_id": guid("worker-mi"),
                                      "runtime_identity_principal_id": guid("runtime-mi")}},
        }
        self.graph = FakeGraph()
        for label, client in (("channel-mi", "channel-client"), ("worker-mi", "worker-client"),
                              ("runtime-mi", "runtime-client")):
            self.graph.principals[guid(label)] = {
                "id": guid(label), "appId": guid(client), "servicePrincipalType": "ManagedIdentity",
            }
        self.graph_factory = patch.object(identity, "GraphClient", return_value=self.graph).start()
        self.addCleanup(patch.stopall)

    def reconcile(self, **kwargs: str) -> dict:
        return identity.reconcile_identities(self.config, self.bindings, self.path, **kwargs)

    def manifest(self) -> dict:
        return json.loads(self.path.with_name("identities.admin-request.json").read_text(encoding="utf-8"))

    def test_idempotent_creation_and_recovery_without_local_state(self) -> None:
        first = self.reconcile(redirect_uri="https://simulator.example/auth")
        self.assertEqual(len(self.graph.apps), 3)
        self.assertEqual(set(first), {"spa", "channel", "agents"})
        for record in (first["spa"], first["channel"], first["agents"]["worker-one"]):
            self.assertEqual(set(record), {"client_id", "object_id", "service_principal_id"})
        before = len(self.graph.calls)
        self.assertEqual(self.reconcile(), first)
        self.assertTrue(all(call["method"] == "GET" for call in self.graph.calls[before:]))
        self.path.unlink()
        before = len(self.graph.calls)
        self.assertEqual(self.reconcile(), first)
        self.assertTrue(all(call["method"] == "GET" for call in self.graph.calls[before:]))
        state = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(set(state), {"environment", "tenant_id", "subscription_id", "spa", "channel", "agents"})
        self.assertEqual({key: state[key] for key in ("spa", "channel", "agents")}, first)
        self.assertNotIn("bindings", state)
        self.assertNotIn("owner_id", state)
        self.assertNotIn("assignments", state)
        self.assertEqual(state["spa"], first["spa"])
        self.assertEqual(self.graph_factory.call_args.args, (guid("tenant"), guid("subscription")))

    def test_spa_scope_roles_redirect_and_owner_are_configured_without_secrets(self) -> None:
        result = self.reconcile(redirect_uri="http://localhost:3000/auth")
        app = self.graph.apps[result["spa"]["object_id"]]
        self.assertEqual(app["signInAudience"], "AzureADMyOrg")
        self.assertEqual(app["identifierUris"], [f"api://{app['appId']}"])
        self.assertEqual(app["spa"]["redirectUris"], ["http://localhost:3000/auth"])
        self.assertEqual(app["api"]["requestedAccessTokenVersion"], 2)
        scope, = app["api"]["oauth2PermissionScopes"]
        self.assertEqual((scope["value"], scope["type"], scope["isEnabled"]), ("access_as_user", "User", True))
        roles = {role["value"]: role for role in app["appRoles"]}
        self.assertEqual(set(roles), {"User", "Admin"})
        self.assertTrue(all(role["allowedMemberTypes"] == ["User"] for role in roles.values()))
        expected_scope = str(uuid.uuid5(identity._namespace(identity._context(self.config)), "access_as_user"))
        self.assertEqual(scope["id"], expected_scope)
        self.assertEqual(app["requiredResourceAccess"], [{
            "resourceAppId": app["appId"], "resourceAccess": [{"id": scope["id"], "type": "Scope"}],
        }])
        self.assertEqual(app["api"]["preAuthorizedApplications"], [
            {"appId": app["appId"], "delegatedPermissionIds": [scope["id"]]},
        ])
        app_patches = [call["body"] for call in self.graph.calls
                       if call["method"] == "PATCH" and call["path"] == f"/applications/{app['id']}"]
        self.assertIn("oauth2PermissionScopes", app_patches[0]["api"])
        self.assertNotIn("preAuthorizedApplications", app_patches[0]["api"])
        self.assertNotIn("requiredResourceAccess", app_patches[0])
        self.assertIn("preAuthorizedApplications", app_patches[1]["api"])
        self.assertIn("requiredResourceAccess", app_patches[1])
        self.assertTrue(self.graph.principals[result["spa"]["service_principal_id"]]["appRoleAssignmentRequired"])
        for record in (result["spa"], result["channel"], result["agents"]["worker-one"]):
            for key in ("object_id", "service_principal_id"):
                self.assertEqual(self.graph.owners[record[key]], [{"id": guid("owner")}])
        payloads = json.dumps(self.graph.calls)
        for forbidden in ("passwordCredentials", "keyCredentials", "addPassword", "agentIdentity", "Agent365"):
            self.assertNotIn(forbidden, payloads)
        self.reconcile(redirect_uri="https://simulator.example/new")
        self.assertEqual(self.graph.apps[app["id"]]["spa"]["redirectUris"], ["https://simulator.example/new"])

    def test_graph_readonly_fields_and_collection_order_do_not_cause_updates(self) -> None:
        result = self.reconcile()
        app = self.graph.apps[result["spa"]["object_id"]]
        app["api"].update(acceptMappedClaims=None, knownClientApplications=[])
        for role in app["appRoles"]:
            role["origin"] = "Application"
        app["appRoles"].reverse()
        before = len(self.graph.calls)
        self.reconcile()
        self.assertTrue(all(call["method"] == "GET" for call in self.graph.calls[before:]))

    def test_federation_uses_principal_not_client_and_distinct_gateway_runtime_names(self) -> None:
        result = self.reconcile()
        channel, = self.graph.fics[result["channel"]["object_id"]]
        self.assertEqual(channel["name"], "simulator-channel-gateway")
        self.assertEqual(channel["subject"], guid("channel-mi"))
        worker = self.graph.fics[result["agents"]["worker-one"]["object_id"]]
        self.assertEqual({fic["name"]: fic["subject"] for fic in worker}, {
            "simulator-worker-one-gateway": guid("worker-mi"),
            "simulator-worker-one-runtime": guid("runtime-mi"),
        })
        for fic in [channel, *worker]:
            self.assertEqual(fic["issuer"], f"https://login.microsoftonline.com/{guid('tenant')}/v2.0")
            self.assertEqual(fic["audiences"], ["api://AzureADTokenExchange"])

    def test_federation_repairs_binding_and_removes_only_obsolete_canonical_runtime(self) -> None:
        result = self.reconcile()
        worker_id = result["agents"]["worker-one"]["object_id"]
        unrelated = {"id": guid("unrelated-fic"), "name": "unrelated", "subject": guid("unrelated-mi"),
                     "issuer": "https://issuer.example", "audiences": [identity.TOKEN_EXCHANGE]}
        self.graph.fics[worker_id].append(unrelated)
        self.graph.principals[guid("new-mi")] = {"id": guid("new-mi"), "appId": guid("new-client"),
                                                "servicePrincipalType": "ManagedIdentity"}
        self.bindings["agents"]["worker-one"] = {
            "identity_client_id": guid("new-client"), "identity_principal_id": guid("new-mi"),
        }
        self.reconcile()
        self.assertEqual(self.graph.fics[worker_id], [
            unrelated,
            next(fic for fic in self.graph.fics[worker_id] if fic["name"] == "simulator-worker-one-gateway"),
        ])
        self.assertEqual(self.graph.fics[worker_id][1]["subject"], guid("new-mi"))

    def test_role_assignments_resolve_types_and_reconcile_removed_access(self) -> None:
        result = self.reconcile()
        spa = result["spa"]
        roles = {role["value"]: role["id"] for role in self.graph.apps[spa["object_id"]]["appRoles"]}
        assignments = self.graph.assignments[spa["service_principal_id"]]
        self.assertEqual({(item["principalId"], item["appRoleId"]) for item in assignments},
                         {(guid("user"), roles["User"]), (guid("group"), roles["Admin"])})
        self.assertTrue(all(item["resourceId"] == spa["service_principal_id"] for item in assignments))
        paths = [call["path"] for call in self.graph.calls]
        self.assertIn(f"/users/{guid('user')}?$select=id", paths)
        self.assertIn(f"/groups/{guid('group')}?$select=id", paths)
        self.config["assignments"] = [{"principal_id": guid("user"), "principal_type": "User", "role": "Admin"}]
        self.reconcile()
        self.assertEqual([(item["principalId"], item["appRoleId"]) for item in assignments],
                         [(guid("user"), roles["Admin"])])

    def test_omitted_assignments_grant_owner_admin_and_explicit_empty_revokes_it(self) -> None:
        del self.config["assignments"]
        result = self.reconcile()
        spa = result["spa"]
        roles = {role["value"]: role["id"] for role in self.graph.apps[spa["object_id"]]["appRoles"]}
        assignments = self.graph.assignments[spa["service_principal_id"]]
        self.assertEqual([(item["principalId"], item["appRoleId"]) for item in assignments],
                         [(guid("owner"), roles["Admin"])])
        before = len(self.graph.calls)
        self.reconcile(redirect_uri="https://native-host.example/auth")
        self.assertFalse(any(call["method"] == "POST" for call in self.graph.calls[before:]))
        self.assertEqual(self.graph.apps[spa["object_id"]]["spa"]["redirectUris"], ["https://native-host.example/auth"])
        self.config["assignments"] = []
        self.reconcile()
        self.assertEqual(assignments, [])

    def test_self_preauthorization_permissions_failure_has_exact_request_and_can_resume(self) -> None:
        del self.config["assignments"]
        self.graph.fail = lambda method, path, body: (
            403 if method == "PATCH" and "preAuthorizedApplications" in (body or {}).get("api", {}) else None)
        with self.assertRaises(identity.GraphPermissionsError):
            self.reconcile()
        state = json.loads(self.path.read_text(encoding="utf-8"))
        app = self.graph.apps[state["spa"]["object_id"]]
        self.assertIn("oauth2PermissionScopes", app["api"])
        manifest = self.manifest()
        self.assertEqual(manifest["effective_assignments"], [
            {"principal_id": guid("owner"), "principal_type": "User", "role": "Admin"},
        ])
        self.assertEqual(manifest["failed_graph_call"]["body"], self.graph.calls[-1]["body"])
        self.assertTrue(any("preAuthorizedApplications" in call.get("body", {}).get("api", {})
                            for call in manifest["proposed_graph_calls"]))
        self.graph.fail = None
        self.reconcile()
        self.assertEqual(len(self.graph.apps), 3)

    def test_self_preauthorization_repair_preserves_other_authorized_clients(self) -> None:
        result = self.reconcile()
        app = self.graph.apps[result["spa"]["object_id"]]
        other = {"appId": guid("other-client"), "delegatedPermissionIds": [app["api"]["oauth2PermissionScopes"][0]["id"]]}
        app["api"]["preAuthorizedApplications"] = [
            other, {"appId": app["appId"], "delegatedPermissionIds": []},
        ]
        before = len(self.graph.calls)
        self.reconcile()
        authorized = app["api"]["preAuthorizedApplications"]
        self.assertEqual(authorized[0], other)
        self.assertEqual(authorized[1]["delegatedPermissionIds"], [app["api"]["oauth2PermissionScopes"][0]["id"]])
        writes = [call for call in self.graph.calls[before:] if call["method"] != "GET"]
        self.assertEqual([(call["method"], call["path"]) for call in writes], [("PATCH", f"/applications/{app['id']}")])

    def test_rejects_foreign_display_name_before_any_graph_writes(self) -> None:
        app_id = guid("foreign")
        self.graph.apps[app_id] = {"id": app_id, "appId": guid("foreign-client"), "displayName": "Autopilots Simulator classroom",
                                   "tags": [], "signInAudience": "AzureADMyOrg"}
        with self.assertRaisesRegex(ValueError, "foreign ownership"):
            self.reconcile()
        self.assertTrue(all(call["method"] == "GET" for call in self.graph.calls))
        self.assertFalse(self.path.exists())

    def test_rejects_ambiguous_owned_applications(self) -> None:
        for label in ("duplicate-one", "duplicate-two"):
            self.graph.apps[guid(label)] = {
                "id": guid(label), "appId": guid(label + "-client"), "displayName": "Autopilots Simulator classroom",
                "signInAudience": "AzureADMyOrg", "tags": identity._tags(identity._context(self.config), "spa"),
            }
        with self.assertRaisesRegex(ValueError, "Ambiguous application"):
            self.reconcile()
        self.assertTrue(all(call["method"] == "GET" for call in self.graph.calls))

    def test_rejects_foreign_service_principal(self) -> None:
        result = self.reconcile()
        self.graph.principals[result["spa"]["service_principal_id"]]["tags"] = []
        with self.assertRaisesRegex(ValueError, "foreign ownership"):
            self.reconcile()

    def test_tampered_state_and_other_context_are_rejected(self) -> None:
        result = self.reconcile()
        original = self.path.read_text(encoding="utf-8")
        other = copy.deepcopy(self.config)
        for key in ("environment", "tenant_id", "subscription_id"):
            other[key] = "another" if key == "environment" else guid("another")
            with self.assertRaisesRegex(ValueError, "another environment, tenant or subscription"):
                identity.reconcile_identities(other, self.bindings, self.path)
            other[key] = self.config[key]
        self.graph.apps[result["spa"]["object_id"]]["tags"] = []
        with self.assertRaisesRegex(ValueError, "foreign ownership"):
            self.reconcile()
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)

    def test_managed_identity_type_client_mismatch_and_shared_runtime_are_rejected(self) -> None:
        principal = self.graph.principals[guid("worker-mi")]
        for changes, error in (({"servicePrincipalType": "Application"}, "not a managed identity"),
                               ({"appId": guid("wrong-client")}, "does not match")):
            original = copy.deepcopy(principal)
            principal.update(changes)
            with self.assertRaisesRegex(ValueError, error):
                self.reconcile()
            principal.clear()
            principal.update(original)
        self.bindings["agents"]["worker-one"]["runtime_identity_principal_id"] = guid("worker-mi")
        with self.assertRaisesRegex(ValueError, "must be distinct"):
            self.reconcile()
        self.assertTrue(all(call["method"] == "GET" for call in self.graph.calls))

    def test_input_validation_happens_before_graph_access(self) -> None:
        self.config["assignments"][0]["principal_id"] = "someone@example.com"
        with self.assertRaisesRegex(ValueError, "object ID.*named principals"):
            self.reconcile()
        self.graph_factory.assert_not_called()
        self.config["assignments"][0]["principal_id"] = guid("user")
        for uri in ("http://simulator.example/auth", "https://simulator.example/#token", "javascript:alert(1)"):
            with self.assertRaisesRegex(ValueError, "redirect_uri"):
                self.reconcile(redirect_uri=uri)
        self.graph_factory.assert_not_called()
        self.config["agents"][0]["display_name"] = "Channel"
        with self.assertRaisesRegex(ValueError, "display names must be distinct"):
            self.reconcile()
        self.graph_factory.assert_not_called()

    def test_actual_graph_tenant_is_checked_before_creation_or_destroy(self) -> None:
        self.reconcile()
        self.config["tenant_id"] = guid("foreign-tenant")
        self.path.unlink()
        with self.assertRaisesRegex(ValueError, "Graph tenant does not match"):
            self.reconcile()
        state = identity._context(self.config)
        self.path.write_text(json.dumps({**state, "agents": {}}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Graph tenant does not match"):
            identity.destroy_identities(self.config, self.path)
        self.assertEqual(len(self.graph.apps), 3)

    def test_persists_app_immediately_and_emits_full_admin_manifest_then_resumes(self) -> None:
        self.graph.fail = lambda method, path, body: 403 if method == "PATCH" and path.startswith("/applications/") else None
        with self.assertRaisesRegex(identity.GraphPermissionsError, "HTTP 403"):
            self.reconcile(redirect_uri="https://simulator.example/auth")
        state = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn(state["spa"]["object_id"], self.graph.apps)
        self.assertNotIn("service_principal_id", state["spa"])
        manifest = self.manifest()
        self.assertEqual(manifest["inputs"], self.config)
        self.assertEqual(manifest["bindings"], self.bindings)
        self.assertEqual(manifest["failed_graph_call"]["method"], "PATCH")
        self.assertEqual(manifest["failed_graph_call"]["body"], self.graph.calls[-1]["body"])
        calls = manifest["proposed_graph_calls"]
        self.assertEqual(sum(call["method"] == "POST" and call["path"] == "/applications" for call in calls), 3)
        self.assertEqual(sum("federatedIdentityCredentials" in call["path"] and call["method"] == "POST" for call in calls), 3)
        self.assertTrue(any("appRoleAssignedTo" in call["path"] and call["method"] == "POST" for call in calls))
        self.graph.fail = None
        result = self.reconcile(redirect_uri="https://simulator.example/auth")
        self.assertEqual(result["spa"]["object_id"], state["spa"]["object_id"])
        self.assertEqual(len(self.graph.apps), 3)
        self.assertFalse(self.path.with_name("identities.admin-request.json").exists())

    def test_403_on_first_read_still_emits_plan_without_claiming_success(self) -> None:
        self.graph.fail = lambda *_: 403
        with self.assertRaises(identity.GraphPermissionsError):
            self.reconcile()
        self.assertFalse(self.path.exists())
        manifest = self.manifest()
        self.assertEqual(manifest["operation"], "reconcile")
        self.assertEqual(len(manifest["attempted_graph_calls"]), 1)
        self.assertTrue(any("${spa.object_id}" in call["path"] for call in manifest["proposed_graph_calls"]))

    def test_persists_service_principal_before_owner_failure(self) -> None:
        self.graph.fail = lambda method, path, body: (
            403 if method == "GET" and path.startswith("/servicePrincipals/") and "/owners" in path else None)
        with self.assertRaises(identity.GraphPermissionsError):
            self.reconcile()
        state = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn(state["spa"]["service_principal_id"], self.graph.principals)
        self.graph.fail = None
        self.reconcile()
        self.assertEqual(len(self.graph.apps), 3)
        self.assertEqual(len(self.graph.principals), 6)

    def test_nonpermissions_failure_is_explicit_and_partial_creation_is_recoverable(self) -> None:
        self.graph.fail = lambda method, path, body: 500 if method == "PATCH" else None
        with self.assertRaises(httpx.HTTPStatusError):
            self.reconcile()
        self.assertTrue(self.path.exists())
        self.assertFalse(self.path.with_name("identities.admin-request.json").exists())
        self.graph.fail = None
        self.reconcile()
        self.assertEqual(len(self.graph.apps), 3)

    def test_new_app_and_sp_owner_and_fic_readiness_retries_without_duplicate_creation(self) -> None:
        remaining = {}

        def delayed_readiness(method: str, path: str, body: dict | None) -> int | None:
            if method in {"GET", "POST"} and ("/owners" in path or "/federatedIdentityCredentials" in path):
                key = (method, path)
                remaining.setdefault(key, 2)
                if remaining[key]:
                    state = json.loads(self.path.read_text(encoding="utf-8"))
                    persisted_ids = {record[field] for _, record in identity._records(state)
                                     for field in ("object_id", "service_principal_id") if field in record}
                    self.assertIn(path.split("/")[2], persisted_ids)
                    remaining[key] -= 1
                    return 404
            return None

        self.graph.fail = delayed_readiness
        with patch.object(identity.time, "sleep") as sleep:
            result = self.reconcile()
        self.assertEqual(len(self.graph.apps), 3)
        self.assertEqual(len(self.graph.principals), 6)
        self.assertEqual(sum(call["method"] == "POST" and call["path"] == "/applications"
                             for call in self.graph.calls), 3)
        self.assertEqual(sum(call["method"] == "POST" and call["path"] == "/servicePrincipals"
                             for call in self.graph.calls), 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2] * len(remaining))
        self.assertEqual(len(self.graph.fics[result["agents"]["worker-one"]["object_id"]]), 2)

    def test_readiness_exhaustion_preserves_partial_app_and_resume_does_not_duplicate_it(self) -> None:
        self.graph.fail = lambda method, path, body: 404 if method == "GET" and "/owners" in path else None
        with patch.object(identity.time, "sleep") as sleep:
            with self.assertRaises(httpx.HTTPStatusError) as raised:
                self.reconcile()
        self.assertEqual(raised.exception.response.status_code, 404)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2, 4, 8, 16])
        self.assertEqual(sum(call["method"] == "GET" and "/owners" in call["path"]
                             for call in self.graph.calls), 6)
        state = json.loads(self.path.read_text(encoding="utf-8"))
        app_id = state["spa"]["object_id"]
        self.assertNotIn("service_principal_id", state["spa"])
        self.assertEqual(len(self.graph.apps), 1)
        failures_left = 2

        def pending_app_readiness(method: str, path: str, body: dict | None) -> int | None:
            nonlocal failures_left
            if method == "GET" and path.startswith(f"/applications/{app_id}?") and failures_left:
                failures_left -= 1
                return 404
            return None

        self.graph.fail = pending_app_readiness
        with patch.object(identity.time, "sleep") as sleep:
            result = self.reconcile()
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])
        self.assertEqual(result["spa"]["object_id"], app_id)
        self.assertEqual(len(self.graph.apps), 3)
        self.assertEqual(sum(call["method"] == "POST" and call["path"] == "/applications"
                             for call in self.graph.calls), 3)

    def test_existing_completed_resources_do_not_get_post_create_retries(self) -> None:
        self.reconcile()
        before = len(self.graph.calls)
        self.graph.fail = lambda method, path, body: 404 if method == "GET" and "/owners" in path else None
        with patch.object(identity.time, "sleep") as sleep:
            with self.assertRaises(httpx.HTTPStatusError):
                self.reconcile()
        sleep.assert_not_called()
        self.assertEqual(sum("/owners" in call["path"] for call in self.graph.calls[before:]), 1)
        self.assertEqual(len(self.graph.apps), 3)

    def test_readiness_does_not_retry_other_statuses(self) -> None:
        for status in (403, 429, 500):
            with self.subTest(status=status):
                self.graph.fail = lambda method, path, body: status if method == "GET" and "/owners" in path else None
                before = len(self.graph.calls)
                error = identity.GraphPermissionsError if status == 403 else httpx.HTTPStatusError
                with patch.object(identity.time, "sleep") as sleep:
                    with self.assertRaises(error):
                        self.reconcile()
                sleep.assert_not_called()
                self.assertEqual(sum("/owners" in call["path"] for call in self.graph.calls[before:]), 1)

    def test_unrelated_managed_identity_404_is_not_retried(self) -> None:
        self.graph.fail = lambda method, path, body: 404 if "servicePrincipalType" in path else None
        with patch.object(identity.time, "sleep") as sleep:
            with self.assertRaises(httpx.HTTPStatusError):
                self.reconcile()
        sleep.assert_not_called()
        self.assertEqual(sum("servicePrincipalType" in call["path"] for call in self.graph.calls), 1)
        self.assertFalse(self.path.exists())

    def test_multiple_workers_have_separate_applications_and_federation(self) -> None:
        self.config["agents"].append({"name": "worker-two", "display_name": "Worker Two"})
        self.bindings["agents"]["worker-two"] = copy.deepcopy(self.bindings["agents"]["worker-one"])
        result = self.reconcile()
        self.assertEqual(len(self.graph.apps), 4)
        first, second = result["agents"].values()
        self.assertNotEqual(first["client_id"], second["client_id"])
        self.assertEqual({fic["name"] for fic in self.graph.fics[second["object_id"]]},
                         {"simulator-worker-two-gateway", "simulator-worker-two-runtime"})

    def test_destroy_only_removes_owned_apps_and_sps_not_managed_identities(self) -> None:
        result = self.reconcile()
        foreign = {"id": guid("foreign-app"), "appId": guid("foreign-client"), "tags": [],
                   "displayName": "Autopilots Simulator classroom", "signInAudience": "AzureADMyOrg"}
        self.graph.apps[foreign["id"]] = foreign
        identity.destroy_identities(self.config, self.path)
        self.assertEqual(self.graph.apps, {foreign["id"]: foreign})
        self.assertEqual(set(self.graph.principals), {guid("channel-mi"), guid("worker-mi"), guid("runtime-mi")})
        self.assertFalse(self.path.exists())
        before = len(self.graph.calls)
        identity.destroy_identities(self.config, self.path)
        self.assertEqual(len(self.graph.calls), before)
        self.assertTrue(any(call["path"] == f"/applications/{result['spa']['object_id']}"
                            and call["method"] == "DELETE" for call in self.graph.calls))

    def test_destroy_checks_every_resource_before_deleting_anything(self) -> None:
        result = self.reconcile()
        self.graph.principals[result["agents"]["worker-one"]["service_principal_id"]]["tags"] = []
        before = len(self.graph.calls)
        with self.assertRaisesRegex(ValueError, "foreign ownership"):
            identity.destroy_identities(self.config, self.path)
        self.assertFalse(any(call["method"] == "DELETE" for call in self.graph.calls[before:]))
        self.assertEqual(len(self.graph.apps), 3)

    def test_destroy_resumes_after_partial_failure_and_handles_missing_app(self) -> None:
        result = self.reconcile()
        spa_id = result["spa"]["object_id"]
        self.graph.fail = lambda method, path, body: 403 if method == "DELETE" and path == f"/applications/{spa_id}" else None
        with self.assertRaises(identity.GraphPermissionsError):
            identity.destroy_identities(self.config, self.path)
        self.assertEqual(self.manifest()["operation"], "destroy")
        state = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertNotIn("service_principal_id", state["spa"])
        self.assertIn(spa_id, self.graph.apps)
        del self.graph.apps[spa_id]
        self.graph.fail = None
        identity.destroy_identities(self.config, self.path)
        self.assertFalse(self.path.exists())
        self.assertFalse(self.path.with_name("identities.admin-request.json").exists())
        self.assertEqual(self.graph.apps, {})

    def test_cli_loads_toml_and_bindings_and_prints_only_successful_result(self) -> None:
        config_path = self.workspace / "config.toml"
        config_path.write_text(
            "\n".join(f'{key} = "{value}"' for key, value in self.config.items() if isinstance(value, str))
            + "\nagents = []\nassignments = []\n", encoding="utf-8")
        bindings_path = self.workspace / "bindings.json"
        bindings_path.write_text(json.dumps(self.bindings), encoding="utf-8")
        args = ["--config", str(config_path), "--bindings", str(bindings_path), "--state", str(self.path)]
        with patch("builtins.print") as output:
            self.assertEqual(identity.main(args), 0)
        self.assertIn("spa", json.loads(output.call_args.args[0]))
        with patch("builtins.print"):
            self.assertEqual(identity.main([*args, "--destroy"]), 0)
        self.assertFalse(self.path.exists())
        self.graph.fail = lambda *_: 403
        with patch("builtins.print") as output:
            self.assertEqual(identity.main(args), 1)
        self.assertIn("permissions denied", output.call_args.args[0])


class GraphClientTests(unittest.TestCase):
    def test_cli_token_uses_tenant_without_conflicting_subscription(self) -> None:
        credential = MagicMock()
        requests = []

        def cli_credential(**options: str) -> MagicMock:
            def get_token(scope: str) -> SimpleNamespace:
                if options.get("tenant_id") and options.get("subscription"):
                    raise ValueError("Please specify only one of subscription and tenant, not both")
                return SimpleNamespace(token="opaque-graph-token")

            credential.get_token.side_effect = get_token
            return credential

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"value": []})

        client = httpx.Client(transport=httpx.MockTransport(respond))
        with (patch.object(identity, "AzureCliCredential", side_effect=cli_credential) as factory,
              patch.object(identity.httpx, "Client", return_value=client)):
            with identity.GraphClient(guid("tenant"), guid("subscription")) as graph:
                self.assertEqual(graph.request("GET", "/applications"), {"value": []})
        factory.assert_called_once_with(tenant_id=guid("tenant"))
        credential.get_token.assert_called_once_with(identity.TOKEN_SCOPE)
        self.assertEqual(str(requests[0].url), identity.GRAPH + "/applications")
        self.assertEqual(requests[0].headers["Authorization"], "Bearer opaque-graph-token")
        credential.close.assert_called_once()

    def test_foreign_pagination_never_reaches_network(self) -> None:
        credential = MagicMock()
        transport = MagicMock()
        client = httpx.Client(transport=httpx.MockTransport(transport))
        with (patch.object(identity, "AzureCliCredential", return_value=credential),
              patch.object(identity.httpx, "Client", return_value=client)):
            with identity.GraphClient(guid("tenant"), guid("subscription")) as graph:
                with self.assertRaisesRegex(ValueError, "unexpected pagination URL"):
                    graph.request("GET", "https://foreign.example/v1.0/applications")
        transport.assert_not_called()


if __name__ == "__main__":
    unittest.main()
