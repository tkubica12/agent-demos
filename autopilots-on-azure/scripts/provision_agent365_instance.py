from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.setup_agent365 import agent365_workspace, default_branding, load_json, write_json
from scripts.tf_helpers import REPO_ROOT, output


GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"
GRAPH_BASE = "https://graph.microsoft.com"
TOKEN_BASE = "https://login.microsoftonline.com"
REGISTRATION_APP_FILE = REPO_ROOT / ".local" / "agent365-registration-app.json"
CATALOG_APP_FILE = REPO_ROOT / ".local" / "agent365-catalog-cleanup-app.json"
PACKAGE_APP_FILE = REPO_ROOT / ".local" / "agent365-package-cleanup-app.json"
DEFAULT_LICENSE_SKUS = [
    "AGENT_365",
    "Microsoft_365_Copilot",
    "Microsoft_365_E5_(no_Teams)",
    "Microsoft_Teams_Enterprise_New",
    "FLOW_FREE",
]


class GraphError(RuntimeError):
    def __init__(self, method: str, url: str, status: int, body: str):
        super().__init__(f"{method} {url} failed with HTTP {status}: {body}")
        self.method = method
        self.url = url
        self.status = status
        self.body = body


@dataclass(frozen=True)
class AuthConfig:
    tenant_id: str
    client_id: str
    client_secret: str


class GraphClient:
    def __init__(self, token: str, *, dry_run: bool = False):
        self.token = token
        self.dry_run = dry_run

    @classmethod
    def from_az_cli(cls, *, dry_run: bool = False) -> "GraphClient":
        token = output(
            [
                "az",
                "account",
                "get-access-token",
                "--resource-type",
                "ms-graph",
                "--query",
                "accessToken",
                "-o",
                "tsv",
            ]
        )
        return cls(token, dry_run=dry_run)

    @classmethod
    def from_client_secret(cls, auth: AuthConfig, *, dry_run: bool = False) -> "GraphClient":
        form = urllib.parse.urlencode(
            {
                "client_id": auth.client_id,
                "client_secret": auth.client_secret,
                "grant_type": "client_credentials",
                "scope": f"{GRAPH_BASE}/.default",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{TOKEN_BASE}/{auth.tenant_id}/oauth2/v2.0/token",
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return cls(str(payload["access_token"]), dry_run=dry_run)

    @classmethod
    def from_device_code(
        cls,
        *,
        tenant_id: str,
        scopes: list[str],
        client_id: str | None = None,
        dry_run: bool = False,
    ) -> "GraphClient":
        from azure.identity import DeviceCodeCredential

        def prompt_callback(verification_uri: str, user_code: str, _expires_on: datetime) -> None:
            print(f"To sign in, use a web browser to open {verification_uri} and enter code {user_code}", flush=True)

        if client_id:
            credential = DeviceCodeCredential(tenant_id=tenant_id, client_id=client_id, prompt_callback=prompt_callback)
        else:
            credential = DeviceCodeCredential(tenant_id=tenant_id, prompt_callback=prompt_callback)
        token = credential.get_token(*scopes)
        return cls(token.token, dry_run=dry_run)

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        api_version: str = "v1.0",
        empty_ok: bool = False,
    ) -> dict[str, Any]:
        url = path if path.startswith("https://") else f"{GRAPH_BASE}/{api_version}{path}"
        if self.dry_run and method.upper() not in {"GET", "HEAD"}:
            print(f"DRY-RUN {method.upper()} {url}", flush=True)
            if body is not None:
                print(json.dumps(redact(body), indent=2), flush=True)
            return {}

        data = None
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise GraphError(method.upper(), url, exc.code, raw) from exc

        if not raw.strip():
            if empty_ok:
                return {}
            return {}
        return json.loads(raw)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if "secret" in key.lower() or key.lower() == "password":
                redacted[key] = "<redacted>"
            else:
                redacted[key] = redact(item)
        return redacted
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def state_file(runtime: str, mail_nickname: str) -> Path:
    return agent365_workspace(runtime) / f"instance.{mail_nickname}.json"


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return load_json(path)


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, state)


def graph_app_role_ids(graph_service_principal: dict[str, Any], role_values: list[str]) -> dict[str, str]:
    roles = graph_service_principal.get("appRoles", [])
    result: dict[str, str] = {}
    for role_value in role_values:
        for role in roles:
            if role.get("value") == role_value and "Application" in role.get("allowedMemberTypes", []):
                result[role_value] = str(role["id"])
                break
        if role_value not in result:
            raise KeyError(f"Microsoft Graph app role '{role_value}' was not found.")
    return result


def graph_oauth_scope_ids(graph_service_principal: dict[str, Any], scope_values: list[str]) -> dict[str, str]:
    scopes = graph_service_principal.get("oauth2PermissionScopes", [])
    result: dict[str, str] = {}
    for scope_value in scope_values:
        for scope in scopes:
            if scope.get("value") == scope_value and scope.get("isEnabled", True):
                result[scope_value] = str(scope["id"])
                break
        if scope_value not in result:
            raise KeyError(f"Microsoft Graph delegated scope '{scope_value}' was not found.")
    return result


