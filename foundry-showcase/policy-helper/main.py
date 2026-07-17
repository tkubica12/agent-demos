from __future__ import annotations

import json
import os
from typing import Literal

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langchain_azure_ai.agents.hosting import ResponsesHostServer
from pydantic import BaseModel


AI_SCOPE = "https://ai.azure.com/.default"
DEFAULT_MODEL = "gpt-5.4-mini"
VALID_STATUSES = {"open", "pending_customer", "escalated", "resolved"}
VALID_PRIORITIES = {"low", "medium", "high", "critical"}


class PolicyAssessment(BaseModel):
    decision: Literal["allow", "review", "deny"]
    risk: Literal["low", "medium", "high"]
    contradictions: list[str]
    rationale: str


def assess_policy(
    *,
    current_status: str,
    current_priority: str,
    proposed_status: str = "",
    proposed_owner: str = "",
    proposed_priority: str = "",
    proposed_resolution_note: str = "",
) -> PolicyAssessment:
    contradictions: list[str] = []
    if current_status not in VALID_STATUSES:
        contradictions.append(f"Current status '{current_status}' is unsupported.")
    if current_priority not in VALID_PRIORITIES:
        contradictions.append(f"Current priority '{current_priority}' is unsupported.")
    if proposed_status and proposed_status not in VALID_STATUSES:
        contradictions.append(f"Proposed status '{proposed_status}' is unsupported.")
    if proposed_priority and proposed_priority not in VALID_PRIORITIES:
        contradictions.append(f"Proposed priority '{proposed_priority}' is unsupported.")
    if proposed_status == "resolved" and not proposed_resolution_note.strip():
        contradictions.append("Resolving a case requires a non-empty resolution note.")
    if current_status == "resolved" and proposed_status == "open":
        contradictions.append("A resolved case cannot be reopened through the standard workflow.")
    if proposed_owner and proposed_status == "resolved":
        contradictions.append("Ownership should not change in the same update that resolves a case.")

    if contradictions:
        return PolicyAssessment(
            decision="deny",
            risk="high",
            contradictions=contradictions,
            rationale="The proposed update conflicts with support-case policy.",
        )
    if proposed_status in {"escalated", "resolved"} or proposed_priority == "critical":
        return PolicyAssessment(
            decision="review",
            risk="high",
            contradictions=[],
            rationale="The update is policy-valid but changes a high-impact lifecycle or priority field.",
        )
    if proposed_owner or proposed_status or proposed_priority:
        return PolicyAssessment(
            decision="review",
            risk="medium",
            contradictions=[],
            rationale="The update is policy-valid and changes operational case routing.",
        )
    return PolicyAssessment(
        decision="allow",
        risk="low",
        contradictions=[],
        rationale="The update is policy-valid and changes only descriptive case content.",
    )


@tool
def evaluate_support_case_policy(
    current_status: str,
    current_priority: str,
    proposed_status: str = "",
    proposed_owner: str = "",
    proposed_priority: str = "",
    proposed_resolution_note: str = "",
) -> str:
    """Evaluate a proposed support-case update for policy contradictions and risk."""
    return assess_policy(
        current_status=current_status,
        current_priority=current_priority,
        proposed_status=proposed_status,
        proposed_owner=proposed_owner,
        proposed_priority=proposed_priority,
        proposed_resolution_note=proposed_resolution_note,
    ).model_dump_json()


def build_chat_model() -> ChatOpenAI:
    project_endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"].rstrip("/")
    credential = DefaultAzureCredential()
    project = AIProjectClient(endpoint=project_endpoint, credential=credential)
    openai_client = project.get_openai_client()
    token_provider = get_bearer_token_provider(credential, AI_SCOPE)
    return ChatOpenAI(
        model=os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", DEFAULT_MODEL),
        base_url=str(openai_client.base_url),
        api_key=token_provider,
    )


def create_graph():
    return create_agent(
        build_chat_model(),
        tools=[evaluate_support_case_policy],
        system_prompt=(
            "You are a bounded, read-only support-case policy helper. "
            "You have no write tools. For every policy request, call "
            "evaluate_support_case_policy exactly once. Return only the JSON object "
            "from the tool, without markdown or extra commentary. Do not invent case facts. "
            "If required current or proposed fields are missing, ask for them instead of guessing."
        ),
    )


def main() -> None:
    port = int(os.getenv("PORT", "8088"))
    ResponsesHostServer(create_graph()).run(port=port)


if __name__ == "__main__":
    main()
