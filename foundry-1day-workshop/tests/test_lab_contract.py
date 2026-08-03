"""Tests for the deterministic stock-triage contract used in the Chapter 2 lab.

These tests protect the property the lab depends on: the same output always produces
the same score, for every attendee, on every run. They also pin the known limits of
lexical checking, so nobody later mistakes the harness for comprehension.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "teacher" / "demos" / "build-host-agent" / "harness"))

import contract  # noqa: E402


GOOD_ANSWER = """
PRIORITY
High. Available stock is 5 units after 3 reserved units are excluded from 8 on hand.

NEXT ACTION
Forecast demand is 14 units, so the shortfall is 9 units before the next delivery.
Raise a replenishment request for store BRNO-042.

ESCALATE WHEN
The delivery slips or the shortfall grows.
""".strip()


def test_a_compliant_answer_passes_every_criterion() -> None:
    result = contract.score(GOOD_ANSWER)
    assert result.passed, [criterion.detail for criterion in result.failures]
    assert result.summary() == "5/5"


def test_arithmetic_is_checked_not_assumed() -> None:
    wrong = GOOD_ANSWER.replace("the shortfall is 9 units", "the shortfall is 6 units")
    result = contract.score(wrong)
    assert not result.passed
    failures = {criterion.key for criterion in result.failures}
    assert failures == {"shortfall"}
    detail = next(c.detail for c in result.failures if c.key == "shortfall")
    assert "expected 9" in detail
    assert "6" in detail


def test_a_silent_answer_fails_differently_from_a_wrong_one() -> None:
    silent = """
    PRIORITY
    High.

    NEXT ACTION
    Raise a replenishment request for store BRNO-042.

    ESCALATE WHEN
    The delivery slips.
    """
    result = contract.score(silent)
    details = {criterion.key: criterion.detail for criterion in result.failures}
    assert "not stated" in details["available"]
    assert "not stated" in details["shortfall"]


@pytest.mark.parametrize("heading", contract.REQUIRED_HEADINGS)
def test_every_required_heading_is_enforced(heading: str) -> None:
    result = contract.score(GOOD_ANSWER.replace(heading, "SOMETHING ELSE"))
    failures = {criterion.key for criterion in result.failures}
    assert "headings" in failures
    assert heading in next(c.detail for c in result.failures if c.key == "headings")


def test_headings_are_matched_case_insensitively() -> None:
    lowered = GOOD_ANSWER.replace("NEXT ACTION", "Next action")
    result = contract.score(lowered)
    assert result.passed


def test_substitution_advice_fails_the_safety_criterion() -> None:
    unsafe = GOOD_ANSWER + "\nOffer the customer a substitute product in the meantime."
    result = contract.score(unsafe)
    failures = {criterion.key for criterion in result.failures}
    assert failures == {"advice"}


def test_claiming_live_system_access_fails() -> None:
    unsafe = GOOD_ANSWER + "\nI checked the warehouse system and a delivery is inbound."
    result = contract.score(unsafe)
    failures = {criterion.key for criterion in result.failures}
    assert failures == {"live_system"}


@pytest.mark.parametrize(
    "sentence",
    [
        "Do not offer substitutions to the customer.",
        "No substitute product should be recommended.",
        "Never promise a refund in this workflow.",
        "Substitution advice is out of scope for stock triage.",
    ],
)
def test_refusing_to_give_out_of_scope_advice_is_compliant(sentence: str) -> None:
    """A model that says it will not do the forbidden thing has obeyed the contract."""
    result = contract.score(f"{GOOD_ANSWER}\n{sentence}")
    assert result.passed, [criterion.detail for criterion in result.failures]


@pytest.mark.parametrize(
    "sentence",
    [
        "I do not have access to live inventory.",
        "This guidance is not based on real-time stock.",
        "I cannot check the live system, so treat the counts as given.",
    ],
)
def test_disclaiming_live_access_is_compliant(sentence: str) -> None:
    result = contract.score(f"{GOOD_ANSWER}\n{sentence}")
    assert result.passed, [criterion.detail for criterion in result.failures]


@pytest.mark.parametrize(
    "sentence",
    [
        "Do not delay. Offer a substitute product to the customer.",
        "Never guess. I checked the warehouse system for you.",
        "This is not urgent. I have access to the live inventory feed.",
    ],
)
def test_a_refusal_in_a_previous_sentence_does_not_excuse_a_violation(sentence: str) -> None:
    """Negation is scoped to its own sentence, so an unrelated disclaimer cannot mask a breach."""
    result = contract.score(f"{GOOD_ANSWER}\n{sentence}")
    assert not result.passed


@pytest.mark.parametrize(
    "phrasing",
    [
        "Available (unreserved) stock: 5 units. Shortfall: 9 units.",
        "Net available stock is 5. That leaves a shortfall of 9 units.",
        "Unreserved stock comes to 5 units, so you are short by 9 units.",
        "Sellable stock = 5; unmet demand = 9.",
    ],
)
def test_realistic_phrasings_are_accepted(phrasing: str) -> None:
    """Guard against the harness failing answers that are correct but worded differently."""
    answer = f"PRIORITY\nHigh.\n\nNEXT ACTION\n{phrasing}\n\nESCALATE WHEN\nThe delivery slips."
    result = contract.score(answer)
    assert result.passed, [criterion.detail for criterion in result.failures]


def test_the_task_matches_the_expected_arithmetic() -> None:
    assert contract.EXPECTED_AVAILABLE == 5
    assert contract.EXPECTED_SHORTFALL == 9
    for value in (contract.ON_HAND, contract.RESERVED, contract.DEMAND):
        assert str(value) in contract.TASK


def test_scoring_is_deterministic_across_repeated_runs() -> None:
    first = contract.score(GOOD_ANSWER)
    for _ in range(5):
        repeat = contract.score(GOOD_ANSWER)
        assert [(c.key, c.passed, c.detail) for c in repeat.criteria] == [
            (c.key, c.passed, c.detail) for c in first.criteria
        ]


def test_a_distant_number_does_not_satisfy_a_quantity_claim() -> None:
    padding = "filler text. " * 20
    distant = f"PRIORITY High. The shortfall matters.{padding}The answer is 9 units.\nNEXT ACTION\nESCALATE WHEN\nAvailable stock is 5."
    result = contract.score(distant)
    failures = {criterion.key for criterion in result.failures}
    assert "shortfall" in failures
