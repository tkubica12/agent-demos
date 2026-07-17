from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

import app


def test_latest_user_text_uses_last_user_message() -> None:
    assert (
        app.latest_user_text(
            [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "reply"},
                {"role": "user", "content": "second"},
            ]
        )
        == "second"
    )


def test_agui_streams_foundry_text(monkeypatch) -> None:
    monkeypatch.setenv("BFF_AUTH_MODE", "disabled")
    monkeypatch.setenv("FOUNDRY_AGENT_INVOCATIONS_URL", "https://foundry.test/invocations")

    async def fake_headers(
        user: app.UserContext,
        correlation_id: str,
        user_assertion: str | None,
    ) -> dict[str, str]:
        assert user_assertion is None
        return {
            "Authorization": "Bearer managed-identity-token",
            "x-user-id": user.user_id,
            "x-correlation-id": correlation_id,
        }

    monkeypatch.setattr(app, "foundry_headers", fake_headers)
    def foundry_handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["stream"] is False
        assert payload["message"] == "hello"
        return httpx.Response(200, json={"response": "hello from Foundry"})

    transport = httpx.MockTransport(foundry_handler)
    application = app.create_app(
        client_factory=lambda: httpx.AsyncClient(transport=transport)
    )

    response = TestClient(application).post(
        "/agui",
        headers={"x-correlation-id": "corr-1"},
        json={
            "threadId": "thread-1",
            "runId": "run-1",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 200
    events = [
        json.loads(line.removeprefix("data:").strip())
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]
    assert [event["type"] for event in events] == [
        "RUN_STARTED",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]
    assert events[2]["delta"] == "hello from Foundry"
    assert events[-1]["correlationId"] == "corr-1"


def test_jwt_auth_requires_scope(monkeypatch) -> None:
    monkeypatch.setenv("BFF_AUTH_MODE", "jwt")
    monkeypatch.setenv("BFF_REQUIRED_SCOPE", "Agui.Access")
    monkeypatch.setattr(
        app,
        "decode_bearer_token",
        lambda token: {"oid": "user", "tid": "tenant", "scp": "Other.Scope"},
    )
    application = app.create_app()

    response = TestClient(application).post(
        "/agui",
        headers={"Authorization": "Bearer token"},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 403
