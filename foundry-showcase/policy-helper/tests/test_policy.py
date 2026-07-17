from main import assess_policy


def test_resolution_requires_note() -> None:
    result = assess_policy(
        current_status="open",
        current_priority="high",
        proposed_status="resolved",
    )

    assert result.decision == "deny"
    assert result.risk == "high"
    assert "requires a non-empty resolution note" in result.contradictions[0]


def test_high_impact_valid_change_requires_review() -> None:
    result = assess_policy(
        current_status="open",
        current_priority="high",
        proposed_priority="critical",
    )

    assert result.decision == "review"
    assert result.risk == "high"
    assert result.contradictions == []


def test_resolution_note_is_low_risk() -> None:
    result = assess_policy(
        current_status="open",
        current_priority="high",
        proposed_resolution_note="Customer confirmed recovery.",
    )

    assert result.decision == "allow"
    assert result.risk == "low"


def test_reopen_is_denied() -> None:
    result = assess_policy(
        current_status="resolved",
        current_priority="medium",
        proposed_status="open",
    )

    assert result.decision == "deny"
    assert "cannot be reopened" in result.contradictions[0]