def create_registration_app(
    graph: GraphClient,
    *,
    tenant_id: str,
    display_name: str,
    permission_values: list[str],
    output_path: Path,
) -> AuthConfig:
    graph_sp = graph.request(
        "GET",
        f"/servicePrincipals(appId='{GRAPH_APP_ID}')?$select=id,appRoles",
    )
    role_ids = graph_app_role_ids(graph_sp, permission_values)
    required_resource_access = [
        {
            "resourceAppId": GRAPH_APP_ID,
            "resourceAccess": [{"id": role_id, "type": "Role"} for role_id in role_ids.values()],
        }
    ]
    app = graph.request(
        "POST",
        "/applications",
        body={
            "displayName": display_name,
            "signInAudience": "AzureADMyOrg",
            "requiredResourceAccess": required_resource_access,
            "passwordCredentials": [{"displayName": "agent365-registration-api"}],
        },
    )
    if graph.dry_run:
        return AuthConfig(tenant_id=tenant_id, client_id="dry-run-client-id", client_secret="dry-run-client-secret")
    service_principal = graph.request("POST", "/servicePrincipals", body={"appId": app["appId"]})
    for role_value, role_id in role_ids.items():
        graph.request(
            "POST",
            f"/servicePrincipals/{service_principal['id']}/appRoleAssignments",
            body={
                "principalId": service_principal["id"],
                "resourceId": graph_sp["id"],
                "appRoleId": role_id,
            },
        )
        print(f"Granted {role_value} to {display_name}", flush=True)

    secret = app["passwordCredentials"][0].get("secretText")
    if not secret:
        raise RuntimeError("Microsoft Graph did not return a client secret for the registration app.")
    auth = AuthConfig(tenant_id=tenant_id, client_id=app["appId"], client_secret=secret)
    write_json(
        output_path,
        {
            "tenantId": auth.tenant_id,
            "clientId": auth.client_id,
            "clientSecret": auth.client_secret,
            "applicationObjectId": app["id"],
            "servicePrincipalObjectId": service_principal["id"],
            "permissions": permission_values,
        },
    )
    print(f"Wrote app-only auth config to {output_path}", flush=True)
    return auth


def create_delegated_device_code_app(
    graph: GraphClient,
    *,
    tenant_id: str,
    display_name: str,
    scope_values: list[str],
    output_path: Path,
) -> str:
    graph_sp = graph.request(
        "GET",
        f"/servicePrincipals(appId='{GRAPH_APP_ID}')?$select=id,oauth2PermissionScopes",
    )
    scope_ids = graph_oauth_scope_ids(graph_sp, scope_values)
    app = graph.request(
        "POST",
        "/applications",
        body={
            "displayName": display_name,
            "signInAudience": "AzureADMyOrg",
            "isFallbackPublicClient": True,
            "requiredResourceAccess": [
                {
                    "resourceAppId": GRAPH_APP_ID,
                    "resourceAccess": [{"id": scope_id, "type": "Scope"} for scope_id in scope_ids.values()],
                }
            ],
        },
    )
    if graph.dry_run:
        return "dry-run-client-id"
    service_principal = graph.request("POST", "/servicePrincipals", body={"appId": app["appId"]})
    graph.request(
        "POST",
        "/oauth2PermissionGrants",
        body={
            "clientId": service_principal["id"],
            "consentType": "AllPrincipals",
            "resourceId": graph_sp["id"],
            "scope": " ".join(scope_values),
        },
    )
    write_json(
        output_path,
        {
            "tenantId": tenant_id,
            "clientId": app["appId"],
            "applicationObjectId": app["id"],
            "servicePrincipalObjectId": service_principal["id"],
            "delegatedScopes": scope_values,
        },
    )
    print(f"Wrote delegated device-code app config to {output_path}", flush=True)
    return str(app["appId"])


def auth_config_from_file(path: Path) -> AuthConfig:
    payload = load_json(path)
    return AuthConfig(
        tenant_id=str(payload["tenantId"]),
        client_id=str(payload["clientId"]),
        client_secret=str(payload["clientSecret"]),
    )


def owner_id(graph: GraphClient, *, owner_id_arg: str, owner_upn: str) -> str:
    if owner_id_arg:
        return owner_id_arg
    if not owner_upn:
        raise ValueError("Pass --owner-id or --owner-upn.")
    user = graph.request("GET", f"/users/{urllib.parse.quote(owner_upn)}?$select=id,userPrincipalName")
    return str(user["id"])


def generated_blueprint_id(runtime: str, explicit_blueprint_id: str) -> str:
    if explicit_blueprint_id:
        return explicit_blueprint_id
    generated_path = agent365_workspace(runtime) / "a365.generated.config.json"
    generated = load_json(generated_path)
    blueprint_id = str(generated.get("agentBlueprintId", "")).strip()
    if not blueprint_id:
        raise KeyError(f"{generated_path} does not contain agentBlueprintId.")
    return blueprint_id


