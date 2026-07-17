from __future__ import annotations

import argparse
import json
import os
from typing import Any

import httpx
from azure.identity import DefaultAzureCredential


PROMPTS = (
    "Summarize the support case lifecycle in three concise steps.",
    "Explain why a write proposal should be separated from its confirmation.",
    "Give a calm one-sentence status update for a delayed support case.",
    "List two reasons to use managed identity for an agent tool.",
    "Explain the purpose of a read-only policy helper.",
    "Describe one benefit of trace-based evaluation data.",
    "State one privacy rule for long-term agent memory.",
    "Explain why immutable agent versions help with canary validation.",
    "Describe the difference between a Toolbox skill and an MCP tool.",
    "Give a short example of refusing an unsupported operational request.",
    "Explain why a human approval checkpoint should be durable.",
    "Describe one signal that an agent response is well grounded.",
)


def account_name_from_project_endpoint(project_endpoint: str) -> str:
    host = project_endpoint.split("://", maxsplit=1)[-1].split("/", maxsplit=1)[0]
    suffix = ".services.ai.azure.com"
    if not host.endswith(suffix):
        raise ValueError("The project endpoint does not use the expected Foundry host.")
    return host[: -len(suffix)]


def completion_id(payload: dict[str, Any]) -> str:
    value = payload.get("id")
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"Stored completion response did not contain an id: {payload}")
    return value


def delete_existing_showcase_completions(
    client: httpx.Client,
    endpoint: str,
    headers: dict[str, str],
    user: str,
) -> list[str]:
    matching_ids: list[str] = []
    after: str | None = None
    while True:
        params: dict[str, str | int] = {"limit": 100, "order": "asc"}
        if after:
            params["after"] = after
        response = client.get(endpoint, headers=headers, params=params)
        response.raise_for_status()
        payload = response.json()
        for item in payload.get("data", []):
            metadata = item.get("metadata", {})
            if (
                metadata.get("showcase") == "foundry-showcase"
                and metadata.get("user") == user
            ):
                matching_ids.append(completion_id(item))
        if not payload.get("has_more"):
            break
        after = payload.get("last_id")
        if not isinstance(after, str) or not after:
            raise RuntimeError(
                "Stored completion list indicated another page without last_id."
            )
    for item_id in matching_ids:
        delete_response = client.delete(
            f"{endpoint}/{item_id}",
            headers=headers,
        )
        delete_response.raise_for_status()
    return matching_ids


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-endpoint",
        default=os.getenv("FOUNDRY_PROJECT_ENDPOINT"),
    )
    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument("--user", default="tomas@tomasonline.net")
    args = parser.parse_args()
    if not args.project_endpoint:
        parser.error("--project-endpoint or FOUNDRY_PROJECT_ENDPOINT is required.")

    account_name = account_name_from_project_endpoint(args.project_endpoint)
    endpoint = f"https://{account_name}.openai.azure.com/openai/v1/chat/completions"
    token = DefaultAzureCredential().get_token("https://ai.azure.com/.default").token
    headers = {"Authorization": f"Bearer {token}"}
    completion_ids: list[str] = []

    with httpx.Client(timeout=120) as client:
        deleted_ids = delete_existing_showcase_completions(
            client,
            endpoint,
            headers,
            args.user,
        )
        for index, prompt in enumerate(PROMPTS, start=1):
            response = client.post(
                endpoint,
                headers=headers,
                json={
                    "model": args.model,
                    "store": True,
                    "metadata": {
                        "showcase": "foundry-showcase",
                        "user": args.user,
                        "example": str(index),
                    },
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a concise support-operations assistant. "
                                "Use no more than 80 words."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                },
            )
            response.raise_for_status()
            completion_ids.append(completion_id(response.json()))

    print(
        json.dumps(
            {
                "count": len(completion_ids),
                "model": args.model,
                "user": args.user,
                "deletedCompletionIds": deleted_ids,
                "completionIds": completion_ids,
                "retentionDays": 30,
                "retiresOn": "2026-10-15",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
