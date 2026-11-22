"""Register the Entra APIs of a Worker's role-owned MCP servers and record them for deployment.

Each server gets one Entra application. Its required role goes to the Worker's Agent Identity, so only
this Worker can call the tools. Optional gateway roles go to the gateway managed identity for actions
the agent must not be able to perform itself. Runs with the operator's Azure CLI login.
"""
from __future__ import annotations

import argparse
import json
import tomllib
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

from scripts.provision_agent365_instance import GraphClient
from scripts.setup_agent365 import load_json
from scripts.setup_app_tfvars import runtime_app_tfvars_path, runtime_outputs_path
from scripts.setup_identity import ensure_app_role_assignment, first_value
from scripts.tf_helpers import APPS_DIR, write_tfvars

DEPLOYMENT_KEYS = ("source_directory", "package", "data_directories", "required_role", "environment",
                   "agent_user_token_headers")


def ensure_role_mcp_api(graph: GraphClient, display_name: str, roles: list[str]) -> dict[str, Any]:
    name_filter = urllib.parse.quote(f"displayName eq '{display_name}'", safe="'")
    app = first_value(graph.request("GET", f"/applications?$filter={name_filter}&$select=id,appId,appRoles"))
    if not app:
        app = graph.request("POST", "/applications", body={
            "displayName": display_name,
            "signInAudience": "AzureADMyOrg",
            "api": {"requestedAccessTokenVersion": 2},
            "appRoles": [
                {"allowedMemberTypes": ["Application"], "description": role, "displayName": role,
                 "id": str(uuid.uuid4()), "isEnabled": True, "value": role}
                for role in roles
            ],
        })
        graph.request("PATCH", f"/applications/{app['id']}",
                      body={"identifierUris": [f"api://{app['appId']}"]}, empty_ok=True)
    app_id = str(app["appId"])
    principal_filter = urllib.parse.quote(f"appId eq '{app_id}'", safe="'")
    principal = first_value(graph.request("GET", f"/servicePrincipals?$filter={principal_filter}&$select=id"))
    principal = principal or graph.request("POST", "/servicePrincipals", body={"appId": app_id})
    role_ids = {item["value"]: str(item["id"]) for item in app.get("appRoles", [])}
    if missing := sorted(set(roles) - set(role_ids)):
        raise RuntimeError(f"{display_name} exists without application roles: {', '.join(missing)}.")
    return {"audience": f"api://{app_id}", "servicePrincipalObjectId": str(principal["id"]), "appRoleIds": role_ids}


def main() -> None:
    parser = argparse.ArgumentParser(description="Register role-owned MCP server APIs for one Worker.")
    parser.add_argument("--state-name", default="hermes", help="Worker state directory under .local.")
    parser.add_argument("--config", required=True, type=Path, help="TOML file with one [servers.<name>] table per server.")
    args = parser.parse_args()

    servers = tomllib.loads(args.config.read_text(encoding="utf-8"))["servers"]
    tfvars_path = runtime_app_tfvars_path("hermes", args.state_name)
    tfvars = load_json(tfvars_path)
    outputs = load_json(runtime_outputs_path("hermes", args.state_name))
    agent_identity = tfvars["agent365_agent_identity_object_id"]
    gateway_identity = outputs["sandbox_groups"]["gateway"]["identity_principal_id"]

    graph = GraphClient.from_az_cli()
    deployment: dict[str, dict[str, Any]] = {}
    for name, server in servers.items():
        gateway_roles = server.get("gateway_roles", [])
        api = ensure_role_mcp_api(graph, server["display_name"], [server["required_role"], *gateway_roles])
        for principal_id, role in ((agent_identity, server["required_role"]),
                                   *((gateway_identity, role) for role in gateway_roles)):
            ensure_app_role_assignment(
                graph, principal_id=principal_id, resource_id=api["servicePrincipalObjectId"],
                app_role_id=api["appRoleIds"][role],
            )
        deployment[name] = {
            **{key: server[key] for key in DEPLOYMENT_KEYS if key in server},
            "api_audience": api["audience"],
        }

    tfvars["role_mcp_servers"] = deployment
    for path in (tfvars_path, APPS_DIR / "generated.app.auto.tfvars.json",
                 APPS_DIR / "generated.runtime.auto.tfvars.json"):
        write_tfvars(path, tfvars)
    print(json.dumps({name: server["api_audience"] for name, server in deployment.items()}, indent=2))


if __name__ == "__main__":
    main()
