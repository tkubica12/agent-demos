from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from main import PolicyA2AService


class FakeFunction:
    name = "policy-helper___SendMessage"
    additional_properties = {}

    def __init__(self, assessment: dict) -> None:
        self.assessment = assessment
        self.arguments = None

    async def invoke(self, *, arguments):
        self.arguments = arguments
        task = {
            "kind": "task",
            "status": {"state": "completed"},
            "artifacts": [
                {
                    "parts": [
                        {
                            "kind": "text",
                            "text": json.dumps(self.assessment),
                        }
                    ]
                }
            ],
        }
        return [SimpleNamespace(text=json.dumps(task))]


class FakeToolbox:
    def __init__(self, function: FakeFunction) -> None:
        self.functions = [function]
        self.connected = False

    async def connect(self) -> None:
        self.connected = True


@pytest.mark.asyncio
async def test_policy_service_forwards_exact_json_and_parses_assessment() -> None:
    assessment = {
        "decision": "deny",
        "risk": "high",
        "contradictions": ["Resolution note required."],
        "rationale": "Policy conflict.",
    }
    function = FakeFunction(assessment)
    toolbox = FakeToolbox(function)
    service = PolicyA2AService(toolbox)
    policy_input = {
        "current_priority": "high",
        "current_status": "open",
        "proposed_resolution_note": "",
        "proposed_status": "resolved",
    }

    result = await service.assess(policy_input)

    assert toolbox.connected
    assert result == assessment
    forwarded = function.arguments["message"]["parts"][0]
    assert forwarded["kind"] == "text"
    assert json.loads(forwarded["text"]) == policy_input