def ensure_agent_identity(
    graph: GraphClient,
    *,
    state: dict[str, Any],
    display_name: str,
    blueprint_id: str,
    sponsor_user_id: str,
) -> dict[str, Any]:
    existing_id = state.get("agentIdentityId")
    if existing_id:
        identity = graph.request("GET", f"/servicePrincipals/{existing_id}?$select=id,appId,displayName")
        print(f"Using existing agent identity {identity['id']}", flush=True)
        return identity

    identity = graph.request(
        "POST",
        "/servicePrincipals/microsoft.graph.agentIdentity",
        body={
            "displayName": display_name,
            "agentIdentityBlueprintId": blueprint_id,
            "sponsors@odata.bind": [f"{GRAPH_BASE}/v1.0/users/{sponsor_user_id}"],
        },
    )
    if graph.dry_run and not identity:
        identity = {"id": "dry-run-agent-identity-id", "appId": "dry-run-agent-identity-app-id", "displayName": display_name}
    print(f"Created agent identity {identity.get('id')}", flush=True)
    return identity


def ensure_agent_user(
    graph: GraphClient,
    *,
    state: dict[str, Any],
    display_name: str,
    mail_nickname: str,
    user_principal_name: str,
    agent_identity_id: str,
) -> dict[str, Any]:
    existing_id = state.get("agentUserId")
    if existing_id:
        user = graph.request(
            "GET",
            f"/users/{existing_id}?$select=id,displayName,userPrincipalName,mail,usageLocation,assignedLicenses",
        )
        print(f"Using existing agent user {user['userPrincipalName']}", flush=True)
        return user

    user = graph.request(
        "POST",
        "/users/microsoft.graph.agentUser",
        body={
            "accountEnabled": True,
            "displayName": display_name,
            "mailNickname": mail_nickname,
            "userPrincipalName": user_principal_name,
            "identityParentId": agent_identity_id,
        },
    )
    if graph.dry_run and not user:
        user = {"id": "dry-run-agent-user-id", "displayName": display_name, "userPrincipalName": user_principal_name}
    print(f"Created agent user {user.get('userPrincipalName')}", flush=True)
    return user


def update_usage_location(graph: GraphClient, *, user_id: str, usage_location: str) -> None:
    if not usage_location:
        return
    graph.request("PATCH", f"/users/{user_id}", body={"usageLocation": usage_location}, empty_ok=True)
    print(f"Set usageLocation={usage_location}", flush=True)


def sku_ids_by_part_number(graph: GraphClient) -> dict[str, str]:
    payload = graph.request("GET", "/subscribedSkus?$select=skuId,skuPartNumber,prepaidUnits,consumedUnits")
    return {str(item["skuPartNumber"]): str(item["skuId"]) for item in payload.get("value", [])}


def missing_license_payload(user: dict[str, Any], sku_ids: dict[str, str], requested_skus: list[str]) -> list[dict[str, Any]]:
    assigned = {str(item["skuId"]).lower() for item in user.get("assignedLicenses", [])}
    missing = []
    for sku in requested_skus:
        sku_id = sku_ids.get(sku)
        if not sku_id:
            raise KeyError(f"Tenant does not expose subscribed SKU '{sku}'.")
        if sku_id.lower() not in assigned:
            missing.append({"skuId": sku_id, "disabledPlans": []})
    return missing


def delete_graph_object(graph: GraphClient, path: str, *, label: str, api_version: str = "v1.0") -> bool:
    try:
        graph.request("DELETE", path, api_version=api_version, empty_ok=True)
    except GraphError as exc:
        if exc.status == 404:
            print(f"{label} not found.", flush=True)
            return False
        raise
    print(f"Deleted {label}.", flush=True)
    return True


def post_graph_action(graph: GraphClient, path: str, *, label: str, api_version: str = "v1.0") -> None:
    graph.request("POST", path, api_version=api_version, empty_ok=True)
    print(label, flush=True)


def fetch_user_by_upn(graph: GraphClient, user_principal_name: str) -> dict[str, Any] | None:
    try:
        return graph.request(
            "GET",
            f"/users/{urllib.parse.quote(user_principal_name)}?$select=id,displayName,userPrincipalName,mail,usageLocation,identityParentId",
        )
    except GraphError as exc:
        if exc.status == 404:
            return None
        raise


def assign_missing_licenses(graph: GraphClient, *, user_id: str, requested_skus: list[str]) -> list[str]:
    user = graph.request("GET", f"/users/{user_id}?$select=id,assignedLicenses")
    sku_ids = sku_ids_by_part_number(graph)
    add_licenses = missing_license_payload(user, sku_ids, requested_skus)
    if not add_licenses:
        print("All requested licenses are already assigned.", flush=True)
        return []
    graph.request(
        "POST",
        f"/users/{user_id}/assignLicense",
        body={"addLicenses": add_licenses, "removeLicenses": []},
    )
    assigned = [sku for sku in requested_skus if sku_ids.get(sku) in {item["skuId"] for item in add_licenses}]
    print(f"Assigned licenses: {', '.join(assigned)}", flush=True)
    return assigned


