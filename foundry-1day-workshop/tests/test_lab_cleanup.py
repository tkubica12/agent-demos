"""Cleanup must be idempotent and must only claim a failure when one exists.

An attendee reaches cleanup at the end of a timed lab, sometimes twice, and sometimes
after deleting an agent by hand in the portal. An agent that is already gone satisfies
the goal of the command, so it must not be reported as retained and must not fail the
run. A nonzero exit here sends a facilitator hunting for resources that do not exist.
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from azure.core.exceptions import ResourceNotFoundError

SCRIPTS = Path(__file__).resolve().parents[1] / "teacher" / "demos" / "build-host-agent" / "harness"
sys.path.insert(0, str(SCRIPTS))

import lab  # noqa: E402


class FakeAgents:
    def __init__(self, existing: set[str], broken: dict[str, Exception] | None = None) -> None:
        self.existing = set(existing)
        self.broken = broken or {}
        self.attempted: list[str] = []

    def delete(self, name: str) -> None:
        self.attempted.append(name)
        if name in self.broken:
            raise self.broken[name]
        if name not in self.existing:
            raise ResourceNotFoundError("Agent not found")
        self.existing.remove(name)


class FakeProject:
    def __init__(self, agents: FakeAgents) -> None:
        self.agents = agents


@pytest.fixture
def cleanup_run(tmp_path, monkeypatch):
    """Run cmd_cleanup against a fake project and return its exit code and output."""

    def run(created: list[str], existing: set[str], broken: dict[str, Exception] | None = None):
        card = tmp_path / "card.json"
        card.write_text(
            json.dumps(
                {
                    "run_id": "abc123",
                    "seat_id": "seat-001",
                    "sections": {"guardrail": {"agents_created": created}},
                }
            ),
            encoding="utf-8",
        )
        agents = FakeAgents(existing, broken)

        @contextmanager
        def fake_client(*_args, **_kwargs):
            yield FakeProject(agents)

        monkeypatch.setattr(lab, "project_client", fake_client)
        monkeypatch.setattr(lab, "resolve", lambda _args: object())
        args = argparse.Namespace(card=str(card), tenant=None)
        code = lab.cmd_cleanup(args)
        return code, agents

    return run


def test_deleting_agents_that_exist_reports_success(cleanup_run, capsys):
    code, agents = cleanup_run(["agent-a", "agent-b"], {"agent-a", "agent-b"})
    out = capsys.readouterr().out
    assert code == 0
    assert "deleted agent-a" in out
    assert "deleted agent-b" in out
    assert "NOT deleted" not in out
    assert agents.existing == set()


def test_an_agent_that_is_already_gone_is_not_a_failure(cleanup_run, capsys):
    code, _ = cleanup_run(["agent-a"], set())
    out = capsys.readouterr().out
    assert code == 0, "an absent agent already satisfies the goal of cleanup"
    assert "already gone agent-a" in out
    assert "NOT deleted" not in out


def test_running_cleanup_twice_is_safe(cleanup_run, capsys):
    first, _ = cleanup_run(["agent-a", "agent-b"], {"agent-a", "agent-b"})
    capsys.readouterr()
    second, _ = cleanup_run(["agent-a", "agent-b"], set())
    out = capsys.readouterr().out
    assert (first, second) == (0, 0)
    assert out.count("already gone") == 2
    assert "Your project is clean" in out


def test_a_real_failure_still_fails_loudly(cleanup_run, capsys):
    code, _ = cleanup_run(
        ["agent-a", "agent-b"],
        {"agent-a", "agent-b"},
        broken={"agent-b": PermissionError("denied")},
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "deleted agent-a" in out
    assert "NOT deleted agent-b" in out
    assert "1 agent(s) still exist and need manual removal." in out


def test_every_recorded_agent_is_attempted_even_after_one_fails(cleanup_run):
    _, agents = cleanup_run(
        ["agent-a", "agent-b", "agent-c"],
        {"agent-a", "agent-b", "agent-c"},
        broken={"agent-a": PermissionError("denied")},
    )
    assert agents.attempted == ["agent-a", "agent-b", "agent-c"]
    assert agents.existing == {"agent-a"}
