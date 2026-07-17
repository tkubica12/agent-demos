from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

from scripts.provision_agent365_instance import GraphClient, load_state, state_file
from scripts.setup_agent365 import agent365_workspace, load_json, write_json
from scripts.tf_helpers import APPS_DIR, PLATFORM_DIR, REPO_ROOT, output, resolve_executable, terraform_output, write_tfvars


A7_API_STATE = REPO_ROOT / ".local" / "a7-private-mcp-api.json"
A7_PUBLIC_API_STATE = REPO_ROOT / ".local" / "a7-public-shipments-mcp-api.json"
TOOLING_MANIFEST = REPO_ROOT / "agent365" / "ToolingManifest.json"
INCIDENTS_APP_ROLE = "Incidents.Read.All"
INCIDENTS_DELEGATED_SCOPE = "Incidents.Read"
SHIPMENTS_APP_ROLE = "Shipments.Read.All"
SHIPMENTS_DELEGATED_SCOPE = "Shipments.Read"


def runtime_app_tfvars_path(runtime: str, state_name: str = "") -> Path:
    return REPO_ROOT / ".local" / (state_name or runtime) / "apps" / "generated.app.auto.tfvars.json"


def generated_blueprint_id(runtime: str, state_name: str = "") -> str:
    payload = load_json(agent365_workspace(state_name or runtime) / "a365.generated.config.json")
    value = str(payload.get("agentBlueprintId", "")).strip()
    if not value:
        raise KeyError(f"Agent 365 generated config for {runtime} does not contain agentBlueprintId.")
    return value


def first_value(payload: dict[str, Any]) -> dict[str, Any] | None:
    values = payload.get("value", [])
    return values[0] if values else None


def application_by_display_name(graph: GraphClient, display_name: str) -> dict[str, Any] | None:
    encoded_filter = urllib.parse.quote(f"displayName eq '{display_name}'", safe="'")
    return first_value(
        graph.request(
            "GET",
            f"/applications?$filter={encoded_filter}&$select=id,appId,displayName,appRoles,api",
        )
    )


def ensure_private_mcp_api(graph: GraphClient, state: dict[str, Any], *, display_name: str) -> dict[str, str]:
    app_object_id = str(state.get("applicationObjectId", ""))
    if not app_object_id:
        existing_app = application_by_display_name(graph, display_name)
        app_object_id = str(existing_app["id"]) if existing_app else ""
    if app_object_id:
        app = graph.request("GET", f"/applications/{app_object_id}?$select=id,appId,displayName,appRoles,api")
    else:
        app_role_id = str(uuid.uuid4())
        delegated_scope_id = str(uuid.uuid4())
        app = graph.request(
            "POST",
            "/applications",
            body={
                "displayName": display_name,
                "signInAudience": "AzureADMyOrg",
                "api": {
                    "requestedAccessTokenVersion": 2,
                    "oauth2PermissionScopes": [
                        {
                            "adminConsentDescription": "Read private operational incidents on behalf of the signed-in user.",
                            "adminConsentDisplayName": "Read private operational incidents",
                            "id": delegated_scope_id,
                            "isEnabled": True,
                            "type": "Admin",
                            "userConsentDescription": "Read private operational incidents for this request.",
                            "userConsentDisplayName": "Read private operational incidents",
                            "value": INCIDENTS_DELEGATED_SCOPE,
                        }
                    ],
                },
                "appRoles": [
                    {
                        "allowedMemberTypes": ["Application"],
                        "description": "Read private operational incidents as an autonomous agent.",
                        "displayName": "Read private operational incidents",
                        "id": app_role_id,
                        "isEnabled": True,
                        "value": INCIDENTS_APP_ROLE,
                    }
                ],
            },
        )
        if graph.dry_run and not app:
            app = {
                "id": "dry-run-private-mcp-app-object-id",
                "appId": "00000000-0000-0000-0000-000000000007",
                "appRoles": [{"id": app_role_id, "value": INCIDENTS_APP_ROLE}],
            }
        graph.request(
            "PATCH",
            f"/applications/{app['id']}",
            body={"identifierUris": [f"api://{app['appId']}"]},
            empty_ok=True,
        )

    app_id = str(app["appId"])
    service_principal_id = str(state.get("servicePrincipalObjectId", ""))
    if not service_principal_id:
        encoded_filter = urllib.parse.quote(f"appId eq '{app_id}'", safe="'")
        existing = first_value(
            graph.request(
                "GET",
                f"/servicePrincipals?$filter={encoded_filter}&$select=id,appId,displayName,appRoles",
            )
        )
        service_principal = existing or graph.request("POST", "/servicePrincipals", body={"appId": app_id})
        if graph.dry_run and not service_principal:
            service_principal = {"id": "dry-run-private-mcp-service-principal-id"}
        service_principal_id = str(service_principal["id"])

    app_roles = app.get("appRoles", [])
    role = next((item for item in app_roles if item.get("value") == INCIDENTS_APP_ROLE), None)
    if role is None:
        refreshed = graph.request("GET", f"/applications/{app['id']}?$select=appRoles")
        role = next(item for item in refreshed.get("appRoles", []) if item.get("value") == INCIDENTS_APP_ROLE)
    return {
        "applicationObjectId": str(app["id"]),
        "applicationClientId": app_id,
        "servicePrincipalObjectId": service_principal_id,
        "audience": f"api://{app_id}",
        "appRoleId": str(role["id"]),
        "appRole": INCIDENTS_APP_ROLE,
        "delegatedScope": INCIDENTS_DELEGATED_SCOPE,
    }


