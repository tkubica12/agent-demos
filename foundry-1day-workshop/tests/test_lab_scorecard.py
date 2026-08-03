"""A model that did not answer must never be reported as meeting the contract.

Calls that never return are excluded from both sides of the pass ratio, so comparing
passes to completions alone reads an unanswered model as ``0/0`` and a clean sweep. The
attendee uses this row to choose a model, and the guide teaches that silence about the
task is itself evidence, so the verdict has to say so.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "teacher" / "demos" / "build-host-agent" / "harness"
sys.path.insert(0, str(SCRIPTS))

import lab  # noqa: E402


def row(trials: int, completed: int, passed: int) -> dict:
    return {"trials": trials, "completed": completed, "contract_passed": passed}


def test_every_trial_answered_and_passed_meets_the_contract() -> None:
    assert lab._verdict(row(trials=2, completed=2, passed=2)) == "meets contract"


def test_an_answered_trial_that_fails_a_criterion_is_flagged() -> None:
    assert lab._verdict(row(trials=2, completed=2, passed=1)) == "FLAGGED"
    assert lab._verdict(row(trials=2, completed=2, passed=0)) == "FLAGGED"


def test_a_model_that_never_answered_is_not_a_pass() -> None:
    verdict = lab._verdict(row(trials=2, completed=0, passed=0))
    assert verdict == "NO ANSWER"
    assert "meets contract" not in verdict


def test_a_partially_answered_model_is_not_a_pass() -> None:
    verdict = lab._verdict(row(trials=2, completed=1, passed=1))
    assert verdict == "INCOMPLETE (1 unanswered)"
    assert "meets contract" not in verdict


@pytest.mark.parametrize(
    ("trials", "completed", "passed"),
    [(2, 0, 0), (2, 1, 1), (3, 1, 1), (3, 2, 2), (4, 0, 0)],
)
def test_no_unanswered_trial_can_ever_read_as_meeting_the_contract(
    trials: int, completed: int, passed: int
) -> None:
    assert lab._verdict(row(trials, completed, passed)) != "meets contract"


def test_the_scorecard_prints_the_same_verdict_it_computes(capsys) -> None:
    rows = [
        {
            "slug": "model-a",
            "vendor": "Vendor A",
            "trials": 2,
            "completed": 0,
            "contract_passed": 0,
            "p50_seconds": None,
            "failure_detail": [],
        },
        {
            "slug": "model-b",
            "vendor": "Vendor B",
            "trials": 2,
            "completed": 2,
            "contract_passed": 2,
            "p50_seconds": 3.1,
            "failure_detail": [],
        },
    ]
    lab._print_scorecard(rows)
    out = capsys.readouterr().out
    assert "NO ANSWER" in out
    assert "meets contract" in out
    model_a_line = next(line for line in out.splitlines() if line.startswith("model-a"))
    assert "meets contract" not in model_a_line
