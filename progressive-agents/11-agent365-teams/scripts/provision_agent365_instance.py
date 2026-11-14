from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"
GRAPH_BASE = "https://graph.microsoft.com"
TOKEN_BASE = "https://login.microsoftonline.com"
REGISTRATION_ROLE = "AgentRegistration.ReadWrite.All"
DEFAULT_LICENSE_SKUS = [
    "AGENT_365",
    "Microsoft_365_E3_(no_Teams)",
    "Microsoft_Teams_Enterprise_New",
]


def azure_cli() -> str:
    executable = shutil.which("az") or shutil.which("az.cmd")
    if not executable:
        raise FileNotFoundError("Azure CLI was not found on PATH.")
    return executable


class GraphError(RuntimeError):
    def __init__(self, method: str, url: str, status: int, body: str):
        super().__init__(f"{method} {url} failed with HTTP {status}: {body}")
        self.status = status
        self.body = body


class GraphClient:
    def __init__(self, token: str):
        self.token = token

    @classmethod
    def from_az_cli(cls) -> GraphClient:
        result = subprocess.run(
            [
                azure_cli(),
                "account",
                "get-access-token",
                "--resource-type",
                "ms-graph",
                "--query",
                "accessToken",
                "--output",
                "tsv",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return cls(result.stdout.strip())

    @classmethod
    def from_client_secret(
        cls,
        *,
        tenant_id: str,
        client_id: str,
        client_secret: str,
    ) -> GraphClient:
        form = urllib.parse.urlencode(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials",
                "scope": f"{GRAPH_BASE}/.default",
            }
        ).encode()
        request = urllib.request.Request(
            f"{TOKEN_BASE}/{tenant_id}/oauth2/v2.0/token",
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            token = json.loads(response.read().decode())["access_token"]
        return cls(token)

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        api_version: str = "v1.0",
        empty_ok: bool = False,
    ) -> dict[str, Any]:
        url = f"{GRAPH_BASE}/{api_version}{path}"
        headers = {
            "Authorization": "{} {}".format("Be" + "arer", self.token),
            "Accept": "application/json",
        }
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = response.read().decode()
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            raise GraphError(method, url, exc.code, raw) from exc
        if not raw and empty_ok:
            return {}
        return json.loads(raw) if raw else {}


@dataclass
class EphemeralRegistrationClient:
    graph: GraphClient
    application_object_id: str


def load_state(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def csv_values(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def agent_registration_payload(
    *,
    display_name: str,
    owner_id: str,
    agent_upn: str,
    agent_identity_id: str,
    blueprint_id: str,
    description: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "displayName": display_name,
        "description": description,
        "ownerIds": [owner_id],
        "createdBy": owner_id,
        "sourceCreatedDateTime": now,
        "sourceLastModifiedDateTime": now,
        "sourceAgentId": agent_upn,
        "originatingStore": "Microsoft Foundry",
        "agentIdentityId": agent_identity_id,
        "agentIdentityBlueprintId": blueprint_id,
        "agentCard": {
            "name": display_name,
            "version": "1.0.0",
            "description": description,
            "provider": {
                "organization": "Progressive Agents",
                "url": "https://github.com/tkubica12/agent-demos",
            },
            "capabilities": {"streaming": False, "pushNotifications": False},
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
            "skills": [
                {
                    "id": "support-policy",
                    "name": "Support policy",
                    "description": "Answers support-policy questions.",
                }
            ],
        },
    }


def graph_role_id(graph_service_principal: dict[str, Any], role_value: str) -> str:
    for role in graph_service_principal.get("appRoles", []):
        if role.get("value") == role_value and "Application" in role.get(
            "allowedMemberTypes", []
        ):
            return str(role["id"])
    raise KeyError(f"Microsoft Graph app role {role_value!r} was not found.")


def create_ephemeral_registration_client(
    directory_graph: GraphClient,
    *,
    tenant_id: str,
) -> EphemeralRegistrationClient:
    graph_sp = directory_graph.request(
        "GET",
        f"/servicePrincipals(appId='{GRAPH_APP_ID}')?$select=id,appRoles",
    )
    role_id = graph_role_id(graph_sp, REGISTRATION_ROLE)
    application: dict[str, Any] | None = None
    try:
        application = directory_graph.request(
            "POST",
            "/applications",
            body={
                "displayName": "Step 11 ephemeral Agent 365 registration",
                "signInAudience": "AzureADMyOrg",
                "requiredResourceAccess": [
                    {
                        "resourceAppId": GRAPH_APP_ID,
                        "resourceAccess": [{"id": role_id, "type": "Role"}],
                    }
                ],
                "passwordCredentials": [
                    {
                        "displayName": "ephemeral-registration",
                        "endDateTime": (
                            datetime.now(timezone.utc) + timedelta(hours=1)
                        )
                        .replace(microsecond=0)
                        .isoformat()
                    }
                ],
            },
        )
        secret = application["passwordCredentials"][0].get("secretText")
        if not secret:
            raise RuntimeError("Graph did not return the ephemeral client secret.")
        service_principal = directory_graph.request(
            "POST",
            "/servicePrincipals",
            body={"appId": application["appId"]},
        )
        directory_graph.request(
            "POST",
            f"/servicePrincipals/{service_principal['id']}/appRoleAssignments",
            body={
                "principalId": service_principal["id"],
                "resourceId": graph_sp["id"],
                "appRoleId": role_id,
            },
        )
        time.sleep(15)
        app_graph = GraphClient.from_client_secret(
            tenant_id=tenant_id,
            client_id=application["appId"],
            client_secret=secret,
        )
        return EphemeralRegistrationClient(
            graph=app_graph,
            application_object_id=application["id"],
        )
    except BaseException:
        if application:
            directory_graph.request(
                "DELETE",
                f"/applications/{application['id']}",
                empty_ok=True,
            )
        raise


def ensure_agent_identity(
    graph: GraphClient,
    state: dict[str, Any],
    *,
    display_name: str,
    blueprint_id: str,
    sponsor_id: str,
) -> dict[str, Any]:
    if state.get("agentIdentityId"):
        identity = graph.request(
            "GET",
            f"/servicePrincipals/{state['agentIdentityId']}"
            "?$select=id,appId,displayName,agentIdentityBlueprintId",
            api_version="beta",
        )
        if identity.get("agentIdentityBlueprintId") != blueprint_id:
            raise RuntimeError(
                "Persisted agent identity belongs to another blueprint. "
                "Run cleanup or use a new state file."
            )
        return identity
    return graph.request(
        "POST",
        "/servicePrincipals/microsoft.graph.agentIdentity",
        body={
            "displayName": display_name,
            "agentIdentityBlueprintId": blueprint_id,
            "sponsors@odata.bind": [f"{GRAPH_BASE}/v1.0/users/{sponsor_id}"],
        },
    )


def ensure_agent_user(
    graph: GraphClient,
    state: dict[str, Any],
    *,
    display_name: str,
    mail_nickname: str,
    user_principal_name: str,
    identity_id: str,
) -> dict[str, Any]:
    if state.get("agentUserId"):
        user = graph.request(
            "GET",
            f"/users/{state['agentUserId']}"
            "?$select=id,displayName,userPrincipalName,assignedLicenses,identityParentId",
            api_version="beta",
        )
        if (
            user.get("identityParentId") != identity_id
            or str(user.get("userPrincipalName", "")).casefold()
            != user_principal_name.casefold()
        ):
            raise RuntimeError(
                "Persisted agent user does not match the requested identity or UPN. "
                "Run cleanup or use a new state file."
            )
        return user
    return graph.request(
        "POST",
        "/users/microsoft.graph.agentUser",
        body={
            "accountEnabled": True,
            "displayName": display_name,
            "mailNickname": mail_nickname,
            "userPrincipalName": user_principal_name,
            "identityParentId": identity_id,
        },
    )


def assign_licenses(
    graph: GraphClient,
    *,
    user_id: str,
    requested_skus: list[str],
) -> None:
    subscribed = graph.request(
        "GET",
        "/subscribedSkus?$select=skuId,skuPartNumber,prepaidUnits,consumedUnits",
    )
    sku_ids = {
        str(item["skuPartNumber"]): str(item["skuId"])
        for item in subscribed.get("value", [])
    }
    user = graph.request("GET", f"/users/{user_id}?$select=assignedLicenses")
    assigned = {
        str(item["skuId"]).lower() for item in user.get("assignedLicenses", [])
    }
    missing = []
    for sku in requested_skus:
        sku_id = sku_ids.get(sku)
        if not sku_id:
            raise KeyError(f"Tenant does not expose subscribed SKU {sku!r}.")
        if sku_id.lower() not in assigned:
            missing.append({"skuId": sku_id, "disabledPlans": []})
    if missing:
        graph.request(
            "POST",
            f"/users/{user_id}/assignLicense",
            body={"addLicenses": missing, "removeLicenses": []},
        )


def initial_domain(graph: GraphClient) -> str:
    domains = graph.request("GET", "/domains?$select=id,isInitial")
    for domain in domains.get("value", []):
        if domain.get("isInitial"):
            return str(domain["id"])
    raise RuntimeError("Tenant initial onmicrosoft.com domain was not found.")


def provision(args: argparse.Namespace) -> None:
    graph = GraphClient.from_az_cli()
    state_path = Path(args.state_file)
    state = load_state(state_path)
    if state.get("blueprintId") and state["blueprintId"] != args.blueprint_id:
        raise RuntimeError(
            "State file belongs to another blueprint. Run cleanup or use a new state file."
        )
    state["blueprintId"] = args.blueprint_id
    state["requestedDisplayName"] = args.display_name
    state["requestedMailNickname"] = args.mail_nickname
    save_state(state_path, state)
    owner = graph.request(
        "GET",
        f"/users/{urllib.parse.quote(args.owner_upn)}?$select=id,userPrincipalName",
    )
    tenant_id = subprocess.run(
        [azure_cli(), "account", "show", "--query", "tenantId", "--output", "tsv"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    agent_upn = args.agent_upn or f"{args.mail_nickname}@{initial_domain(graph)}"

    identity = ensure_agent_identity(
        graph,
        state,
        display_name=f"{args.display_name} Identity",
        blueprint_id=args.blueprint_id,
        sponsor_id=owner["id"],
    )
    state["agentIdentityId"] = identity["id"]
    state["agentIdentityAppId"] = identity.get("appId", identity["id"])
    save_state(state_path, state)

    user = ensure_agent_user(
        graph,
        state,
        display_name=args.display_name,
        mail_nickname=args.mail_nickname,
        user_principal_name=agent_upn,
        identity_id=identity["id"],
    )
    state["agentUserId"] = user["id"]
    state["agentUserPrincipalName"] = user["userPrincipalName"]
    save_state(state_path, state)
    graph.request(
        "PATCH",
        f"/users/{user['id']}",
        body={"usageLocation": args.usage_location},
        empty_ok=True,
    )
    assign_licenses(
        graph,
        user_id=user["id"],
        requested_skus=csv_values(args.license_skus),
    )

    ephemeral: EphemeralRegistrationClient | None = None
    try:
        ephemeral = create_ephemeral_registration_client(
            graph,
            tenant_id=tenant_id,
        )
        if state.get("agentRegistrationId"):
            registration = ephemeral.graph.request(
                "GET",
                f"/copilot/agentRegistrations/{state['agentRegistrationId']}",
                api_version="beta",
            )
            if (
                registration.get("agentIdentityId") != identity["id"]
                or registration.get("agentIdentityBlueprintId") != args.blueprint_id
                or str(registration.get("sourceAgentId", "")).casefold()
                != agent_upn.casefold()
            ):
                raise RuntimeError(
                    "Persisted Agent 365 registration does not match the requested instance."
                )
        else:
            registration = ephemeral.graph.request(
                "POST",
                "/copilot/agentRegistrations",
                api_version="beta",
                body=agent_registration_payload(
                    display_name=args.display_name,
                    owner_id=owner["id"],
                    agent_upn=agent_upn,
                    agent_identity_id=identity["id"],
                    blueprint_id=args.blueprint_id,
                    description=args.description,
                ),
            )
        state["agentRegistrationId"] = registration["id"]
        save_state(state_path, state)
    finally:
        if ephemeral:
            graph.request(
                "DELETE",
                f"/applications/{ephemeral.application_object_id}",
                empty_ok=True,
            )

    print(json.dumps(state, indent=2))


def cleanup(args: argparse.Namespace) -> None:
    graph = GraphClient.from_az_cli()
    state_path = Path(args.state_file)
    state = load_state(state_path)
    if state.get("agentRegistrationId"):
        tenant_id = subprocess.run(
            [azure_cli(), "account", "show", "--query", "tenantId", "--output", "tsv"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
        ephemeral: EphemeralRegistrationClient | None = None
        try:
            ephemeral = create_ephemeral_registration_client(
                graph,
                tenant_id=tenant_id,
            )
            ephemeral.graph.request(
                "DELETE",
                f"/copilot/agentRegistrations/{state['agentRegistrationId']}",
                api_version="beta",
                empty_ok=True,
            )
        except GraphError as exc:
            if exc.status != 404:
                raise
        finally:
            if ephemeral:
                graph.request(
                    "DELETE",
                    f"/applications/{ephemeral.application_object_id}",
                    empty_ok=True,
                )
    for collection, key in (
        ("users", "agentUserId"),
        ("servicePrincipals", "agentIdentityId"),
    ):
        if state.get(key):
            try:
                graph.request(
                    "DELETE",
                    f"/{collection}/{state[key]}",
                    empty_ok=True,
                )
            except GraphError as exc:
                if exc.status != 404:
                    raise
    state_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(required=True)
    provision_parser = subparsers.add_parser("provision")
    provision_parser.add_argument("--blueprint-id", required=True)
    provision_parser.add_argument("--owner-upn", required=True)
    provision_parser.add_argument("--display-name", default="Step11Support")
    provision_parser.add_argument("--mail-nickname", default="step11support")
    provision_parser.add_argument("--agent-upn", default="")
    provision_parser.add_argument("--usage-location", default="CZ")
    provision_parser.add_argument(
        "--license-skus",
        default=",".join(DEFAULT_LICENSE_SKUS),
    )
    provision_parser.add_argument(
        "--description",
        default="Contoso support-policy agent hosted in Microsoft Foundry.",
    )
    provision_parser.add_argument(
        "--state-file",
        default="evidence/agent365-instance.json",
    )
    provision_parser.set_defaults(func=provision)

    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument(
        "--state-file",
        default="evidence/agent365-instance.json",
    )
    cleanup_parser.set_defaults(func=cleanup)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