def ensure_public_shipments_api(graph: GraphClient, state: dict[str, Any], *, display_name: str) -> dict[str, str]:
    app_object_id = str(state.get("applicationObjectId", ""))
    if not app_object_id:
        existing_app = application_by_display_name(graph, display_name)
        app_object_id = str(existing_app["id"]) if existing_app else ""
    if app_object_id:
        app = graph.request("GET", f"/applications/{app_object_id}?$select=id,appId,displayName,appRoles,api")
        scopes = app.get("api", {}).get("oauth2PermissionScopes", [])
        shipments_scope = next((scope for scope in scopes if scope.get("value") == SHIPMENTS_DELEGATED_SCOPE), None)
        if shipments_scope and shipments_scope.get("type") != "User":
            shipments_scope["type"] = "User"
            graph.request(
                "PATCH",
                f"/applications/{app_object_id}",
                body={"api": {"oauth2PermissionScopes": scopes}},
                empty_ok=True,
            )
    else:
        app_role_id = str(uuid.uuid4())
        delegated_scope_id = str(uuid.uuid4())
        app = graph.request(
            "POST",
            "/applications",
            body={
                "displayName": display_name,
                "signInAudience": "AzureADMyOrg",
                "api": {
                    "requestedAccessTokenVersion": 2,
                    "oauth2PermissionScopes": [
                        {
                            "adminConsentDescription": "Read public demo shipment status.",
                            "adminConsentDisplayName": "Read public demo shipments",
                            "id": delegated_scope_id,
                            "isEnabled": True,
                            "type": "User",
                            "userConsentDescription": "Read public demo shipment status.",
                            "userConsentDisplayName": "Read public demo shipments",
                            "value": SHIPMENTS_DELEGATED_SCOPE,
                        }
                    ],
                },
                "appRoles": [
                    {
                        "allowedMemberTypes": ["Application"],
                        "description": "Read public demo shipment status as an autonomous agent.",
                        "displayName": "Read public demo shipments",
                        "id": app_role_id,
                        "isEnabled": True,
                        "value": SHIPMENTS_APP_ROLE,
                    }
                ],
            },
        )
        if graph.dry_run and not app:
            app = {
                "id": "dry-run-public-shipments-app-object-id",
                "appId": "00000000-0000-0000-0000-000000000008",
                "appRoles": [{"id": app_role_id, "value": SHIPMENTS_APP_ROLE}],
            }
        graph.request(
            "PATCH",
            f"/applications/{app['id']}",
            body={"identifierUris": [f"api://{app['appId']}"]},
            empty_ok=True,
        )

    app_id = str(app["appId"])
    service_principal_id = str(state.get("servicePrincipalObjectId", ""))
    if not service_principal_id:
        encoded_filter = urllib.parse.quote(f"appId eq '{app_id}'", safe="'")
        existing = first_value(
            graph.request(
                "GET",
                f"/servicePrincipals?$filter={encoded_filter}&$select=id,appId,displayName,appRoles",
            )
        )
        service_principal = existing or graph.request("POST", "/servicePrincipals", body={"appId": app_id})
        if graph.dry_run and not service_principal:
            service_principal = {"id": "dry-run-public-shipments-service-principal-id"}
        service_principal_id = str(service_principal["id"])

    app_roles = app.get("appRoles", [])
    role = next((item for item in app_roles if item.get("value") == SHIPMENTS_APP_ROLE), None)
    if role is None:
        refreshed = graph.request("GET", f"/applications/{app['id']}?$select=appRoles")
        role = next(item for item in refreshed.get("appRoles", []) if item.get("value") == SHIPMENTS_APP_ROLE)
    return {
        "applicationObjectId": str(app["id"]),
        "applicationClientId": app_id,
        "servicePrincipalObjectId": service_principal_id,
        "audience": f"api://{app_id}",
        "appRoleId": str(role["id"]),
        "appRole": SHIPMENTS_APP_ROLE,
        "delegatedScope": SHIPMENTS_DELEGATED_SCOPE,
    }


