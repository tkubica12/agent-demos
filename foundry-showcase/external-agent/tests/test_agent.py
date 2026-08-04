"""Unit tests for the external agent service (no Azure calls)."""

from __future__ import annotations

import json
import os
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://fake.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-test")


@pytest.fixture()
def client() -> TestClient:
    with patch("external_agent.telemetry.setup_telemetry"):
        from external_agent.server import app
        return TestClient(app, raise_server_exceptions=False)


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["agent"] == "support-triage-assistant"


def test_agent_card(client: TestClient) -> None:
    resp = client.get("/.well-known/agent-card.json")
    assert resp.status_code == 200
    card = resp.json()
    assert card["name"] == "Support Triage Assistant"
    assert "url" in card
    assert card["version"] == "1.0.0"
    assert len(card["skills"]) == 1
    assert card["skills"][0]["id"] == "support-triage"
    assert card["capabilities"]["streaming"] is False


def test_message_send(client: TestClient) -> None:
    mock_choice = MagicMock()
    mock_choice.message.content = "This is a high priority billing case."
    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 42
    mock_usage.completion_tokens = 18
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage = mock_usage

    with (
        patch("external_agent.agent.DefaultAzureCredential"),
        patch("external_agent.agent.get_bearer_token_provider", return_value="tok"),
        patch("external_agent.agent.AzureOpenAI") as mock_openai,
    ):
        mock_openai.return_value.chat.completions.create.return_value = mock_response
        payload = {
            "jsonrpc": "2.0",
            "method": "message/send",
            "params": {
                "message": {
                    "messageId": str(uuid.uuid4()),
                    "role": "user",
                    "parts": [{"kind": "text", "text": "What priority for a billing issue?"}],
                }
            },
            "id": "t-1",
        }
        resp = client.post("/", json=payload)

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "t-1"
    assert "result" in body
    artifacts = body["result"]["artifacts"]
    assert len(artifacts) == 1
    assert "billing" in artifacts[0]["parts"][0]["text"].lower()


def test_unknown_method(client: TestClient) -> None:
    payload = {"jsonrpc": "2.0", "method": "unknown/method", "params": {}, "id": "t-2"}
    resp = client.post("/", json=payload)
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == -32601


def test_empty_text_returns_error(client: TestClient) -> None:
    payload = {
        "jsonrpc": "2.0",
        "method": "message/send",
        "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": "  "}]}},
        "id": "t-3",
    }
    resp = client.post("/", json=payload)
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["code"] == -32000
