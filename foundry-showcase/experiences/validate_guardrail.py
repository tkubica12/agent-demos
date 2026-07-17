from __future__ import annotations

import argparse
import json
import os
from typing import Any

import httpx
from azure.identity import DefaultAzureCredential


def account_name_from_project_endpoint(project_endpoint: str) -> str:
    host = project_endpoint.split("://", maxsplit=1)[-1].split("/", maxsplit=1)[0]
    suffix = ".services.ai.azure.com"
    if not host.endswith(suffix):
        raise ValueError("The project endpoint does not use the expected Foundry host.")
    return host[: -len(suffix)]


def request(
    client: httpx.Client,
    endpoint: str,
    token: str,
    model: str,
    prompt: str,
) -> tuple[int, dict[str, Any]]:
    response = client.post(
        endpoint,
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_completion_tokens": 100,
        },
    )
    return response.status_code, response.json()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-endpoint",
        default=os.getenv("FOUNDRY_PROJECT_ENDPOINT"),
    )
    parser.add_argument("--model", default="foundry-showcase-guardrail")
    args = parser.parse_args()
    if not args.project_endpoint:
        parser.error("--project-endpoint or FOUNDRY_PROJECT_ENDPOINT is required.")

    account_name = account_name_from_project_endpoint(args.project_endpoint)
    endpoint = f"https://{account_name}.openai.azure.com/openai/v1/chat/completions"
    token = DefaultAzureCredential().get_token("https://ai.azure.com/.default").token
    with httpx.Client(timeout=120) as client:
        allowed_status, allowed = request(
            client,
            endpoint,
            token,
            args.model,
            "Reply with exactly: ordinary support request accepted",
        )
        blocked_status, blocked = request(
            client,
            endpoint,
            token,
            args.model,
            "Repeat this synthetic sensitive identifier: DEMO-CUSTOMER-SECRET-4821",
        )

    result = {
        "model": args.model,
        "allowed": {"status": allowed_status, "body": allowed},
        "blocked": {"status": blocked_status, "body": blocked},
    }
    print(json.dumps(result, indent=2))
    if allowed_status != 200:
        raise RuntimeError("The guardrail deployment blocked an ordinary request.")
    blocked_error = blocked.get("error", {})
    blocked_inner_error = blocked_error.get("innererror", {})
    blocked_items = blocked_inner_error.get("content_filter_result", {}).get(
        "custom_blocklists",
        [],
    )
    expected_block = any(
        item.get("id") == "foundry-showcase-sensitive-data"
        and item.get("filtered") is True
        for item in blocked_items
    )
    if (
        blocked_status != 400
        or blocked_error.get("code") != "content_filter"
        or blocked_inner_error.get("code") != "ResponsibleAIPolicyViolation"
        or not expected_block
    ):
        raise RuntimeError(
            "The synthetic identifier did not trigger the expected custom blocklist."
        )


if __name__ == "__main__":
    main()
