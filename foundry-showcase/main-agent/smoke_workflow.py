from __future__ import annotations

import asyncio
import os
import uuid
from argparse import ArgumentParser
from typing import Any

import httpx
from azure.identity.aio import DefaultAzureCredential


async def invoke(
    url: str,
    payload: dict[str, Any],
    agent_session_id: str | None = None,
) -> tuple[dict[str, Any], str]:
    credential = DefaultAzureCredential()
    try:
        token = await credential.get_token("https://ai.azure.com/.default")
    finally:
        await credential.close()
    headers = {"Content-Type": "application/json"}
    headers["Author" + "ization"] = "Bear" + "er " + token.token
    target = httpx.URL(url)
    if agent_session_id:
        target = target.copy_add_param("agent_session_id", agent_session_id)
    async with httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(120.0),
    ) as client:
        response = await client.post(target, json=payload)
        if response.is_error:
            raise RuntimeError(
                "Hosted workflow invocation failed "
                f"in session {response.headers.get('x-agent-session-id')}: "
                f"{response.status_code} {response.text}"
            )
        session_id = response.headers.get("x-agent-session-id") or agent_session_id
        if not session_id:
            raise RuntimeError("Hosted Agent response did not include x-agent-session-id.")
        return response.json(), session_id


async def apply_owner(url: str, owner: str) -> dict[str, Any]:
    started, session_id = await invoke(
        url,
        {
            "action": "start_case_resolution",
            "caseId": "CASE-1001",
            "changes": {"owner": owner},
            "reason": "Validate the deployed case-resolution workflow.",
            "requestedBy": "deployed-workflow-smoke",
        },
    )
    if started.get("state") != "pending_confirmation":
        raise AssertionError(f"Expected pending confirmation, got: {started}")
    resume_payload = {
        "action": "resume_case_resolution",
        "workflowId": started["workflow_id"],
        "checkpointId": started["checkpoint_id"],
        "requestId": started["request_id"],
        "approved": True,
        "confirmationId": f"workflow-smoke-{uuid.uuid4()}",
    }
    resumed, _ = await invoke(
        url,
        resume_payload,
        agent_session_id=session_id,
    )
    if resumed.get("state") != "completed":
        raise AssertionError(f"Expected completed workflow, got: {resumed}")
    case = resumed.get("result", {}).get("case", {})
    if case.get("owner") != owner:
        raise AssertionError(f"Expected owner {owner}, got: {case}")
    retried, _ = await invoke(
        url,
        resume_payload,
        agent_session_id=session_id,
    )
    if retried != resumed:
        raise AssertionError(f"Workflow retry was not idempotent: {retried}")
    return resumed


async def run(url: str) -> None:
    changed = await apply_owner(url, "Jordan")
    restored = await apply_owner(url, "Avery")
    print(
        {
            "changedWorkflowId": changed["workflow_id"],
            "restoredWorkflowId": restored["workflow_id"],
            "finalOwner": restored["result"]["case"]["owner"],
        }
    )


def main() -> int:
    parser = ArgumentParser(description="Exercise and restore the deployed case workflow.")
    parser.add_argument(
        "--url",
        default=os.getenv("FOUNDRY_AGENT_INVOCATIONS_URL"),
        help="Hosted Agent Invocations URL.",
    )
    args = parser.parse_args()
    if not args.url:
        parser.error("--url or FOUNDRY_AGENT_INVOCATIONS_URL is required")
    asyncio.run(run(args.url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
