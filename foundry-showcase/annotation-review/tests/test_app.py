from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

import app as app_module
from telemetry import TelemetryClient

TRACE_ID = "38cf2c1fc140195dcc2de68e9ff0d4e6"
SPAN_ID = "f52be5677a2c7aca"
CONNECTION_STRING = (
    "InstrumentationKey=test-ikey;"
    "IngestionEndpoint=https://swedencentral-0.in.applicationinsights.azure.com/"
)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("REVIEW_TENANT_ID", "tenant")
    monkeypatch.setenv("REVIEW_CLIENT_ID", "client")
    monkeypatch.setenv("REVIEW_APPINSIGHTS_RESOURCE_ID", "/subscriptions/s/x")
    monkeypatch.setenv("REVIEW_APPINSIGHTS_CONNECTION_STRING", CONNECTION_STRING)


def test_healthz(env):
    with TestClient(app_module.create_app()) as client:
        assert client.get("/healthz").json() == {"status": "ok"}


def test_me_reports_signed_out(env):
    with TestClient(app_module.create_app()) as client:
        assert client.get("/api/me").json() == {"signedIn": False}


def test_protected_routes_require_sign_in(env):
    with TestClient(app_module.create_app()) as client:
        assert client.get("/api/runs").status_code == 401
        assert client.get(f"/api/runs/{TRACE_ID}").status_code == 401
        assert client.post("/api/annotations", json={}).status_code == 401
        assert client.get("/api/evaluation-set").status_code == 401


def test_index_is_served(env):
    with TestClient(app_module.create_app()) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "Agent Response Review" in response.text


async def test_write_annotation_posts_expected_envelope():
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers["Authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"itemsReceived": 1, "itemsAccepted": 1})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = TelemetryClient("/subscriptions/s/x", CONNECTION_STRING, http)
        from telemetry import Annotation

        result = await client.write_annotation(
            Annotation(
                trace_id=TRACE_ID,
                span_id=SPAN_ID,
                passed=False,
                explanation="Wrong policy quoted.",
                reviewer="reviewer@contoso.com",
            ),
            "the-token",
        )

    assert captured["url"].endswith("/v2.1/track")
    assert captured["auth"] == "Bearer the-token"
    envelope = captured["body"][0]
    assert envelope["tags"]["ai.operation.id"] == TRACE_ID
    assert envelope["tags"]["ai.operation.parentId"] == SPAN_ID
    properties = envelope["data"]["baseData"]["properties"]
    assert properties["gen_ai.evaluation.score.label"] == "fail"
    assert result["ingestion"]["itemsAccepted"] == 1


async def test_write_annotation_raises_when_rejected():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"itemsReceived": 1, "itemsAccepted": 0, "errors": [{"index": 0}]}
        )

    from telemetry import Annotation, TelemetryError

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = TelemetryClient("/subscriptions/s/x", CONNECTION_STRING, http)
        with pytest.raises(TelemetryError):
            await client.write_annotation(
                Annotation(trace_id=TRACE_ID, span_id=SPAN_ID, passed=True),
                "the-token",
            )


async def test_list_runs_projects_question_and_answer():
    payload = {
        "tables": [
            {
                "name": "PrimaryResult",
                "columns": [
                    {"name": "timestamp"},
                    {"name": "traceId"},
                    {"name": "spanId"},
                    {"name": "inputMessages"},
                    {"name": "outputMessages"},
                    {"name": "responseId"},
                    {"name": "conversationId"},
                    {"name": "model"},
                    {"name": "durationMs"},
                    {"name": "agentName"},
                    {"name": "agentVersion"},
                ],
                "rows": [
                    [
                        "2026-08-03T07:00:00Z",
                        TRACE_ID,
                        SPAN_ID,
                        json.dumps(
                            [
                                {
                                    "role": "user",
                                    "parts": [
                                        {
                                            "type": "text",
                                            "content": "Current user message:\nWhere is my order?",
                                        }
                                    ],
                                }
                            ]
                        ),
                        json.dumps(
                            [
                                {
                                    "role": "assistant",
                                    "parts": [
                                        {"type": "text", "content": "It shipped."}
                                    ],
                                }
                            ]
                        ),
                        "resp_1",
                        "conv_1",
                        "gpt-5.4-mini",
                        1200.0,
                        "foundry-showcase-main",
                        "28",
                    ]
                ],
            }
        ]
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer read-token"
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = TelemetryClient("/subscriptions/s/x", CONNECTION_STRING, http)
        runs = await client.list_runs("read-token")

    assert len(runs) == 1
    assert runs[0]["question"] == "Where is my order?"
    assert runs[0]["answer"] == "It shipped."
    assert runs[0]["agentVersion"] == "28"


async def test_query_failure_surfaces_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Forbidden")

    from telemetry import TelemetryError

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = TelemetryClient("/subscriptions/s/x", CONNECTION_STRING, http)
        with pytest.raises(TelemetryError):
            await client.list_runs("read-token")
