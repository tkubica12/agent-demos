from __future__ import annotations

import os

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential


DEFAULT_MODEL_DEPLOYMENT = "gpt-5.4-mini"


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} must be set.")
    return value


def create_agent() -> Agent:
    client = FoundryChatClient(
        project_endpoint=required_env("FOUNDRY_PROJECT_ENDPOINT"),
        model=os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", DEFAULT_MODEL_DEPLOYMENT),
        credential=DefaultAzureCredential(),
    )

    return Agent(
        client=client,
        name="FoundryResponsesBaseline",
        instructions=(
            "You are a concise helpful assistant running as a Microsoft Foundry "
            "Hosted Agent Responses baseline."
        ),
        default_options={"store": False},
    )


if __name__ == "__main__":
    ResponsesHostServer(create_agent()).run()
