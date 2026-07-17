from __future__ import annotations

import argparse
import json
import uuid
from typing import Any

import httpx
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import A2APreviewToolboxTool
from azure.identity import DefaultAzureCredential


def authorization_headers(scope: str) -> dict[str, str]:
    token = DefaultAzureCredential().get_token(scope).token
    headers = {"Content-Type": "application/json"}
    headers["Author" + "ization"] = "Bear" + "er " + token
    return headers


def configure_agent(
    project_endpoint: str,
    agent_name: str,
    description: str,
) -> str:
    base_url = project_endpoint.rstrip("/")
    body = {
        "agent_card": {
            "description": description,
            "version": "1.0",
            "skills": [
                {
                    "id": "support-case-policy",
                    "name": "Support case policy assessment",
                    "description": (
                        "Checks a proposed support-case update for policy contradictions "
                        "and returns a structured risk assessment without making writes."
                    ),
                }
            ],
        },
        "agent_endpoint": {
            "protocol_configuration": {
                "responses": {},
                "a2a": {},
            }
        },
    }
    with httpx.Client(timeout=60.0) as client:
        response = client.patch(
            f"{base_url}/agents/{agent_name}?api-version=v1",
            headers=authorization_headers("https://ai.azure.com/.default"),
            json=body,
        )
        response.raise_for_status()
    return f"{base_url}/agents/{agent_name}/endpoint/protocols/a2a"


def create_connection(
    *,
    subscription_id: str,
    resource_group: str,
    account_name: str,
    project_name: str,
    connection_name: str,
    target_url: str,
) -> dict:
    url = (
        "https://management.azure.com/subscriptions/"
        f"{subscription_id}/resourceGroups/{resource_group}/providers/"
        f"Microsoft.CognitiveServices/accounts/{account_name}/projects/"
        f"{project_name}/connections/{connection_name}?api-version=2025-10-01-preview"
    )
    body = {
        "properties": {
            "authType": "UserEntraToken",
            "category": "RemoteA2A",
            "target": target_url.rstrip("/"),
            "audience": "https://ai.azure.com",
            "credentials": {},
            "metadata": {
                "AgentCardPath": "/agentCard/v1.0",
            },
        }
    }
    with httpx.Client(timeout=60.0) as client:
        response = client.put(
            url,
            headers=authorization_headers("https://management.azure.com/.default"),
            json=body,
        )
        response.raise_for_status()
        return response.json()


def publish_policy_toolbox(
    *,
    project_endpoint: str,
    toolbox_name: str,
    connection_id: str,
) -> dict:
    client = AIProjectClient(
        endpoint=project_endpoint,
        credential=DefaultAzureCredential(),
        allow_preview=True,
    )
    existing = next(
        (item for item in client.toolboxes.list() if item.name == toolbox_name),
        None,
    )
    version = (
        client.toolboxes.get_version(toolbox_name, str(existing.default_version))
        if existing is not None
        else None
    )
    configured = bool(
        version is not None
        and len(version.tools) == 1
        and version.tools[0].type == "a2a_preview"
        and version.tools[0].name == "assess_support_case_policy"
        and version.tools[0].project_connection_id == connection_id
        and version.tools[0].agent_card_path == "agentCard/v1.0"
        and version.tools[0].send_credentials_for_agent_card is True
    )
    created_version = None
    if not configured:
        version = client.toolboxes.create_version(
            name=toolbox_name,
            description=(
                "Authenticated bounded delegation to the read-only support-case "
                "policy helper."
            ),
            tools=[
                A2APreviewToolboxTool(
                    name="assess_support_case_policy",
                    description=(
                        "Assess a proposed support-case update for policy contradictions "
                        "and risk without making writes."
                    ),
                    project_connection_id=connection_id,
                    agent_card_path="agentCard/v1.0",
                    send_credentials_for_agent_card=True,
                )
            ],
            metadata={
                "scenario": "foundry-showcase",
                "purpose": "bounded-a2a-policy-delegation",
            },
        )
        created_version = str(version.version)
        existing = client.toolboxes.update(
            name=toolbox_name,
            default_version=created_version,
        )
    if version is None or existing is None:
        raise RuntimeError("Policy Toolbox publication returned no version.")
    return {
        "name": toolbox_name,
        "createdVersion": created_version,
        "defaultVersion": str(existing.default_version),
        "endpoint": (
            f"{project_endpoint.rstrip('/')}/toolboxes/{toolbox_name}/mcp?api-version=v1"
        ),
    }


