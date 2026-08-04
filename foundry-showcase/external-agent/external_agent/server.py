"""FastAPI service exposing A2A protocol, agent card, and health endpoint."""

from __future__ import annotations

import os
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from .agent import AGENT_ID, AGENT_NAME, answer_triage_question
from .telemetry import setup_telemetry

setup_telemetry()

app = FastAPI(title=AGENT_NAME, docs_url=None, redoc_url=None)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "agent": AGENT_ID}


@app.get("/.well-known/agent-card.json")
def agent_card(request: Request) -> dict[str, Any]:
    base_url = os.getenv("AGENT_PUBLIC_URL") or str(request.base_url).rstrip("/")
    base_url = base_url.rstrip("/")
    return {
        "name": AGENT_NAME,
        "description": (
            "Read-only support triage assistant. Answers support-lead questions about "
            "case priority classification, routing rules, SLA thresholds, and escalation "
            "criteria. Does not assess policy risk on case updates and does not read or "
            "write case records."
        ),
        "url": base_url,
        "version": "1.0.0",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
        },
        "skills": [
            {
                "id": "support-triage",
                "name": "Support Case Triage",
                "description": (
                    "Classifies case priority, routes to the right team, explains SLAs, "
                    "and advises on escalation criteria based on the support-operations "
                    "knowledge base."
                ),
                "inputModes": ["text/plain"],
                "outputModes": ["text/plain"],
            }
        ],
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
    }


@app.post("/")
async def jsonrpc_endpoint(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None},
            status_code=400,
        )

    req_id = body.get("id")
    method = body.get("method", "")

    if method not in ("message/send",):
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": f"Method '{method}' not found"},
                "id": req_id,
            },
            status_code=400,
        )

    try:
        params = body.get("params", {})
        message = params.get("message", {})
        parts = message.get("parts", [])
        question = next((p["text"] for p in parts if p.get("kind") == "text"), "")

        if not question.strip():
            raise ValueError("Empty message text.")

        result = answer_triage_question(question)

        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "result": {
                    "id": str(uuid.uuid4()),
                    "contextId": str(uuid.uuid4()),
                    "status": {"state": "completed"},
                    "artifacts": [
                        {
                            "artifactId": str(uuid.uuid4()),
                            "parts": [{"kind": "text", "text": result["text"]}],
                        }
                    ],
                },
                "id": req_id,
            }
        )
    except Exception as exc:
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "error": {"code": -32000, "message": str(exc)},
                "id": req_id,
            },
            status_code=500,
        )


def main() -> None:
    import uvicorn

    uvicorn.run(
        "external_agent.server:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
    )


if __name__ == "__main__":
    main()
