from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime_probe import INSTANCE_ID, SandboxProbe, platform_environment, platform_headers  # noqa: E402

_LAB_PATH = Path(__file__).resolve().parents[2] / "scripts" / "session_scaling_lab.py"
_spec = importlib.util.spec_from_file_location("session_scaling_lab", _LAB_PATH)
lab = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["session_scaling_lab"] = lab
_spec.loader.exec_module(lab)


def test_probe_counts_requests_and_peak_concurrency() -> None:
    probe = SandboxProbe()
    with probe.track():
        with probe.track():
            assert probe.in_flight == 2
    assert probe.in_flight == 0
    assert probe.requests_served == 2
    assert probe.max_in_flight == 2


def test_probe_snapshot_reports_stable_instance_identity(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    probe = SandboxProbe()
    with probe.track():
        pass
    snapshot = probe.snapshot(session_hint="session-a")
    assert snapshot["instanceId"] == INSTANCE_ID
    assert snapshot["requestsServed"] == 1
    assert snapshot["sessionMarker"]["createdByInstanceId"] == INSTANCE_ID
    assert snapshot["sessionMarker"]["resumeCount"] == 0

    second = probe.snapshot(session_hint="session-a")
    assert second["sessionMarker"]["createdByInstanceId"] == INSTANCE_ID


def test_platform_headers_filter_and_redact() -> None:
    headers = {
        "Authorization": "Bearer secret",
        "x-agent-user-id": "alice",
        "x-ms-user-identity": "alice@contoso.com",
        "x-agent-auth-token": "abc",
        "content-type": "application/json",
    }
    collected = platform_headers(headers)
    assert collected["x-agent-user-id"] == "alice"
    assert collected["x-ms-user-identity"] == "alice@contoso.com"
    assert collected["x-agent-auth-token"] == "<redacted>"
    assert "authorization" not in collected
    assert "content-type" not in collected


def test_platform_environment_filters_and_redacts(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_NAME", "foundry-showcase-main")
    monkeypatch.setenv("SESSION_ID", "sess_lab_a")
    monkeypatch.setenv("AGENT_API_KEY", "super-secret")
    monkeypatch.setenv("UNRELATED_SETTING", "value")
    collected = platform_environment()
    assert collected["AGENT_NAME"] == "foundry-showcase-main"
    assert collected["SESSION_ID"] == "sess_lab_a"
    assert collected["AGENT_API_KEY"] == "<redacted>"
    assert "UNRELATED_SETTING" not in collected


def test_session_pool_packs_users_then_grows() -> None:
    pool = lab.SessionPool(max_users_per_session=2, prefix="pool")
    assignments = [pool.session_for(f"user{index}") for index in range(5)]
    assert assignments == ["pool-000", "pool-000", "pool-001", "pool-001", "pool-002"]
    assert pool.session_for("user0") == "pool-000"


def test_assign_sessions_per_mode() -> None:
    users = ["a", "b", "c", "d"]
    shared = lab.assign_sessions("shared", users, "run", 2)
    isolated = lab.assign_sessions("isolated", users, "run", 2)
    pooled = lab.assign_sessions("pooled", users, "run", 2)
    assert len(set(shared.values())) == 1
    assert len(set(isolated.values())) == 4
    assert len(set(pooled.values())) == 2


@pytest.mark.parametrize(
    ("base", "session", "expected_fragment"),
    [
        ("https://host/path?api-version=v1", "s1", "agent_session_id=s1"),
        ("https://host/path", "s2", "agent_session_id=s2"),
    ],
)
def test_session_url_adds_query_parameter(base: str, session: str, expected_fragment: str) -> None:
    url = lab.session_url(base, session)
    assert expected_fragment in url


def test_session_url_preserves_api_version() -> None:
    url = lab.session_url("https://host/path?api-version=v1", "s1")
    assert "api-version=v1" in url


def test_summarize_reports_distinct_sandboxes() -> None:
    turns = [
        lab.TurnResult("shared", "a", "s", 1.0, 200, "inst-1", 1, 1),
        lab.TurnResult("shared", "b", "s", 2.0, 200, "inst-1", 2, 2, throttle_retries=1),
    ]
    summary = lab.summarize("shared", turns, [{"maxInFlight": 2}])
    assert summary["distinctSandboxes"] == 1
    assert summary["sessions"] == 1
    assert summary["maxInFlightPerSandbox"] == 2
    assert summary["failures"] == 0
    assert summary["modelThrottleRetries"] == 1


def test_cost_note_scales_with_sandbox_count() -> None:
    summary = {"distinctSandboxes": 6, "sessions": 6, "wallClockSeconds": 60.0}
    note = lab.cost_note(summary, cpu=0.5, memory_gib=1.0, idle_seconds=900.0)
    assert "6 active sandbox(es)" in note
    assert "96.0 sandbox-minutes" in note


def test_cost_note_reports_savings_from_stopping_sessions() -> None:
    summary = {"distinctSandboxes": 6, "sessions": 6, "wallClockSeconds": 60.0}
    note = lab.cost_note(summary, cpu=0.5, memory_gib=1.0, idle_seconds=900.0)
    assert "6.0 sandbox-minutes" in note


def test_sessions_base_url_derives_management_collection() -> None:
    invocations = (
        "https://host/api/projects/p/agents/a/endpoint/protocols/invocations?api-version=v1"
    )
    assert lab.sessions_base_url(invocations) == (
        "https://host/api/projects/p/agents/a/endpoint/sessions?api-version=v1"
    )
