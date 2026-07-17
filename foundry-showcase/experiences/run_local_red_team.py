from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from azure.ai.evaluation.red_team import AttackStrategy, RedTeam, RiskCategory
from azure.identity import DefaultAzureCredential


RISK_CATEGORIES = {
    item.value: item
    for item in (
        RiskCategory.HateUnfairness,
        RiskCategory.Violence,
        RiskCategory.Sexual,
        RiskCategory.SelfHarm,
        RiskCategory.ProtectedMaterial,
        RiskCategory.CodeVulnerability,
        RiskCategory.UngroundedAttributes,
    )
}


def extract_response_text(payload: dict[str, Any]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text:
        return output_text

    parts: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
    return "\n".join(parts)


def as_json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return as_json(value.model_dump(mode="json"))
    if hasattr(value, "as_dict"):
        return as_json(value.as_dict())
    if isinstance(value, dict):
        return {key: as_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [as_json(item) for item in value]
    return value


def validate_agent_route(
    client: httpx.Client,
    endpoint: str,
    agent_name: str,
    agent_version: str,
    api_version: str,
    token: str,
) -> None:
    response = client.get(
        f"{endpoint}/agents/{agent_name}?api-version={api_version}",
        headers={"Authorization": f"Bearer {token}"},
    )
    response.raise_for_status()
    payload = response.json()
    latest_version = str(payload.get("versions", {}).get("latest", {}).get("version"))
    rules = (
        payload.get("agent_endpoint", {})
        .get("version_selector", {})
        .get("version_selection_rules", [])
    )
    valid_route = (
        len(rules) == 1
        and rules[0].get("agent_version") in {"@latest", agent_version}
        and rules[0].get("traffic_percentage") == 100
    )
    if latest_version != agent_version or not valid_route:
        raise RuntimeError(
            f"Agent endpoint is not exclusively routed to version {agent_version}."
        )


async def run(args: argparse.Namespace) -> None:
    endpoint = args.project_endpoint.rstrip("/")
    responses_url = (
        f"{endpoint}/agents/{args.agent_name}/endpoint/protocols/openai/responses"
        f"?api-version={args.api_version}"
    )
    credential = DefaultAzureCredential()
    args.out.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=httpx.Timeout(args.request_timeout)) as client:
        route_token = credential.get_token("https://ai.azure.com/.default")
        validate_agent_route(
            client,
            endpoint,
            args.agent_name,
            args.agent_version,
            args.api_version,
            route_token.token,
        )

        def invoke_agent(query: str) -> str:
            access_token = credential.get_token("https://ai.azure.com/.default")
            response = client.post(
                responses_url,
                headers={"Authorization": f"Bearer {access_token.token}"},
                json={"input": query},
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("status") == "failed":
                raise RuntimeError(
                    f"Hosted Agent response failed: {json.dumps(payload.get('error'))}"
                )
            text = extract_response_text(payload)
            if not text:
                raise RuntimeError("Hosted Agent response did not include output text.")
            return text

        red_team = RedTeam(
            azure_ai_project=endpoint,
            credential=credential,
            risk_categories=[
                RISK_CATEGORIES[category] for category in args.risk_categories
            ],
            num_objectives=args.objectives,
            application_scenario=(
                "A governed support-operations agent that reads support cases, "
                "proposes changes, uses private memory, retrieves knowledge, and "
                "requires explicit approval before consequential writes."
            ),
            output_dir=str(args.out),
        )
        try:
            result = await red_team.scan(
                target=invoke_agent,
                scan_name=(
                    f"{args.agent_name}-v{args.agent_version}-local-"
                    f"{datetime.now(UTC):%Y%m%d-%H%M}"
                ),
                attack_strategies=[
                    AttackStrategy.Tense,
                ],
                skip_upload=False,
                output_path=args.out,
                parallel_execution=True,
                max_parallel_tasks=args.max_parallel,
                timeout=args.scan_timeout,
            )
        finally:
            credential.close()

    if not result.attack_details:
        raise RuntimeError("Local red-team scan completed without any attack results.")
    attack_details = as_json(result.attack_details)
    callback_errors = [
        message["content"]
        for detail in attack_details
        for message in detail.get("conversation", [])
        if message.get("role") == "assistant"
        and str(message.get("content", "")).startswith("Something went wrong")
    ]
    if callback_errors:
        raise RuntimeError(
            f"{len(callback_errors)} red-team attacks used callback error placeholders."
        )
    summary = {
        "agent": {
            "name": args.agent_name,
            "version": args.agent_version,
            "endpoint": responses_url,
        },
        "scorecard": as_json(result.to_scorecard()),
        "attacks": len(result.attack_details or []),
        "artifacts": str(args.out),
    }
    print(json.dumps(summary, indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run a local AI red-team scan against a real Foundry Hosted Agent "
            "and upload the evaluation result to the Foundry project."
        )
    )
    parser.add_argument(
        "--project-endpoint",
        default=os.getenv("FOUNDRY_PROJECT_ENDPOINT"),
    )
    parser.add_argument("--agent-name", default="foundry-showcase-main")
    parser.add_argument("--agent-version", default="27")
    parser.add_argument("--api-version", default="2025-11-15-preview")
    parser.add_argument(
        "--risk-categories",
        nargs="+",
        choices=sorted(RISK_CATEGORIES),
        default=[
            RiskCategory.ProtectedMaterial.value,
            RiskCategory.CodeVulnerability.value,
            RiskCategory.UngroundedAttributes.value,
        ],
    )
    parser.add_argument("--objectives", type=int, default=1)
    parser.add_argument("--max-parallel", type=int, default=4)
    parser.add_argument("--request-timeout", type=float, default=180.0)
    parser.add_argument("--scan-timeout", type=int, default=3600)
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Directory for local red-team artifacts.",
    )
    args = parser.parse_args()
    if not args.project_endpoint:
        parser.error("--project-endpoint or FOUNDRY_PROJECT_ENDPOINT is required.")
    if args.objectives < 1:
        parser.error("--objectives must be at least 1.")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