def agent_registration_payload(
    *,
    display_name: str,
    description: str,
    owner_id_value: str,
    agent_upn: str,
    agent_identity_id: str,
    blueprint_id: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "displayName": display_name,
        "description": description,
        "ownerIds": [owner_id_value],
        "createdBy": owner_id_value,
        "sourceCreatedDateTime": now,
        "sourceLastModifiedDateTime": now,
        "sourceAgentId": agent_upn,
        "originatingStore": "Autopilots on Azure",
        "agentIdentityId": agent_identity_id,
        "agentIdentityBlueprintId": blueprint_id,
        "agentCard": {
            "name": display_name,
            "version": "1.0.0",
            "description": description,
            "provider": {
                "organization": "Autopilots on Azure",
                "url": "https://github.com/tkubica12/agent-demos",
            },
            "capabilities": {"streaming": False, "pushNotifications": False},
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
            "skills": [
                {
                    "id": "chat",
                    "name": "Chat",
                    "description": description,
                }
            ],
        },
    }


def ensure_agent_registration(
    graph: GraphClient,
    *,
    state: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    existing_id = state.get("agentRegistrationId")
    if existing_id:
        registration = graph.request("GET", f"/copilot/agentRegistrations/{existing_id}", api_version="beta")
        print(f"Using existing agent registration {registration['id']}", flush=True)
        return registration
    registration = graph.request("POST", "/copilot/agentRegistrations", body=payload, api_version="beta")
    if graph.dry_run and not registration:
        registration = {"id": "dry-run-agent-registration-id"}
    print(f"Created agent registration {registration.get('id')}", flush=True)
    return registration


def parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def bootstrap_registration_app_command(args: argparse.Namespace) -> None:
    tenant_id = args.tenant_id or output(["az", "account", "show", "--query", "tenantId", "-o", "tsv"])
    graph = GraphClient.from_az_cli(dry_run=args.dry_run)
    create_registration_app(
        graph,
        tenant_id=tenant_id,
        display_name=args.display_name,
        permission_values=parse_csv(args.permissions),
        output_path=Path(args.output),
    )


def bootstrap_catalog_app_command(args: argparse.Namespace) -> None:
    tenant_id = args.tenant_id or output(["az", "account", "show", "--query", "tenantId", "-o", "tsv"])
    graph = GraphClient.from_az_cli(dry_run=args.dry_run)
    create_delegated_device_code_app(
        graph,
        tenant_id=tenant_id,
        display_name=args.display_name,
        scope_values=parse_csv(args.scopes),
        output_path=Path(args.output),
    )


def provision_command(args: argparse.Namespace) -> None:
    runtime = args.runtime.strip().lower()
    branding = default_branding(runtime)
    state_path = Path(args.state_file) if args.state_file else state_file(runtime, args.mail_nickname)
    state = load_state(state_path)
    directory_graph = GraphClient.from_az_cli(dry_run=args.dry_run)

    resolved_owner_id = owner_id(directory_graph, owner_id_arg=args.owner_id, owner_upn=args.owner_upn)
    blueprint_id = generated_blueprint_id(runtime, args.agent_blueprint_id)
    identity = ensure_agent_identity(
        directory_graph,
        state=state,
        display_name=args.identity_display_name or f"{args.display_name} Identity",
        blueprint_id=blueprint_id,
        sponsor_user_id=resolved_owner_id,
    )
    if identity.get("id"):
        state["agentIdentityId"] = identity["id"]
        state["agentIdentityAppId"] = identity.get("appId", identity["id"])
        save_state(state_path, state)

    user = ensure_agent_user(
        directory_graph,
        state=state,
        display_name=args.display_name,
        mail_nickname=args.mail_nickname,
        user_principal_name=args.agent_upn,
        agent_identity_id=state["agentIdentityId"],
    )
    if user.get("id"):
        state["agentUserId"] = user["id"]
        state["agentUserPrincipalName"] = user.get("userPrincipalName", args.agent_upn)
        save_state(state_path, state)

    update_usage_location(directory_graph, user_id=state["agentUserId"], usage_location=args.usage_location)
    requested_skus = parse_csv(args.license_skus)
    if requested_skus:
        assign_missing_licenses(directory_graph, user_id=state["agentUserId"], requested_skus=requested_skus)

    if args.register:
        auth = auth_config_from_file(Path(args.registration_auth_file))
        registration_graph = GraphClient.from_client_secret(auth, dry_run=args.dry_run)
        payload = agent_registration_payload(
            display_name=args.display_name,
            description=args.description or branding.description_short,
            owner_id_value=resolved_owner_id,
            agent_upn=args.agent_upn,
            agent_identity_id=state["agentIdentityId"],
            blueprint_id=blueprint_id,
        )
        try:
            registration = ensure_agent_registration(registration_graph, state=state, payload=payload)
        except GraphError as exc:
            if exc.status in {403, 404}:
                raise RuntimeError(
                    "Agent 365 registration API is reachable only with the Microsoft Graph "
                    "AgentRegistration.ReadWrite.All application permission and tenant rollout. "
                    f"Graph returned {exc.status}: {exc.body}"
                ) from exc
            raise
        if registration.get("id"):
            state["agentRegistrationId"] = registration["id"]
            save_state(state_path, state)

    print(f"Instance state: {state_path}", flush=True)


def cleanup_command(args: argparse.Namespace) -> None:
    runtime = args.runtime.strip().lower()
    state_path = Path(args.state_file) if args.state_file else state_file(runtime, args.mail_nickname)
    state = load_state(state_path)
    graph = GraphClient.from_az_cli(dry_run=args.dry_run)

    registration_id = args.agent_registration_id or (state.get("agentRegistrationId", "") if args.instance else "")
    if registration_id:
        delete_graph_object(
            graph,
            f"/copilot/agentRegistrations/{registration_id}",
            label=f"Agent 365 registration {registration_id}",
            api_version="beta",
        )

    agent_user_ids = parse_csv(args.agent_user_ids)
    if args.agent_upn:
        user = fetch_user_by_upn(graph, args.agent_upn)
        if user:
            agent_user_ids.append(str(user["id"]))
    if args.instance and state.get("agentUserId"):
        agent_user_ids.append(str(state["agentUserId"]))
    for user_id in sorted(set(agent_user_ids)):
        deleted = delete_graph_object(graph, f"/users/{user_id}", label=f"agent user {user_id}")
        if deleted and args.purge_deleted:
            delete_graph_object(graph, f"/directory/deletedItems/{user_id}", label=f"deleted agent user {user_id}")

    agent_identity_ids = parse_csv(args.agent_identity_ids)
    if args.instance and state.get("agentIdentityId"):
        agent_identity_ids.append(str(state["agentIdentityId"]))
    for identity_id in sorted(set(agent_identity_ids)):
        deleted = delete_graph_object(graph, f"/servicePrincipals/{identity_id}", label=f"agent identity {identity_id}")
        if deleted and args.purge_deleted:
            delete_graph_object(graph, f"/directory/deletedItems/{identity_id}", label=f"deleted agent identity {identity_id}")

    if args.registration_app:
        auth_path = Path(args.registration_auth_file)
        if auth_path.exists():
            auth_payload = load_json(auth_path)
            service_principal_id = str(auth_payload.get("servicePrincipalObjectId", "")).strip()
            application_id = str(auth_payload.get("applicationObjectId", "")).strip()
            if service_principal_id:
                delete_graph_object(
                    graph,
                    f"/servicePrincipals/{service_principal_id}",
                    label=f"registration app service principal {service_principal_id}",
                )
            if application_id:
                delete_graph_object(graph, f"/applications/{application_id}", label=f"registration app {application_id}")
                if args.purge_deleted:
                    delete_graph_object(
                        graph,
                        f"/directory/deletedItems/microsoft.graph.application/{application_id}",
                        label=f"deleted registration app {application_id}",
                    )
            if not args.dry_run:
                auth_path.unlink(missing_ok=True)
                print(f"Removed {auth_path}", flush=True)

    if args.catalog_app:
        catalog_path = Path(args.catalog_auth_file)
        if catalog_path.exists():
            catalog_payload = load_json(catalog_path)
            service_principal_id = str(catalog_payload.get("servicePrincipalObjectId", "")).strip()
            application_id = str(catalog_payload.get("applicationObjectId", "")).strip()
            if service_principal_id:
                delete_graph_object(
                    graph,
                    f"/servicePrincipals/{service_principal_id}",
                    label=f"catalog cleanup app service principal {service_principal_id}",
                )
            if application_id:
                delete_graph_object(graph, f"/applications/{application_id}", label=f"catalog cleanup app {application_id}")
                if args.purge_deleted:
                    delete_graph_object(
                        graph,
                        f"/directory/deletedItems/microsoft.graph.application/{application_id}",
                        label=f"deleted catalog cleanup app {application_id}",
                    )
            if not args.dry_run:
                catalog_path.unlink(missing_ok=True)
                print(f"Removed {catalog_path}", flush=True)

    if args.package_app:
        package_path = Path(args.package_auth_file)
        if package_path.exists():
            package_payload = load_json(package_path)
            service_principal_id = str(package_payload.get("servicePrincipalObjectId", "")).strip()
            application_id = str(package_payload.get("applicationObjectId", "")).strip()
            if service_principal_id:
                delete_graph_object(
                    graph,
                    f"/servicePrincipals/{service_principal_id}",
                    label=f"package cleanup app service principal {service_principal_id}",
                )
            if application_id:
                delete_graph_object(graph, f"/applications/{application_id}", label=f"package cleanup app {application_id}")
                if args.purge_deleted:
                    delete_graph_object(
                        graph,
                        f"/directory/deletedItems/microsoft.graph.application/{application_id}",
                        label=f"deleted package cleanup app {application_id}",
                    )
            if not args.dry_run:
                package_path.unlink(missing_ok=True)
                print(f"Removed {package_path}", flush=True)

    if args.remove_state and not args.dry_run:
        state_path.unlink(missing_ok=True)
        print(f"Removed {state_path}", flush=True)


def teams_app_filter(display_name: str) -> str:
    escaped = display_name.replace("'", "''")
    return f"displayName eq '{escaped}'"


def list_catalog_apps_by_display_name(graph: GraphClient, display_name: str) -> list[dict[str, Any]]:
    encoded_filter = urllib.parse.quote(teams_app_filter(display_name), safe="")
    payload = graph.request(
        "GET",
        f"/appCatalogs/teamsApps?$filter={encoded_filter}&$expand=appDefinitions",
    )
    return list(payload.get("value", []))


def catalog_app_summary(app: dict[str, Any]) -> dict[str, Any]:
    definitions = app.get("appDefinitions", [])
    latest = definitions[0] if definitions else {}
    return {
        "id": app.get("id", ""),
        "displayName": app.get("displayName", ""),
        "externalId": app.get("externalId", ""),
        "distributionMethod": app.get("distributionMethod", ""),
        "publishingState": latest.get("publishingState", ""),
        "version": latest.get("version", ""),
        "teamsAppDefinitionId": latest.get("id", ""),
    }


def cleanup_catalog_command(args: argparse.Namespace) -> None:
    tenant_id = args.tenant_id or output(["az", "account", "show", "--query", "tenantId", "-o", "tsv"])
    auth_path = Path(args.catalog_auth_file)
    if not auth_path.exists():
        raise FileNotFoundError(
            f"{auth_path} does not exist. Run `uv run python -m scripts.provision_agent365_instance "
            "bootstrap-catalog-app` first."
        )
    auth_payload = load_json(auth_path)
    graph = GraphClient.from_device_code(
        tenant_id=tenant_id,
        scopes=[f"{GRAPH_BASE}/AppCatalog.ReadWrite.All"],
        client_id=str(auth_payload["clientId"]),
        dry_run=args.dry_run,
    )
    delete_names = parse_csv(args.delete_display_names)
    keep_names = set(parse_csv(args.keep_display_names))
    if not delete_names:
        raise ValueError("Pass --delete-display-names with at least one exact app display name.")

    deleted = []
    for display_name in delete_names:
        if display_name in keep_names:
            print(f"Skipping kept catalog app name '{display_name}'.", flush=True)
            continue
        apps = list_catalog_apps_by_display_name(graph, display_name)
        if not apps:
            print(f"No catalog app found for '{display_name}'.", flush=True)
            continue
        for app in apps:
            summary = catalog_app_summary(app)
            print("Catalog app candidate:", json.dumps(summary, indent=2), flush=True)
            if summary["distributionMethod"] != "organization":
                print(f"Skipping {summary['id']} because distributionMethod is not organization.", flush=True)
                continue
            delete_graph_object(graph, f"/appCatalogs/teamsApps/{summary['id']}", label=f"catalog app {display_name}")
            deleted.append(summary)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_path, {"deletedCatalogApps": deleted})