def blueprint_application_object_id(graph: GraphClient, blueprint_client_id: str) -> str:
    encoded_filter = urllib.parse.quote(f"appId eq '{blueprint_client_id}'", safe="'")
    payload = graph.request("GET", f"/applications?$filter={encoded_filter}&$select=id,appId,displayName")
    blueprint = first_value(payload)
    if not blueprint:
        raise KeyError(f"Agent identity blueprint application {blueprint_client_id} was not found.")
    return str(blueprint["id"])


def ensure_federated_credential(
    graph: GraphClient,
    *,
    blueprint_object_id: str,
    tenant_id: str,
    name: str,
    managed_identity_principal_id: str,
) -> None:
    payload = graph.request("GET", f"/applications/{blueprint_object_id}/federatedIdentityCredentials")
    existing = next((item for item in payload.get("value", []) if item.get("name") == name), None)
    expected_issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
    if existing:
        if existing.get("issuer") == expected_issuer and existing.get("subject") == managed_identity_principal_id:
            return
        credential_id = existing.get("id")
        if not credential_id:
            raise RuntimeError(f"Federated credential {name} cannot be replaced because its id is missing.")
        graph.request(
            "DELETE",
            f"/applications/{blueprint_object_id}/federatedIdentityCredentials/{credential_id}",
        )
    graph.request(
        "POST",
        f"/applications/{blueprint_object_id}/federatedIdentityCredentials",
        body={
            "name": name,
            "issuer": expected_issuer,
            "subject": managed_identity_principal_id,
            "audiences": ["api://AzureADTokenExchange"],
        },
    )


def ensure_app_role_assignment(
    graph: GraphClient,
    *,
    principal_id: str,
    resource_id: str,
    app_role_id: str,
) -> None:
    payload = graph.request("GET", f"/servicePrincipals/{principal_id}/appRoleAssignments")
    if any(
        str(item.get("resourceId")) == resource_id and str(item.get("appRoleId")) == app_role_id
        for item in payload.get("value", [])
    ):
        return
    graph.request(
        "POST",
        f"/servicePrincipals/{principal_id}/appRoleAssignments",
        body={
            "principalId": principal_id,
            "resourceId": resource_id,
            "appRoleId": app_role_id,
        },
    )


def ensure_agent_user_delegated_grant(
    graph: GraphClient,
    *,
    agent_identity_object_id: str,
    agent_user_id: str,
    resource_app_id: str,
    scopes: list[str],
) -> None:
    encoded_resource_filter = urllib.parse.quote(f"appId eq '{resource_app_id}'", safe="'")
    resource_sp = first_value(
        graph.request(
            "GET",
            f"/servicePrincipals?$filter={encoded_resource_filter}&$select=id,appId,displayName",
        )
    )
    if not resource_sp:
        raise KeyError(f"Service principal for resource {resource_app_id} was not found.")
    resource_id = str(resource_sp["id"])
    encoded_grant_filter = urllib.parse.quote(
        (
            f"clientId eq '{agent_identity_object_id}' and "
            f"principalId eq '{agent_user_id}' and resourceId eq '{resource_id}'"
        ),
        safe="'",
    )
    existing = first_value(graph.request("GET", f"/oauth2PermissionGrants?$filter={encoded_grant_filter}"))
    requested = set(scopes)
    if existing:
        current = set(str(existing.get("scope", "")).split())
        merged = sorted(current | requested)
        if current != set(merged):
            graph.request(
                "PATCH",
                f"/oauth2PermissionGrants/{existing['id']}",
                body={"scope": " ".join(merged)},
            )
        return
    graph.request(
        "POST",
        "/oauth2PermissionGrants",
        body={
            "clientId": agent_identity_object_id,
            "consentType": "Principal",
            "principalId": agent_user_id,
            "resourceId": resource_id,
            "scope": " ".join(sorted(requested)),
        },
    )


