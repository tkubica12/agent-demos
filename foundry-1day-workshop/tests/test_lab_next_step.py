"""What the harness tells you to open next must always exist.

Samples are recorded only for calls that returned, so a model that stayed silent has
nothing to show. Before this was handled, a silent model produced no criterion failures,
so the harness announced that nothing was flagged and then offered to display a result
that was not there. The attendee is reading this line under time pressure in a live room.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "teacher" / "demos" / "build-host-agent" / "harness"
sys.path.insert(0, str(SCRIPTS))

import lab  # noqa: E402

def _row(slug, trials=2, completed=2, passed=2, failures=0, samples=None):
    detail = [{"trial": i + 1, "summary": "criterion failed"} for i in range(failures)]
    if samples is None:
        samples = [{"trial": i + 1, "text": "answer"} for i in range(completed)]
    return {
        "slug": slug,
        "vendor": "V",
        "trials": trials,
        "completed": completed,
        "contract_passed": passed,
        "failure_detail": detail,
        "samples": samples,
        "p50_seconds": 1.0,
    }


def test_a_flagged_model_is_the_one_offered_for_reading():
    lines = lab._next_step([_row("a"), _row("b", passed=1, failures=1)])
    assert "python lab.py show --model b" in "\n".join(lines)


def test_a_silent_model_is_reported_as_a_finding():
    lines = lab._next_step([_row("a", completed=0, passed=0, samples=[]), _row("b")])
    text = "\n".join(lines)
    assert "a did not answer 2 of 2 calls" in text
    assert "Nothing was flagged" not in text


def test_a_silent_model_is_never_offered_for_reading():
    lines = lab._next_step([_row("a", completed=0, passed=0, samples=[]), _row("b")])
    text = "\n".join(lines)
    assert "show --model a" not in text
    assert "python lab.py show --model b" in text


def test_a_partially_silent_model_is_reported_and_still_readable():
    lines = lab._next_step([_row("a", completed=1, passed=1)])
    text = "\n".join(lines)
    assert "a did not answer 1 of 2 calls" in text
    assert "python lab.py show --model a" in text


def test_two_silent_models_offer_nothing_to_open():
    lines = lab._next_step(
        [
            _row("a", completed=0, passed=0, samples=[]),
            _row("b", completed=0, passed=0, samples=[]),
        ]
    )
    text = "\n".join(lines)
    assert "show --model" not in text
    assert "nothing to open" in text
    assert "facilitator" in text


def test_a_clean_run_still_asks_for_one_output_to_be_read():
    lines = lab._next_step([_row("a"), _row("b")])
    text = "\n".join(lines)
    assert "Nothing was flagged" in text
    assert "python lab.py show --model a" in text


def test_show_says_so_when_a_model_left_nothing_to_read(tmp_path, capsys):
    import argparse
    import json

    card = tmp_path / "card.json"
    card.write_text(
        json.dumps(
            {
                "run_id": "r",
                "seat_id": "s",
                "sections": {
                    "model_contract": {
                        "models": [_row("a", completed=0, passed=0, samples=[])]
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    lab.cmd_show(argparse.Namespace(card=str(card), model="a"))
    out = capsys.readouterr().out
    assert "returned no answer to read" in out


def test_show_refuses_a_model_that_was_never_scored(tmp_path):
    import argparse
    import json

    import pytest

    card = tmp_path / "card.json"
    card.write_text(
        json.dumps(
            {
                "run_id": "r",
                "seat_id": "s",
                "sections": {"model_contract": {"models": [_row("a")]}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as error:
        lab.cmd_show(argparse.Namespace(card=str(card), model="nope"))
    assert "no model named nope" in str(error.value)