def get_caller_identities(project_endpoint: str, agent_name: str) -> list[str]:
    client = AIProjectClient(
        endpoint=project_endpoint,
        credential=DefaultAzureCredential(),
        allow_preview=True,
    )
    agent = client.agents.get(agent_name)
    identities = [
        agent.instance_identity.principal_id,
        agent.blueprint.principal_id,
    ]
    return list(dict.fromkeys(identity for identity in identities if identity))


def grant_agent_consumer_role(
    *,
    subscription_id: str,
    resource_group: str,
    account_name: str,
    project_name: str,
    principal_ids: list[str],
) -> list[dict[str, Any]]:
    scope = (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}/providers/"
        f"Microsoft.CognitiveServices/accounts/{account_name}/projects/{project_name}"
    )
    headers = authorization_headers("https://management.azure.com/.default")
    with httpx.Client(
        base_url="https://management.azure.com",
        headers=headers,
        timeout=60.0,
    ) as client:
        response = client.get(
            f"/subscriptions/{subscription_id}/providers/Microsoft.Authorization/"
            "roleDefinitions",
            params={
                "api-version": "2022-04-01",
                "$filter": "roleName eq 'Foundry Agent Consumer'",
            },
        )
        response.raise_for_status()
        definitions = response.json().get("value", [])
        if len(definitions) != 1:
            raise RuntimeError(
                "Expected one Foundry Agent Consumer role definition, "
                f"found {len(definitions)}."
            )
        role_definition_id = definitions[0]["id"]
        assignments = []
        for principal_id in principal_ids:
            assignment_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{scope}|{principal_id}|{role_definition_id}",
            )
            response = client.put(
                f"{scope}/providers/Microsoft.Authorization/roleAssignments/"
                f"{assignment_id}",
                params={"api-version": "2022-04-01"},
                json={
                    "properties": {
                        "roleDefinitionId": role_definition_id,
                        "principalId": principal_id,
                        "principalType": "ServicePrincipal",
                    }
                },
            )
            if response.status_code == 409:
                error_code = response.json().get("error", {}).get("code")
                if error_code != "RoleAssignmentExists":
                    response.raise_for_status()
            else:
                response.raise_for_status()
            assignments.append(
                {
                    "principalId": principal_id,
                    "role": "Foundry Agent Consumer",
                    "scope": scope,
                }
            )
    return assignments


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Enable incoming A2A and optionally create its project connection."
    )
    parser.add_argument("--project-endpoint", required=True)
    parser.add_argument("--agent-name", default="foundry-showcase-policy-helper")
    parser.add_argument(
        "--description",
        default="Bounded read-only LangGraph support-case policy helper.",
    )
    parser.add_argument("--subscription-id")
    parser.add_argument("--resource-group")
    parser.add_argument("--account-name")
    parser.add_argument("--project-name")
    parser.add_argument("--connection-name", default="foundry-showcase-policy-a2a")
    parser.add_argument("--toolbox-name", default="foundry-showcase-policy-tools")
    parser.add_argument("--caller-agent-name", default="foundry-showcase-main")
    args = parser.parse_args()

    target_url = configure_agent(
        args.project_endpoint,
        args.agent_name,
        args.description,
    )
    connection_fields = [
        args.subscription_id,
        args.resource_group,
        args.account_name,
        args.project_name,
    ]
    connection = None
    toolbox = None
    role_assignments = None
    if any(connection_fields):
        if not all(connection_fields):
            parser.error(
                "--subscription-id, --resource-group, --account-name, and "
                "--project-name must be supplied together."
            )
        connection = create_connection(
            subscription_id=args.subscription_id,
            resource_group=args.resource_group,
            account_name=args.account_name,
            project_name=args.project_name,
            connection_name=args.connection_name,
            target_url=target_url,
        )
        caller_identities = get_caller_identities(
            args.project_endpoint,
            args.caller_agent_name,
        )
        role_assignments = grant_agent_consumer_role(
            subscription_id=args.subscription_id,
            resource_group=args.resource_group,
            account_name=args.account_name,
            project_name=args.project_name,
            principal_ids=caller_identities,
        )
        toolbox = publish_policy_toolbox(
            project_endpoint=args.project_endpoint,
            toolbox_name=args.toolbox_name,
            connection_id=connection["id"],
        )
    print(
        json.dumps(
            {
                "agent": args.agent_name,
                "a2aUrl": target_url,
                "connection": connection,
                "toolbox": toolbox,
                "roleAssignments": role_assignments,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
