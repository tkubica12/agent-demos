"""Synthetic stock-exception agent hosted through the Foundry Responses protocol."""

from __future__ import annotations

import json
import os
from typing import Annotated

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from langchain_azure_ai.agents.hosting import ResponsesHostServer

load_dotenv()

AZURE_AI_SCOPE = "https://ai.azure.com/.default"
AGENT_NAME = "foundryws-chapter2-langgraph"
SYSTEM_PROMPT = """
You triage synthetic retail stock exceptions for an operations team.
Never recommend product substitutions, promise pricing or refunds, or claim access to live systems.
When the user provides current stock, reserved stock, and forecast demand, always call
calculate_shortfall before answering. Return concise operational guidance with exactly
these headings: PRIORITY, NEXT ACTION, ESCALATE WHEN.
""".strip()


@tool
def calculate_shortfall(
    current_stock: Annotated[int, "Synthetic units currently on hand."],
    reserved_stock: Annotated[int, "Synthetic units already reserved."],
    forecast_demand: Annotated[int, "Synthetic units expected during the planning window."],
) -> str:
    """Calculate available stock and the forecast shortfall for a synthetic exception."""
    values = (current_stock, reserved_stock, forecast_demand)
    if any(value < 0 for value in values):
        raise ValueError("stock and demand values must be non-negative")
    available = max(current_stock - reserved_stock, 0)
    return json.dumps(
        {
            "available_stock": available,
            "forecast_demand": forecast_demand,
            "shortfall": max(forecast_demand - available, 0),
        },
        sort_keys=True,
    )


def build_chat_model() -> ChatOpenAI:
    """Create a LangChain chat model backed by the current Foundry project."""
    endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"].rstrip("/")
    model = os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"]
    credential = DefaultAzureCredential()
    project = AIProjectClient(endpoint=endpoint, credential=credential)
    openai_client = project.get_openai_client()
    token_provider = get_bearer_token_provider(credential, AZURE_AI_SCOPE)

    return ChatOpenAI(
        model=model,
        base_url=str(openai_client.base_url),
        api_key=token_provider,
        use_responses_api=True,
        output_version="responses/v1",
    )


def build_graph():
    """Compile the framework-owned orchestration graph."""
    return create_agent(
        build_chat_model(),
        tools=[calculate_shortfall],
        system_prompt=SYSTEM_PROMPT,
        name=AGENT_NAME,
    )


def main() -> None:
    port = int(os.environ.get("PORT", "8088"))
    ResponsesHostServer(build_graph()).run(port=port)


if __name__ == "__main__":
    main()
