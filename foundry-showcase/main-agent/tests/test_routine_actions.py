from __future__ import annotations

import pytest

from case_workflow import MCPCaseTools
from main import build_follow_up, build_quality_digest, parse_invocation_payload


def test_parse_invocation_payload_accepts_routine_json_string() -> None:
    assert parse_invocation_payload(
        '{"action":"case_follow_up_reminder","caseId":"CASE-1001"}'
    ) == {
        "action": "case_follow_up_reminder",
        "caseId": "CASE-1001",
    }


@pytest.mark.parametrize(
    "value",
    [
        {"input": {"action": "daily_support_quality_review"}},
        {"input": '{"action":"daily_support_quality_review"}'},
    ],
)
def test_parse_invocation_payload_unwraps_routine_input(value: object) -> None:
    assert parse_invocation_payload(value) == {
        "action": "daily_support_quality_review"
    }


def test_parse_invocation_payload_preserves_plain_message() -> None:
    assert parse_invocation_payload("hello") == {"message": "hello"}


def test_parse_invocation_payload_rejects_other_shapes() -> None:
    with pytest.raises(ValueError, match="object"):
        parse_invocation_payload(["not", "supported"])


def test_quality_digest_summarizes_unresolved_cases() -> None:
    digest = build_quality_digest(
        [
            {
                "case_id": "CASE-1",
                "title": "First",
                "status": "open",
                "priority": "high",
                "owner": "Avery",
                "updated_at": "2026-01-01T00:00:00Z",
            },
            {
                "case_id": "CASE-2",
                "title": "Second",
                "status": "escalated",
                "priority": "high",
                "owner": "Jordan",
                "updated_at": "2026-01-02T00:00:00Z",
            },
        ]
    )

    assert digest["unresolvedCount"] == 2
    assert digest["priorityCounts"] == {"high": 2}
    assert digest["ownerCounts"] == {"Avery": 1, "Jordan": 1}
    assert [case["case_id"] for case in digest["cases"]] == ["CASE-1", "CASE-2"]


def test_follow_up_is_status_specific() -> None:
    follow_up = build_follow_up(
        {
            "case_id": "CASE-1001",
            "status": "pending_customer",
            "priority": "medium",
            "owner": "Avery",
        }
    )

    assert follow_up["caseId"] == "CASE-1001"
    assert "Contact the customer" in follow_up["recommendation"]


@pytest.mark.asyncio
async def test_mcp_search_unwraps_structured_list_result() -> None:
    class Content:
        text = '{"result":[{"case_id":"CASE-1001","status":"open"}]}'

    class Function:
        name = "case-read___search_cases"
        additional_properties = {}

        async def invoke(self, *, arguments: dict[str, object]) -> list[Content]:
            assert arguments["limit"] == 50
            return [Content()]

    class Toolbox:
        functions = [Function()]

        async def connect(self) -> None:
            return None

    cases = await MCPCaseTools(Toolbox()).search_cases(limit=50)

    assert cases == [{"case_id": "CASE-1001", "status": "open"}]
