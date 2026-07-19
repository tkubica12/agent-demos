from __future__ import annotations

import argparse
import json
import re
import subprocess
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.provision_agent365_instance import GraphClient
from scripts.setup_agent365 import agent365_workspace, load_json, write_json
from scripts.tf_helpers import APPS_DIR, REPO_ROOT, resolve_executable, terraform_output


PUBLIC_API_STATE = REPO_ROOT / ".local" / "public-shipments-mcp-api.json"


def registration_state_path(runtime: str) -> Path:
    return agent365_workspace(runtime) / "byo.public-shipments.json"


def first_value(payload: dict[str, Any]) -> dict[str, Any] | None:
    values = payload.get("value", [])
    return values[0] if values else None


def application_by_display_name(graph: GraphClient, display_name: str) -> dict[str, Any]:
    app = find_application_by_display_name(graph, display_name)
    if not app:
        raise KeyError(f"Entra application '{display_name}' was not found.")
    return app


def find_application_by_display_name(graph: GraphClient, display_name: str) -> dict[str, Any] | None:
    encoded_filter = urllib.parse.quote(f"displayName eq '{display_name}'", safe="'")
    return first_value(
        graph.request(
            "GET",
            f"/applications?$filter={encoded_filter}&$select=id,appId,displayName,requiredResourceAccess,isFallbackPublicClient",
        )
    )


def catalog_server_available(server_name: str) -> bool:
    result = subprocess.run(
        [resolve_executable("a365"), "develop", "list-available"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and re.search(
        rf"^\s*{re.escape(server_name)}\s*$",
        result.stdout,
        flags=re.MULTILINE,
    ) is not None


def runtime_outputs(runtime: str) -> dict[str, Any]:
    outputs = terraform_output(APPS_DIR)
    if outputs.get("agent_runtime") != runtime:
        raise RuntimeError(
            f"Terraform apps workspace contains {outputs.get('agent_runtime')!r}; "
            f"select autopilot-{runtime} before registration."
        )
    return outputs


def ensure_service_principal(graph: GraphClient, app_id: str) -> dict[str, Any]:
    encoded_filter = urllib.parse.quote(f"appId eq '{app_id}'", safe="'")
    service_principal = first_value(
        graph.request(
            "GET",
            f"/servicePrincipals?$filter={encoded_filter}&$select=id,appId,displayName,oauth2PermissionScopes",
        )
    )
    if service_principal:
        return service_principal
    created = graph.request("POST", "/servicePrincipals", body={"appId": app_id})
    return graph.request(
        "GET",
        f"/servicePrincipals/{created['id']}?$select=id,appId,displayName,oauth2PermissionScopes",
    )


def ensure_all_principals_grant(
    graph: GraphClient,
    *,
    client_service_principal_id: str,
    resource_service_principal_id: str,
    scopes: list[str],
) -> None:
    encoded_filter = urllib.parse.quote(f"clientId eq '{client_service_principal_id}'", safe="'")
    grants = graph.request("GET", f"/oauth2PermissionGrants?$filter={encoded_filter}").get("value", [])
    existing = next(
        (
            grant
            for grant in grants
            if grant.get("resourceId") == resource_service_principal_id
            and grant.get("consentType") == "AllPrincipals"
        ),
        None,
    )
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
            "clientId": client_service_principal_id,
            "consentType": "AllPrincipals",
            "principalId": None,
            "resourceId": resource_service_principal_id,
            "scope": " ".join(sorted(requested)),
        },
    )


def ensure_user_assignment(
    graph: GraphClient,
    *,
    service_principal_id: str,
    user_id: str,
) -> None:
    assignments = graph.request(
        "GET",
        f"/servicePrincipals/{service_principal_id}/appRoleAssignedTo",
    ).get("value", [])
    if any(assignment.get("principalId") == user_id for assignment in assignments):
        return
    graph.request(
        "POST",
        f"/servicePrincipals/{service_principal_id}/appRoleAssignedTo",
        body={
            "principalId": user_id,
            "resourceId": service_principal_id,
            "appRoleId": "00000000-0000-0000-0000-000000000000",
        },
    )


def grant_backing_app_permissions(graph: GraphClient, server_name: str) -> dict[str, Any]:
    display_names = [
        f"{server_name} - BYO",
        f"{server_name}-A365Proxy",
        f"{server_name}-RemoteProxy",
        f"{server_name}-PublicClients",
    ]
    granted: list[dict[str, Any]] = []
    current_user_id = str(graph.request("GET", "/me?$select=id")["id"])
    for display_name in display_names:
        app = application_by_display_name(graph, display_name)
        if display_name.endswith("-PublicClients") and app.get("isFallbackPublicClient") is not True:
            graph.request(
                "PATCH",
                f"/applications/{app['id']}",
                body={"isFallbackPublicClient": True},
                empty_ok=True,
            )
        client_sp = ensure_service_principal(graph, str(app["appId"]))
        ensure_user_assignment(
            graph,
            service_principal_id=str(client_sp["id"]),
            user_id=current_user_id,
        )
        for required in app.get("requiredResourceAccess", []):
            resource_app_id = str(required["resourceAppId"])
            resource_sp = ensure_service_principal(graph, resource_app_id)
            scope_by_id = {
                str(scope["id"]): str(scope["value"])
                for scope in resource_sp.get("oauth2PermissionScopes", [])
                if scope.get("isEnabled", True) and scope.get("value")
            }
            scopes = [
                scope_by_id[str(access["id"])]
                for access in required.get("resourceAccess", [])
                if access.get("type") == "Scope" and str(access["id"]) in scope_by_id
            ]
            if not scopes:
                continue
            ensure_all_principals_grant(
                graph,
                client_service_principal_id=str(client_sp["id"]),
                resource_service_principal_id=str(resource_sp["id"]),
                scopes=scopes,
            )
            granted.append(
                {
                    "client": display_name,
                    "resource": resource_sp.get("displayName") or resource_app_id,
                    "scopes": scopes,
                }
            )
    return {"serverName": server_name, "grants": granted}


