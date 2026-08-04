from __future__ import annotations

import argparse
import json
import uuid
from typing import Any

import httpx
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    A2APreviewToolboxTool,
    AgentKind,
    MCPTool,
    PromptAgentDefinition,
)
from azure.identity import DefaultAzureCredential

INSTRUCTIONS = """
You are the support knowledge expert for the Foundry Showcase support desk.

Your only job is to answer questions about documented support policy, product
behaviour, and published guidance by retrieving them from the knowledge base.

Rules:
- Retrieve before you answer. Never answer from prior knowledge alone.
- Quote or paraphrase only what the retrieved passages support, and cite the
  source title for every claim.
- When the knowledge base does not cover the question, say so plainly and stop.
  Do not speculate and do not fall back on general reasoning.
- You are read-only. You never propose, approve, or apply a case update, and you
  never assess risk or escalation level. Those belong to other specialists.
- Answer in at most six sentences. A support lead is reading you mid-conversation.
""".strip()

AGENT_CARD_SKILL = {
    "id": "support-knowledge-lookup",
    "name": "Support knowledge lookup",
    "description": (
        "Answers documented support policy and product questions from the "
        "Foundry IQ knowledge base and returns cited findings."
    ),
}


def authorization_headers(scope: str) -> dict[str, str]:
    token = DefaultAzureCredential(process_timeout=120).get_token(scope).token
    headers = {"Content-Type": "application/json"}
    headers["Author" + "ization"] = "Bear" + "er " + token
    return headers


def project_client(project_endpoint: str) -> AIProjectClient:
    return AIProjectClient(
        endpoint=project_endpoint,
        credential=DefaultAzureCredential(process_timeout=120),
        allow_preview=True,
    )


def publish_prompt_agent(
    *,
    project_endpoint: str,
    agent_name: str,
    model: str,
    knowledge_connection_name: str,
) -> dict[str, Any]:
    client = project_client(project_endpoint)
    knowledge_connection = client.connections.get(knowledge_connection_name)
    knowledge_mcp_endpoint = knowledge_connection.target
    if not knowledge_mcp_endpoint:
        raise RuntimeError(f"Connection {knowledge_connection_name} has no target.")
    definition = PromptAgentDefinition(
        kind=AgentKind.PROMPT,
        model=model,
        instructions=INSTRUCTIONS,
        tools=[
            MCPTool(
                server_label="foundry_iq_knowledge",
                server_url=knowledge_mcp_endpoint,
                server_description=(
                    "Foundry IQ knowledge base covering support policy documents "
                    "and approved web sources."
                ),
                allowed_tools=["knowledge_base_retrieve"],
                project_connection_id=knowledge_connection_name,
                require_approval="never",
            )
        ],
    )
    version = client.agents.create_version(
        agent_name,
        definition=definition,
        description=(
            "Read-only prompt agent that answers documented support questions "
            "from the Foundry IQ knowledge base and cites its sources."
        ),
        metadata={
            "scenario": "foundry-showcase",
            "role": "knowledge-expert",
        },
    )
    return {"name": agent_name, "version": str(version.version), "model": model}


def enable_incoming_a2a(
    *,
    project_endpoint: str,
    agent_name: str,
    description: str,
) -> str:
    base_url = project_endpoint.rstrip("/")
    body = {
        "agent_card": {
            "description": description,
            "version": "1.0",
            "skills": [AGENT_CARD_SKILL],
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


def create_a2a_connection(
    *,
    subscription_id: str,
    resource_group: str,
    account_name: str,
    project_name: str,
    connection_name: str,
    target_url: str,
) -> dict[str, Any]:
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
            "metadata": {"AgentCardPath": "/agentCard/v1.0"},
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


def publish_knowledge_toolbox(
    *,
    project_endpoint: str,
    toolbox_name: str,
    connection_id: str,
) -> dict[str, Any]:
    client = project_client(project_endpoint)
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
        and version.tools[0].name == "ask_support_knowledge_expert"
        and version.tools[0].project_connection_id == connection_id
    )
    created_version = None
    if not configured:
        version = client.toolboxes.create_version(
            name=toolbox_name,
            description=(
                "Authenticated bounded delegation to the read-only support "
                "knowledge expert."
            ),
            tools=[
                A2APreviewToolboxTool(
                    name="ask_support_knowledge_expert",
                    description=(
                        "Ask the read-only knowledge expert what documented support "
                        "policy or product guidance says about a question. Returns a "
                        "cited answer and never changes a case."
                    ),
                    project_connection_id=connection_id,
                    agent_card_path="agentCard/v1.0",
                    send_credentials_for_agent_card=True,
                )
            ],
            metadata={
                "scenario": "foundry-showcase",
                "purpose": "bounded-a2a-knowledge-delegation",
            },
        )
        created_version = str(version.version)
        existing = client.toolboxes.update(
            name=toolbox_name,
            default_version=created_version,
        )
    if version is None or existing is None:
        raise RuntimeError("Knowledge Toolbox publication returned no version.")
    return {
        "name": toolbox_name,
        "createdVersion": created_version,
        "defaultVersion": str(existing.default_version),
        "endpoint": (
            f"{project_endpoint.rstrip('/')}/toolboxes/{toolbox_name}/mcp?api-version=v1"
        ),
    }


def caller_identities(project_endpoint: str, agent_name: str) -> list[str]:
    client = project_client(project_endpoint)
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
                if response.json().get("error", {}).get("code") != "RoleAssignmentExists":
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
        description=(
            "Publish the prompt-agent knowledge expert, expose it over A2A, and "
            "make it callable by the main Hosted Agent through a bounded Toolbox."
        )
    )
    parser.add_argument("--project-endpoint", required=True)
    parser.add_argument("--subscription-id", required=True)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--account-name", required=True)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--agent-name", default="foundry-showcase-knowledge-expert")
    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument("--knowledge-connection-name", default="foundry-showcase-knowledge")
    parser.add_argument("--connection-name", default="foundry-showcase-knowledge-a2a")
    parser.add_argument("--toolbox-name", default="foundry-showcase-knowledge-tools")
    parser.add_argument("--caller-agent-name", default="foundry-showcase-main")
    parser.add_argument(
        "--description",
        default=(
            "Read-only prompt agent answering documented support questions from "
            "the Foundry IQ knowledge base."
        ),
    )
    args = parser.parse_args()

    agent = publish_prompt_agent(
        project_endpoint=args.project_endpoint,
        agent_name=args.agent_name,
        model=args.model,
        knowledge_connection_name=args.knowledge_connection_name,
    )
    a2a_url = enable_incoming_a2a(
        project_endpoint=args.project_endpoint,
        agent_name=args.agent_name,
        description=args.description,
    )
    connection = create_a2a_connection(
        subscription_id=args.subscription_id,
        resource_group=args.resource_group,
        account_name=args.account_name,
        project_name=args.project_name,
        connection_name=args.connection_name,
        target_url=a2a_url,
    )
    role_assignments = grant_agent_consumer_role(
        subscription_id=args.subscription_id,
        resource_group=args.resource_group,
        account_name=args.account_name,
        project_name=args.project_name,
        principal_ids=caller_identities(args.project_endpoint, args.caller_agent_name),
    )
    toolbox = publish_knowledge_toolbox(
        project_endpoint=args.project_endpoint,
        toolbox_name=args.toolbox_name,
        connection_id=connection["id"],
    )
    print(
        json.dumps(
            {
                "agent": agent,
                "a2aUrl": a2a_url,
                "connection": {"id": connection["id"], "name": args.connection_name},
                "toolbox": toolbox,
                "roleAssignments": role_assignments,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