def list_agent_registrations_by_display_name(graph: GraphClient, display_name: str) -> list[dict[str, Any]]:
    encoded_filter = urllib.parse.quote(teams_app_filter(display_name), safe="")
    payload = graph.request(
        "GET",
        f"/copilot/agentRegistrations?$filter={encoded_filter}",
        api_version="beta",
    )
    return list(payload.get("value", []))


def registration_summary(registration: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": registration.get("id", ""),
        "displayName": registration.get("displayName", ""),
        "description": registration.get("description", ""),
        "agentIdentityId": registration.get("agentIdentityId", ""),
        "agentIdentityBlueprintId": registration.get("agentIdentityBlueprintId", ""),
        "sourceAgentId": registration.get("sourceAgentId", ""),
        "originatingStore": registration.get("originatingStore", ""),
    }


def cleanup_registrations_command(args: argparse.Namespace) -> None:
    auth = auth_config_from_file(Path(args.registration_auth_file))
    graph = GraphClient.from_client_secret(auth, dry_run=args.dry_run)
    delete_names = parse_csv(args.delete_display_names)
    keep_names = set(parse_csv(args.keep_display_names))
    if not delete_names:
        raise ValueError("Pass --delete-display-names with at least one exact registration display name.")

    deleted = []
    for display_name in delete_names:
        if display_name in keep_names:
            print(f"Skipping kept registration name '{display_name}'.", flush=True)
            continue
        registrations = list_agent_registrations_by_display_name(graph, display_name)
        if not registrations:
            print(f"No Agent 365 registration found for '{display_name}'.", flush=True)
            continue
        for registration in registrations:
            summary = registration_summary(registration)
            print("Agent 365 registration candidate:", json.dumps(summary, indent=2), flush=True)
            if not summary["id"]:
                continue
            delete_graph_object(
                graph,
                f"/copilot/agentRegistrations/{summary['id']}",
                label=f"Agent 365 registration {display_name}",
                api_version="beta",
            )
            deleted.append(summary)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_path, {"deletedAgentRegistrations": deleted})


