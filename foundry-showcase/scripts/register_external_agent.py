"""Register the self-managed Container Apps triage agent as a Foundry external agent."""

from __future__ import annotations

import argparse
import json

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import AgentKind, ExternalAgentDefinition
from azure.identity import DefaultAzureCredential

DESCRIPTION = (
    "Self-managed A2A triage assistant running on Azure Container Apps. Registered "
    "in Foundry for observability and evaluation over its OpenTelemetry spans."
)


def register(
    *,
    project_endpoint: str,
    agent_name: str,
    otel_agent_id: str,
) -> dict[str, str]:
    client = AIProjectClient(
        endpoint=project_endpoint,
        credential=DefaultAzureCredential(process_timeout=120),
        allow_preview=True,
    )
    version = client.agents.create_version(
        agent_name,
        definition=ExternalAgentDefinition(
            kind=AgentKind.EXTERNAL,
            otel_agent_id=otel_agent_id,
        ),
        description=DESCRIPTION,
        metadata={
            "scenario": "foundry-showcase",
            "role": "external-triage",
            "hosting": "azure-container-apps",
        },
    )
    return {
        "name": agent_name,
        "version": str(version.version),
        "otelAgentId": otel_agent_id,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Register the external triage agent into the Foundry project."
    )
    parser.add_argument("--project-endpoint", required=True)
    parser.add_argument("--agent-name", default="foundry-showcase-external-triage")
    parser.add_argument("--otel-agent-id", default="support-triage-assistant")
    args = parser.parse_args()

    print(
        json.dumps(
            register(
                project_endpoint=args.project_endpoint,
                agent_name=args.agent_name,
                otel_agent_id=args.otel_agent_id,
            ),
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
