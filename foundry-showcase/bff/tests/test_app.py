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
    assert "traceId" in events[-1]
    assert "spanId" in events[-1]


def test_feedback_records_annotation_for_signed_in_user(monkeypatch) -> None:
    monkeypatch.setenv("BFF_AUTH_MODE", "disabled")
    captured: dict[str, object] = {}

    def fake_record(**kwargs):
        captured.update(kwargs)
        return {"traceId": kwargs["trace_id"], "label": "fail"}

    monkeypatch.setattr(app, "record_feedback", fake_record)
    response = TestClient(app.create_app()).post(
        "/feedback",
        json={
            "traceId": "38cf2c1fc140195dcc2de68e9ff0d4e6",
            "spanId": "f52be5677a2c7aca",
            "passed": False,
            "comment": "  Not helpful  ",
        },
    )

    assert response.status_code == 200
    assert captured["passed"] is False
    assert captured["comment"] == "Not helpful"
    assert captured["reviewer"] == "Local Developer"


def test_feedback_rejects_missing_verdict(monkeypatch) -> None:
    monkeypatch.setenv("BFF_AUTH_MODE", "disabled")
    response = TestClient(app.create_app()).post(
        "/feedback",
        json={"traceId": "38cf2c1fc140195dcc2de68e9ff0d4e6", "spanId": "f52be5677a2c7aca"},
    )
    assert response.status_code == 400


def test_feedback_rejects_bad_trace_id(monkeypatch) -> None:
    monkeypatch.setenv("BFF_AUTH_MODE", "disabled")
    response = TestClient(app.create_app()).post(
        "/feedback",
        json={"traceId": "nope", "spanId": "f52be5677a2c7aca", "passed": True},
    )
    assert response.status_code == 400


def test_sign_in_library_is_served_locally(monkeypatch) -> None:
    monkeypatch.setenv("BFF_AUTH_MODE", "disabled")
    client = TestClient(app.create_app())

    page = client.get("/")
    assert page.status_code == 200
    assert "/vendor/msal-browser.min.js" in page.text
    assert "alcdn.msauth.net" not in page.text

    script = client.get("/vendor/msal-browser.min.js")
    assert script.status_code == 200
    assert "PublicClientApplication" in script.text


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
