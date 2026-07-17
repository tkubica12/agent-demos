from __future__ import annotations

import pytest
from azure.data.tables import TableEntity

from case_mcp.repository import InMemoryCaseRepository, _entity_to_proposal


def test_search_and_get_cases() -> None:
    repository = InMemoryCaseRepository()

    matches = repository.search(query="identity", status="open")

    assert [case.case_id for case in matches] == ["CASE-1001"]
    assert repository.get("case-1003").priority == "critical"


def test_propose_then_apply_case_update() -> None:
    repository = InMemoryCaseRepository()
    proposal = repository.propose(
        "CASE-1002",
        {"status": "escalated", "priority": "high"},
        "Customer supplied evidence that increases impact.",
    )

    assert repository.get("CASE-1002").status == "pending_customer"
    updated, applied = repository.apply(proposal.proposal_id, "confirm-1234")

    assert updated.status == "escalated"
    assert updated.priority == "high"
    assert applied.status == "applied"
    assert applied.confirmation_id == "confirm-1234"


def test_apply_rejects_missing_confirmation_and_replay() -> None:
    repository = InMemoryCaseRepository()
    proposal = repository.propose(
        "CASE-1001",
        {"owner": "Taylor"},
        "Move to the identity response owner.",
    )

    with pytest.raises(ValueError, match="at least 8"):
        repository.apply(proposal.proposal_id, "yes")

    repository.apply(proposal.proposal_id, "confirm-5678")
    with pytest.raises(ValueError, match="already applied"):
        repository.apply(proposal.proposal_id, "confirm-9999")


def test_proposal_entity_allows_missing_apply_metadata() -> None:
    proposal = _entity_to_proposal(
        TableEntity(
            {
                "PartitionKey": "CASE-1001",
                "RowKey": "proposal:test",
                "proposal_id": "CASE-1001:test",
                "changes_json": '{"owner":"Taylor"}',
                "reason": "Move to the identity response owner.",
                "status": "pending",
                "created_at": "2026-07-16T00:00:00+00:00",
            }
        )
    )

    assert proposal.applied_at is None
    assert proposal.confirmation_id is None


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({}, "At least one"),
        ({"status": "unknown"}, "Unsupported case status"),
        ({"priority": "urgent"}, "Unsupported case priority"),
        ({"customer": "Other"}, "Unsupported case update fields"),
    ],
)
def test_proposal_validation(changes: dict[str, str], message: str) -> None:
    repository = InMemoryCaseRepository()
    with pytest.raises(ValueError, match=message):
        repository.propose("CASE-1001", changes, "Test validation.")