def list_copilot_packages(graph: GraphClient) -> list[dict[str, Any]]:
    encoded_filter = urllib.parse.quote("supportedHosts/any(h:h eq 'Copilot')", safe="")
    payload = graph.request(
        "GET",
        f"/copilot/admin/catalog/packages?$filter={encoded_filter}",
        api_version="beta",
    )
    return list(payload.get("value", []))


def package_summary(package: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": package.get("id", ""),
        "displayName": package.get("displayName", ""),
        "isBlocked": package.get("isBlocked", ""),
        "supportedHosts": package.get("supportedHosts", []),
        "platform": package.get("platform", ""),
        "type": package.get("type", ""),
        "manifestId": package.get("manifestId", ""),
        "appId": package.get("appId", ""),
        "lastModifiedDateTime": package.get("lastModifiedDateTime", ""),
    }


def cleanup_packages_command(args: argparse.Namespace) -> None:
    tenant_id = args.tenant_id or output(["az", "account", "show", "--query", "tenantId", "-o", "tsv"])
    auth_path = Path(args.package_auth_file)
    if not auth_path.exists():
        raise FileNotFoundError(
            f"{auth_path} does not exist. Run `uv run python -m scripts.provision_agent365_instance "
            "bootstrap-catalog-app --scopes CopilotPackages.ReadWrite.All "
            f"--output {auth_path}` first."
        )
    auth_payload = load_json(auth_path)
    scopes = [f"{GRAPH_BASE}/{scope}" for scope in auth_payload.get("delegatedScopes", ["CopilotPackages.ReadWrite.All"])]
    graph = GraphClient.from_device_code(
        tenant_id=tenant_id,
        scopes=scopes,
        client_id=str(auth_payload["clientId"]),
        dry_run=args.dry_run,
    )
    delete_names = set(parse_csv(args.delete_display_names))
    keep_names = set(parse_csv(args.keep_display_names))
    if not delete_names:
        raise ValueError("Pass --delete-display-names with at least one exact package display name.")

    matched = []
    for package in list_copilot_packages(graph):
        summary = package_summary(package)
        if summary["displayName"] not in delete_names:
            continue
        if summary["displayName"] in keep_names:
            print(f"Skipping kept package name '{summary['displayName']}'.", flush=True)
            continue
        print("Copilot package candidate:", json.dumps(summary, indent=2), flush=True)
        if args.block and summary["id"] and not summary["isBlocked"]:
            post_graph_action(
                graph,
                f"/copilot/admin/catalog/packages/{summary['id']}/block",
                label=f"Blocked Copilot package {summary['displayName']}.",
                api_version="beta",
            )
            summary["blockedByScript"] = True
        elif summary["isBlocked"]:
            summary["blockedByScript"] = False
            print(f"Package '{summary['displayName']}' is already blocked.", flush=True)
        matched.append(summary)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(
        output_path,
        {
            "matchedPackages": matched,
            "deleteSupported": False,
            "note": "Microsoft Graph Package Management API exposes block/unblock/update, but no package delete API.",
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision Agent 365 AI teammate identity/user/registration via Graph.")
    parser.add_argument("--dry-run", action="store_true", help="Print write calls without sending them.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    bootstrap = subparsers.add_parser(
        "bootstrap-registration-app",
        help="Create a local app-only Graph client for the Agent 365 registration API.",
    )
    bootstrap.add_argument("--tenant-id", default="", help="Tenant ID. Defaults to az account tenant.")
    bootstrap.add_argument("--display-name", default="Autopilots Agent 365 Registration API")
    bootstrap.add_argument("--output", default=str(REGISTRATION_APP_FILE))
    bootstrap.add_argument("--permissions", default="AgentRegistration.ReadWrite.All")
    bootstrap.set_defaults(func=bootstrap_registration_app_command)

    bootstrap_catalog = subparsers.add_parser(
        "bootstrap-catalog-app",
        help="Create a tenant-local public client for delegated Teams app-catalog cleanup.",
    )
    bootstrap_catalog.add_argument("--tenant-id", default="", help="Tenant ID. Defaults to az account tenant.")
    bootstrap_catalog.add_argument("--display-name", default="Autopilots Agent 365 Catalog Cleanup")
    bootstrap_catalog.add_argument("--output", default=str(CATALOG_APP_FILE))
    bootstrap_catalog.add_argument("--scopes", default="AppCatalog.ReadWrite.All")
    bootstrap_catalog.set_defaults(func=bootstrap_catalog_app_command)

    provision = subparsers.add_parser(
        "provision",
        help="Create or reuse an Agent ID identity, agent user, licenses, and optional Agent 365 registration.",
    )
    provision.add_argument("--runtime", choices=["openclaw", "hermes"], required=True)
    provision.add_argument("--owner-id", default="", help="Owner/sponsor user object ID.")
    provision.add_argument("--owner-upn", default="", help="Owner/sponsor UPN. Used when --owner-id is omitted.")
    provision.add_argument("--agent-upn", required=True, help="Agent user UPN, usually on the tenant onmicrosoft.com domain.")
    provision.add_argument("--display-name", required=True, help="Agent user display name.")
    provision.add_argument("--mail-nickname", required=True)
    provision.add_argument("--identity-display-name", default="")
    provision.add_argument("--agent-blueprint-id", default="", help="Defaults to .local/<runtime>/agent365 generated config.")
    provision.add_argument("--usage-location", default="CZ")
    provision.add_argument("--license-skus", default=",".join(DEFAULT_LICENSE_SKUS))
    provision.add_argument("--state-file", default="")
    provision.add_argument("--register", action="store_true", help="Also call beta /copilot/agentRegistrations.")
    provision.add_argument("--registration-auth-file", default=str(REGISTRATION_APP_FILE))
    provision.add_argument("--description", default="")
    provision.set_defaults(func=provision_command)

    cleanup = subparsers.add_parser(
        "cleanup",
        help="Delete Graph artifacts created by this script or earlier direct Agent 365 attempts.",
    )
    cleanup.add_argument("--runtime", choices=["openclaw", "hermes"], required=True)
    cleanup.add_argument("--mail-nickname", required=True)
    cleanup.add_argument("--agent-upn", default="", help="Agent user UPN to delete when present.")
    cleanup.add_argument("--agent-user-ids", default="", help="Comma-separated extra agent user object IDs to delete.")
    cleanup.add_argument("--agent-identity-ids", default="", help="Comma-separated extra agent identity service principal IDs.")
    cleanup.add_argument("--agent-registration-id", default="")
    cleanup.add_argument("--instance", action="store_true", help="Delete agent user/identity IDs from the instance state file.")
    cleanup.add_argument("--state-file", default="")
    cleanup.add_argument("--remove-state", action="store_true")
    cleanup.add_argument("--registration-app", action="store_true", help="Delete the local app-only registration client too.")
    cleanup.add_argument("--registration-auth-file", default=str(REGISTRATION_APP_FILE))
    cleanup.add_argument("--catalog-app", action="store_true", help="Delete the local delegated app-catalog cleanup client too.")
    cleanup.add_argument("--catalog-auth-file", default=str(CATALOG_APP_FILE))
    cleanup.add_argument("--package-app", action="store_true", help="Delete the local delegated package cleanup client too.")
    cleanup.add_argument("--package-auth-file", default=str(PACKAGE_APP_FILE))
    cleanup.add_argument("--purge-deleted", action="store_true", help="Permanently purge supported deleted directory objects.")
    cleanup.set_defaults(func=cleanup_command)

    catalog = subparsers.add_parser(
        "cleanup-catalog",
        help="Delete stale Teams/admin-center app-catalog entries by exact display name.",
    )
    catalog.add_argument("--tenant-id", default="", help="Tenant ID. Defaults to az account tenant.")
    catalog.add_argument("--delete-display-names", required=True, help="Comma-separated exact display names to delete.")
    catalog.add_argument("--keep-display-names", default="", help="Comma-separated exact display names that must not be deleted.")
    catalog.add_argument("--catalog-auth-file", default=str(CATALOG_APP_FILE))
    catalog.add_argument("--output", default=str(REPO_ROOT / ".local" / "agent365-catalog-cleanup.json"))
    catalog.set_defaults(func=cleanup_catalog_command)

    registrations = subparsers.add_parser(
        "cleanup-registrations",
        help="Delete stale Agent 365 registrations by exact display name.",
    )
    registrations.add_argument("--delete-display-names", required=True, help="Comma-separated exact registration names to delete.")
    registrations.add_argument("--keep-display-names", default="", help="Comma-separated exact names that must not be deleted.")
    registrations.add_argument("--registration-auth-file", default=str(REGISTRATION_APP_FILE))
    registrations.add_argument("--output", default=str(REPO_ROOT / ".local" / "agent365-registration-cleanup.json"))
    registrations.set_defaults(func=cleanup_registrations_command)

    packages = subparsers.add_parser(
        "cleanup-packages",
        help="Find stale Copilot package inventory rows by exact display name and block them. Delete is not exposed by Graph.",
    )
    packages.add_argument("--tenant-id", default="", help="Tenant ID. Defaults to az account tenant.")
    packages.add_argument("--delete-display-names", required=True, help="Comma-separated exact package names to target.")
    packages.add_argument("--keep-display-names", default="", help="Comma-separated exact names that must not be touched.")
    packages.add_argument("--package-auth-file", default=str(PACKAGE_APP_FILE))
    packages.add_argument("--block", action="store_true", help="Block matching packages that are not already blocked.")
    packages.add_argument("--output", default=str(REPO_ROOT / ".local" / "agent365-package-cleanup.json"))
    packages.set_defaults(func=cleanup_packages_command)

    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