def update_runtime_tfvars(
    *,
    runtime: str,
    state_name: str = "",
    identity_state: dict[str, Any],
    api_state: dict[str, str],
    public_api_state: dict[str, str],
    tenant_id: str,
) -> dict[str, Any]:
    runtime_path = runtime_app_tfvars_path(runtime, state_name)
    if not runtime_path.exists():
        raise FileNotFoundError(f"{runtime_path} does not exist. Run scripts.setup_app_tfvars first.")
    tfvars = load_json(runtime_path)
    tfvars.update(
        {
            "agent365_tenant_id": tenant_id,
            "agent365_agent_identity_client_id": identity_state["agentIdentityAppId"],
            "agent365_agent_identity_object_id": identity_state["agentIdentityId"],
            "agent365_agent_user_id": identity_state["agentUserId"],
            "agent365_agent_user_principal_name": identity_state["agentUserPrincipalName"],
            "private_mcp_api_audience": api_state["audience"],
            "public_shipments_mcp_api_audience": public_api_state["audience"],
        }
    )
    write_tfvars(runtime_path, tfvars)
    write_tfvars(APPS_DIR / "generated.app.auto.tfvars.json", tfvars)
    write_tfvars(APPS_DIR / "generated.runtime.auto.tfvars.json", tfvars)
    return tfvars


def workiq_permissions_configured(runtime: str, state_name: str = "") -> bool:
    generated_path = agent365_workspace(state_name or runtime) / "a365.generated.config.json"
    if not generated_path.exists():
        return False
    generated = load_json(generated_path)
    consents = generated.get("resourceConsents", [])
    manifest = load_json(TOOLING_MANIFEST)
    for server in manifest.get("mcpServers", []):
        audience = str(server.get("audience", "")).strip()
        scope = str(server.get("scope", "")).strip()
        if not any(
            consent.get("resourceAppId") == audience
            and consent.get("consentGranted") is True
            and consent.get("inheritablePermissionsConfigured") is True
            and scope in consent.get("scopes", [])
            for consent in consents
        ):
            return False
    return True


def configure_workiq_permissions(
    runtime: str,
    *,
    state_name: str = "",
    dry_run: bool,
    force: bool = False,
) -> None:
    workspace = agent365_workspace(state_name or runtime)
    shutil.copyfile(TOOLING_MANIFEST, workspace / "ToolingManifest.json")
    if not force and not dry_run and workiq_permissions_configured(runtime, state_name):
        print(f"Work IQ MCP permissions are already configured for {runtime}; skipping interactive admin consent.")
        return
    manifest = load_json(TOOLING_MANIFEST)
    for server in manifest.get("mcpServers", []):
        audience = str(server.get("audience", "")).strip()
        if not audience:
            continue
        existing = subprocess.run(
            [resolve_executable("az"), "ad", "sp", "show", "--id", audience, "-o", "none"],
            capture_output=True,
            text=True,
            check=False,
        )
        if existing.returncode != 0 and not dry_run:
            subprocess.run([resolve_executable("az"), "ad", "sp", "create", "--id", audience, "-o", "none"], check=True)
    command = [resolve_executable("a365"), "setup", "permissions", "mcp"]
    if dry_run:
        command.append("--dry-run")
    result = subprocess.run(command, cwd=workspace, capture_output=True, text=True, check=True)
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="")
    combined = f"{result.stdout}\n{result.stderr}"
    if "ERROR:" in combined or "operation was canceled" in combined.lower():
        raise RuntimeError("Agent 365 MCP permission configuration did not complete.")


def ensure_workiq_agent_user_grants(
    graph: GraphClient,
    *,
    identity_state: dict[str, Any],
) -> None:
    manifest = load_json(TOOLING_MANIFEST)
    for server in manifest.get("mcpServers", []):
        audience = str(server.get("audience", "")).strip()
        scope = str(server.get("scope", "")).strip()
        if audience and scope:
            ensure_agent_user_delegated_grant(
                graph,
                agent_identity_object_id=str(identity_state["agentIdentityId"]),
                agent_user_id=str(identity_state["agentUserId"]),
                resource_app_id=audience,
                scopes=[scope],
            )


