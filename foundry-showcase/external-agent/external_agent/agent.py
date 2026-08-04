"""Support triage agent: calls gpt-5.4-mini via managed identity and emits OTel traces."""

from __future__ import annotations

import os
from typing import Any

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI
from opentelemetry import trace

AGENT_ID = "support-triage-assistant"
AGENT_NAME = "Support Triage Assistant"
AI_SCOPE = "https://cognitiveservices.azure.com/.default"

SYSTEM_PROMPT = """\
You are a bounded, read-only support triage assistant for a support-operations lead.
Your responsibility is triage guidance only: priority classification, routing rules,
SLA thresholds, escalation criteria, and how to categorize incoming support requests.
You do not assess policy risk on specific case updates (that is a separate helper's role).
You do not read or write case data. Answer in plain prose, concisely.
"""

tracer = trace.get_tracer(AGENT_NAME)


def _build_client() -> AzureOpenAI:
    credential = DefaultAzureCredential()
    token_provider = get_bearer_token_provider(credential, AI_SCOPE)
    return AzureOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        azure_deployment=os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-5.4-mini"),
        azure_ad_token_provider=token_provider,
        api_version="2025-04-01-preview",
    )


def answer_triage_question(question: str) -> dict[str, Any]:
    """Answer a triage question, return text and token counts."""
    client = _build_client()
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-5.4-mini")

    with tracer.start_as_current_span("create_agent") as agent_span:
        agent_span.set_attribute("gen_ai.operation.name", "create_agent")
        agent_span.set_attribute("gen_ai.agent.id", AGENT_ID)
        agent_span.set_attribute("gen_ai.agent.name", AGENT_NAME)

        with tracer.start_as_current_span("chat") as model_span:
            model_span.set_attribute("gen_ai.operation.name", "chat")
            model_span.set_attribute("gen_ai.system", "azure.ai.openai")
            model_span.set_attribute("gen_ai.request.model", deployment)

            response = client.chat.completions.create(
                model=deployment,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": question},
                ],
                max_completion_tokens=1024,
            )

            usage = response.usage
            if usage:
                model_span.set_attribute("gen_ai.usage.input_tokens", usage.prompt_tokens)
                model_span.set_attribute("gen_ai.usage.output_tokens", usage.completion_tokens)

            return {
                "text": response.choices[0].message.content or "",
                "input_tokens": usage.prompt_tokens if usage else 0,
                "output_tokens": usage.completion_tokens if usage else 0,
            }