def main() -> None:
    parser = argparse.ArgumentParser(description="Register the public shipments server as an Agent 365 BYO MCP server.")
    parser.add_argument("--runtime", choices=["openclaw", "hermes"], required=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--repair-consent", action="store_true")
    parser.add_argument("--mark-approved", action="store_true")
    args = parser.parse_args()

    registration_path = registration_state_path(args.runtime)
    if args.mark_approved:
        state = load_json(registration_path)
        state["status"] = "Approved"
        state["approvedAt"] = datetime.now(timezone.utc).isoformat()
        state["next"] = "Use a supported BYO MCP client surface and verify Defender ExecuteToolByGateway telemetry."
        write_json(registration_path, state)
        print(json.dumps(state, indent=2))
        return
    if args.repair_consent:
        result = grant_backing_app_permissions(GraphClient.from_az_cli(), "ext_Shipments")
        if registration_path.exists():
            state = load_json(registration_path)
            state["adminConsentGrants"] = result["grants"]
            write_json(registration_path, state)
        print(json.dumps(result, indent=2))
        return
    if registration_path.exists() and not args.force and not args.dry_run:
        state = load_json(registration_path)
        if catalog_server_available("ext_Shipments") and state.get("status") != "Approved":
            state["status"] = "Approved"
            state["approvedAt"] = datetime.now(timezone.utc).isoformat()
            write_json(registration_path, state)
        print(json.dumps(state, indent=2))
        return

    outputs = runtime_outputs(args.runtime)
    public_api = load_json(PUBLIC_API_STATE)
    delegated_scope = f"{public_api['audience']}/{public_api['delegatedScope']}"
    if not args.dry_run:
        graph = GraphClient.from_az_cli()
        existing_byo = find_application_by_display_name(graph, "ext_Shipments - BYO")
        if existing_byo:
            if args.force:
                raise RuntimeError(
                    "ext_Shipments backing applications already exist. "
                    "Use --repair-consent instead of forcing a duplicate registration."
                )
            consent = grant_backing_app_permissions(graph, "ext_Shipments")
            state = {
                "runtime": args.runtime,
                "serverName": "ext_Shipments",
                "serverUrl": outputs["public_shipments_mcp_url"],
                "authType": "EntraOAuth",
                "remoteScope": delegated_scope,
                "status": "Approved" if catalog_server_available("ext_Shipments") else "PendingApproval",
                "recoveredAt": datetime.now(timezone.utc).isoformat(),
                "adminConsentGrants": consent["grants"],
                "next": "Approve the pending request if it is not already available in the Agent 365 catalog.",
            }
            write_json(registration_path, state)
            print(json.dumps(state, indent=2))
            return
    request_path = agent365_workspace(args.runtime) / "byo.public-shipments.request.json"
    write_json(
        request_path,
        {
            "serverName": "ext_Shipments",
            "serverUrl": outputs["public_shipments_mcp_url"],
            "authType": "EntraOAuth",
            "description": "Public shipment tracking demo governed through Microsoft Agent 365.",
            "publisherName": "Autopilots on Azure",
            "tools": [
                {
                    "name": "list_demo_shipments",
                    "description": "List the mock shipment tracking IDs available in the demo.",
                },
                {
                    "name": "get_shipment_status",
                    "description": "Get the current public status of one mock shipment by tracking ID.",
                },
            ],
            "remoteScopes": delegated_scope,
            "externalOAuth": None,
            "apiKey": None,
        },
    )
    command = [
        resolve_executable("a365"),
        "develop-mcp",
        "register-external-mcp-server",
        "--input-file",
        str(request_path),
        "--secret-lifetime-months",
        "6",
    ]
    if args.dry_run:
        command.append("--dry-run")

    result = subprocess.run(command, input="y\n", capture_output=True, text=True, check=False)
    combined = f"{result.stdout}\n{result.stderr}"
    if (
        result.returncode != 0
        or "ERROR:" in combined
        or "registration cancelled" in combined.lower()
    ):
        message = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"Agent 365 BYO MCP registration failed: {message}")
    if args.dry_run:
        print(result.stdout)
        return

    consent = grant_backing_app_permissions(GraphClient.from_az_cli(), "ext_Shipments")

    state = {
        "runtime": args.runtime,
        "serverName": "ext_Shipments",
        "serverUrl": outputs["public_shipments_mcp_url"],
        "authType": "EntraOAuth",
        "remoteScope": delegated_scope,
        "registeredAt": datetime.now(timezone.utc).isoformat(),
        "status": "PendingApproval",
        "adminConsentGrants": consent["grants"],
        "next": "Approve the pending tool request in Microsoft 365 admin center under Agents > Tools > Requests.",
    }
    write_json(registration_path, state)
    print(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()