def sandbox_group_principal_id(platform_outputs: dict[str, Any]) -> str:
    principal_id = str(platform_outputs.get("sandbox_group_principal_id", "")).strip()
    if principal_id:
        return principal_id
    return output(
        [
            "az",
            "resource",
            "show",
            "--resource-group",
            str(platform_outputs["resource_group_name"]),
            "--resource-type",
            "Microsoft.App/sandboxGroups",
            "--name",
            str(platform_outputs["sandbox_group_name"]),
            "--api-version",
            "2026-02-01-preview",
            "--query",
            "identity.principalId",
            "-o",
            "tsv",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure milestone A7 Agent Identity, Agent User, and private MCP permissions.")
    parser.add_argument("--runtime", choices=["openclaw", "hermes"], required=True)
    parser.add_argument("--state-name", default="", help="Local Worker state directory under .local.")
    parser.add_argument("--mail-nickname", default="")
    parser.add_argument("--state-file", default="")
    parser.add_argument("--api-state-file", default=str(A7_API_STATE))
    parser.add_argument("--public-api-state-file", default=str(A7_PUBLIC_API_STATE))
    parser.add_argument("--api-display-name", default="Autopilots Private Incidents MCP")
    parser.add_argument("--public-api-display-name", default="Autopilots Public Shipments MCP")
    parser.add_argument("--skip-workiq-permissions", action="store_true")
    parser.add_argument("--force-workiq-permissions", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    runtime = args.runtime
    state_name = args.state_name or runtime
    mail_nickname = args.mail_nickname or f"{runtime}1"
    instance_path = Path(args.state_file) if args.state_file else state_file(runtime, mail_nickname)
    identity_state = load_state(instance_path)
    required = ["agentIdentityId", "agentIdentityAppId", "agentUserId", "agentUserPrincipalName"]
    missing = [name for name in required if not identity_state.get(name)]
    if missing:
        raise KeyError(f"{instance_path} is missing: {', '.join(missing)}")

    tenant_id = output(["az", "account", "show", "--query", "tenantId", "-o", "tsv"])
    platform_outputs = terraform_output(PLATFORM_DIR)
    sandbox_principal_id = sandbox_group_principal_id(platform_outputs)
    graph = GraphClient.from_az_cli(dry_run=args.dry_run)
    api_state_path = Path(args.api_state_file)
    api_state = ensure_private_mcp_api(
        graph,
        load_state(api_state_path),
        display_name=args.api_display_name,
    )
    if not args.dry_run:
        write_json(api_state_path, api_state)
    public_api_state_path = Path(args.public_api_state_file)
    public_api_state = ensure_public_shipments_api(
        graph,
        load_state(public_api_state_path),
        display_name=args.public_api_display_name,
    )
    if not args.dry_run:
        write_json(public_api_state_path, public_api_state)

    blueprint_client_id = generated_blueprint_id(runtime, state_name)
    blueprint_object_id = blueprint_application_object_id(graph, blueprint_client_id)
    ensure_federated_credential(
        graph,
        blueprint_object_id=blueprint_object_id,
        tenant_id=tenant_id,
        name=f"a7-{state_name}-sandbox",
        managed_identity_principal_id=sandbox_principal_id,
    )
    ensure_app_role_assignment(
        graph,
        principal_id=str(identity_state["agentIdentityId"]),
        resource_id=api_state["servicePrincipalObjectId"],
        app_role_id=api_state["appRoleId"],
    )
    ensure_app_role_assignment(
        graph,
        principal_id=str(identity_state["agentIdentityId"]),
        resource_id=public_api_state["servicePrincipalObjectId"],
        app_role_id=public_api_state["appRoleId"],
    )
    if not args.skip_workiq_permissions:
        configure_workiq_permissions(
            runtime,
            state_name=state_name,
            dry_run=args.dry_run,
            force=args.force_workiq_permissions,
        )
        ensure_workiq_agent_user_grants(graph, identity_state=identity_state)

    if not args.dry_run:
        tfvars = update_runtime_tfvars(
            runtime=runtime,
            state_name=state_name,
            identity_state=identity_state,
            api_state=api_state,
            public_api_state=public_api_state,
            tenant_id=tenant_id,
        )
        print(
            json.dumps(
                {
                    "runtime": runtime,
                    "agentIdentityClientId": tfvars["agent365_agent_identity_client_id"],
                    "agentUserPrincipalName": tfvars["agent365_agent_user_principal_name"],
                    "privateMcpAudience": tfvars["private_mcp_api_audience"],
                    "publicShipmentsMcpAudience": tfvars["public_shipments_mcp_api_audience"],
                    "toolingManifest": str(TOOLING_MANIFEST),
                    "next": "Apply terraform/apps, rebuild the runtime and private MCP images, then reset the sandbox.",
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
