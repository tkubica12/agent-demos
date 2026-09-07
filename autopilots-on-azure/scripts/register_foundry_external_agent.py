"""Link Sandbox telemetry to Foundry, without provisioning Foundry compute."""

from __future__ import annotations

import argparse
import json
import os
import re
from typing import Any
from urllib.parse import urlsplit

from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.identity import AzureCliCredential, ManagedIdentityCredential

DESCRIPTION = (
    "Hermes running in Azure Container Apps Sandbox. External registration links "
    "metadata-only OpenTelemetry traces; Foundry does not host or invoke this runtime."
)


def validate_project_endpoint(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or not (parsed.hostname or "").endswith(".services.ai.azure.com")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or parsed.query
        or parsed.fragment
        or not re.fullmatch(r"/api/projects/[A-Za-z0-9_.-]+/?", parsed.path)
    ):
        raise ValueError(
            "Use an explicit HTTPS Foundry project endpoint: "
            "https://<account>.services.ai.azure.com/api/projects/<project>."
        )
    return endpoint.rstrip("/")


def external_definition(otel_agent_id: str):
    from azure.ai.projects.models import ExternalAgentDefinition

    return ExternalAgentDefinition(otel_agent_id=otel_agent_id)


def ensure_registration(
    client: Any,
    *,
    agent_name: str,
    otel_agent_id: str,
    apply: bool = False,
) -> dict[str, str]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", agent_name):
        raise ValueError("Foundry agent name must contain only letters, digits, hyphens, or underscores.")
    if not otel_agent_id or len(otel_agent_id) > 256 or any(
        character.isspace() or not character.isprintable() for character in otel_agent_id
    ):
        raise ValueError("OTEL_AGENT_ID must be a nonempty identifier without whitespace, at most 256 characters.")
    current = None
    try:
        current = client.agents.get(agent_name=agent_name)
    except ResourceNotFoundError:
        pass
    if current is not None:
        definition = current.versions.latest.definition
        if definition.kind != "external":
            raise ValueError("An agent with this name already exists and is not external.")
        if (definition.otel_agent_id or current.name) == otel_agent_id:
            return {"name": agent_name, "otelAgentId": otel_agent_id, "status": "unchanged"}
    action = "update" if current is not None else "create"
    if not apply:
        return {"name": agent_name, "otelAgentId": otel_agent_id, "status": f"would-{action}"}
    client.agents.create_version(
        agent_name=agent_name,
        definition=external_definition(otel_agent_id),
        description=DESCRIPTION,
        metadata={"hosting": "aca-sandbox", "scenario": "autopilots-on-azure"},
    )
    registered = client.agents.get(agent_name=agent_name)
    definition = registered.versions.latest.definition
    if (
        registered.name != agent_name
        or definition.kind != "external"
        or definition.otel_agent_id != otel_agent_id
    ):
        raise ValueError("Foundry read-back did not match the requested external registration.")
    return {"name": agent_name, "otelAgentId": otel_agent_id, "status": f"{action}d"}


def register(
    *,
    project_endpoint: str,
    agent_name: str,
    otel_agent_id: str,
    auth: str = "azure-cli",
    managed_identity_client_id: str | None = None,
    apply: bool = False,
) -> dict[str, str]:
    endpoint = validate_project_endpoint(project_endpoint)
    if auth not in {"azure-cli", "managed-identity"}:
        raise ValueError("Choose azure-cli or managed-identity authentication.")
    if auth != "managed-identity" and managed_identity_client_id:
        raise ValueError("--managed-identity-client-id requires --auth managed-identity.")
    from azure.ai.projects import AIProjectClient

    credential = (
        ManagedIdentityCredential(client_id=managed_identity_client_id)
        if auth == "managed-identity"
        else AzureCliCredential(process_timeout=120)
    )
    with credential, AIProjectClient(
        endpoint=endpoint, credential=credential, allow_preview=True
    ) as client:
        return ensure_registration(
            client, agent_name=agent_name, otel_agent_id=otel_agent_id, apply=apply
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-endpoint",
        default=os.getenv("FOUNDRY_PROJECT_ENDPOINT", ""),
        help="https://<account>.services.ai.azure.com/api/projects/<project>",
    )
    parser.add_argument("--agent-name", default=os.getenv("FOUNDRY_AGENT_NAME", "autopilots-hermes"))
    parser.add_argument("--otel-agent-id", default=None)
    parser.add_argument("--auth", choices=["azure-cli", "managed-identity"], default="azure-cli")
    parser.add_argument("--managed-identity-client-id")
    parser.add_argument("--apply", action="store_true", help="Apply registration changes; otherwise inspect only.")
    args = parser.parse_args()
    try:
        result = register(
            project_endpoint=args.project_endpoint,
            agent_name=args.agent_name,
            otel_agent_id=args.otel_agent_id or os.getenv("OTEL_AGENT_ID") or args.agent_name,
            auth=args.auth,
            managed_identity_client_id=args.managed_identity_client_id,
            apply=args.apply,
        )
    except (AzureError, ValueError, OSError) as exc:
        result = {
            "status": "error",
            "type": type(exc).__name__,
            "message": "Foundry registration failed; check the project endpoint, selected identity, and permissions.",
        }
        if status := getattr(exc, "status_code", None):
            result["httpStatus"] = status
        print(json.dumps(result))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
