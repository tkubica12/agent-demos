from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main


class FakeRunner:
    home = ".hermes-test"
    executable = "hermes"

    async def run_turn(self, prompt: str, session_id: str | None = None):
        if not prompt.strip():
            raise ValueError("Prompt must not be empty.")
        return main.HermesResult(
            text=f"Hermes saw: {prompt} / session={session_id}",
            returncode=0,
            duration_ms=1,
        )


@pytest.fixture(autouse=True)
def fake_runner(monkeypatch):
    monkeypatch.setattr(main, "runner", FakeRunner())


def test_health():
    client = TestClient(main.app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_invocations_extracts_task_and_session():
    client = TestClient(main.app)
    response = client.post(
        "/invocations",
        json={"task": "do real work", "session_id": "s1", "metadata": {"a": "b"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["output"] == "Hermes saw: do real work / session=s1"
    assert body["metadata"] == {"a": "b"}


def test_invocations_accepts_raw_string_body():
    client = TestClient(main.app)
    response = client.post("/invocations", content="raw hosted message")
    assert response.status_code == 200
    assert response.json()["output"] == "Hermes saw: raw hosted message / session=None"


def test_responses_returns_openai_style_output():
    client = TestClient(main.app)
    response = client.post(
        "/responses",
        json={
            "conversation_id": "c1",
            "input": [{"type": "input_text", "text": "hello"}],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "response"
    assert body["status"] == "completed"
    assert body["conversation"] == "c1"
    assert body["output_text"] == "Hermes saw: hello / session=c1"
    assert body["output"][0]["content"][0]["text"] == "Hermes saw: hello / session=c1"


def test_responses_accepts_conversation_object():
    client = TestClient(main.app)
    response = client.post(
        "/responses",
        json={
            "conversation": {"id": "conv1"},
            "input": "hello",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["conversation"] == "conv1"
    assert body["output_text"] == "Hermes saw: hello / session=conv1"
    assert body["output"][0]["content"][0]["text"] == "Hermes saw: hello / session=conv1"


def test_responses_extracts_portal_message_shape():
    client = TestClient(main.app)
    response = client.post(
        "/responses",
        json={
            "conversation": {"id": "conv1"},
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "hello portal"}],
                }
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["output_text"] == "Hermes saw: hello portal / session=conv1"


def test_responses_streams_sse_when_requested():
    client = TestClient(main.app)
    response = client.post(
        "/responses",
        json={"input": "hello", "stream": True},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: response.output_text.delta" in response.text
    assert "Hermes saw: hello / session=None" in response.text
    assert "data: [DONE]" in response.text


def test_empty_prompt_is_bad_request():
    client = TestClient(main.app)
    response = client.post("/invocations", json={"task": ""})
    assert response.status_code == 400


def test_clean_hermes_output_removes_cli_scaffolding():
    output = """
Query: Say hello.
Initializing agent...
────────────────
╭─ ⚕ Hermes ─────────────────────────╮
    Hello from Hermes Agent, running on Azure Foundry!
╰────────────────────────────────────╯

Resume this session with:
  hermes --resume 20260619_130525_9c47dc

Session:        20260619_130525_9c47dc
Duration:       25s
Messages:       2 (1 user, 0 tool calls)
"""
    assert (
        main.HermesRunner._clean_hermes_output(output)
        == "Hello from Hermes Agent, running on Azure Foundry!"
    )


def test_clean_hermes_output_removes_hosted_setup_logs():
    output = """
⚠ tirith security scanner enabled but not available — command scanning will use pattern matching only
→ Checking Node.js (for browser tools)...
→ Node.js not found — installing Node.js 22 LTS...
Downloading Chrome 150.0.7871.24 for linux64
https://storage.googleapis.com/chrome-for-testing-public/150.0.7871.24/linux64/chrome-linux64.zip
Location: /home/session/.agent-browser/browsers/chrome-150.0.7871.24
Note: If you see "shared library" errors when running, use:
agent-browser install --with-deps
hosted invocation ok
"""
    assert main.HermesRunner._clean_hermes_output(output) == "hosted invocation ok"
