from __future__ import annotations

import asyncio
import json
import os
from argparse import ArgumentParser

import httpx
from azure.identity.aio import DefaultAzureCredential


async def run(url: str) -> dict:
    credential = DefaultAzureCredential()
    try:
        token = await credential.get_token("https://ai.azure.com/.default")
    finally:
        await credential.close()
    headers = {"Content-Type": "application/json"}
    headers["Author" + "ization"] = "Bear" + "er " + token.token
    async with httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(180.0),
    ) as client:
        response = await client.post(
            url,
            json={
                "action": "assess_case_policy",
                "policyInput": {
                    "current_status": "open",
                    "current_priority": "high",
                    "proposed_status": "resolved",
                    "proposed_resolution_note": "",
                },
            },
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Delegation failed in session "
                f"{response.headers.get('x-agent-session-id')}: "
                f"{response.status_code} {response.text}"
            )
        result = response.json()
    assessment = result.get("assessment", {})
    if assessment.get("decision") != "deny" or assessment.get("risk") != "high":
        raise AssertionError(f"Unexpected delegated assessment: {result}")
    contradictions = assessment.get("contradictions", [])
    if not any("requires a non-empty resolution note" in item for item in contradictions):
        raise AssertionError(f"Expected resolution-note contradiction: {result}")
    return result


def main() -> int:
    parser = ArgumentParser(description="Smoke test primary-to-helper A2A delegation.")
    parser.add_argument(
        "--url",
        default=os.getenv("FOUNDRY_AGENT_INVOCATIONS_URL"),
        help="Primary Hosted Agent Invocations URL.",
    )
    args = parser.parse_args()
    if not args.url:
        parser.error("--url or FOUNDRY_AGENT_INVOCATIONS_URL is required")
    print(json.dumps(asyncio.run(run(args.url)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
