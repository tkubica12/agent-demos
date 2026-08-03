"""Reviewer app: read Foundry agent traces, annotate them, export an evaluation set.

Annotations are written straight into the Application Insights resource connected to the
Foundry project, so they appear in the Foundry portal without the reviewer ever opening it.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles

from auth import (
    SESSION_COOKIE,
    Authenticator,
    AuthError,
    Session,
    SessionStore,
)
from telemetry import (
    Annotation,
    TelemetryClient,
    TelemetryError,
    build_evaluation_dataset,
    dataset_to_jsonl,
)

WEB_DIR = Path(__file__).with_name("web")


def required_config(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required configuration: {name}")
    return value


def create_app(client: httpx.AsyncClient | None = None) -> FastAPI:
    provided_client = client

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owns_client = provided_client is None
        app.state.http = provided_client or httpx.AsyncClient(timeout=120.0)
        try:
            yield
        finally:
            if owns_client:
                await app.state.http.aclose()

    app = FastAPI(title="Foundry Showcase Annotation Review", lifespan=lifespan)
    app.state.sessions = SessionStore()
    app.mount("/web", StaticFiles(directory=WEB_DIR), name="web")

    def authenticator() -> Authenticator:
        return Authenticator(
            tenant_id=required_config("REVIEW_TENANT_ID"),
            client_id=required_config("REVIEW_CLIENT_ID"),
            redirect_uri=os.getenv(
                "REVIEW_REDIRECT_URI", "http://localhost:8080/auth/callback"
            ),
        )

    def telemetry() -> TelemetryClient:
        return TelemetryClient(
            resource_id=required_config("REVIEW_APPINSIGHTS_RESOURCE_ID"),
            connection_string=required_config("REVIEW_APPINSIGHTS_CONNECTION_STRING"),
            client=app.state.http,
        )

    def current_session(request: Request) -> Session:
        session = app.state.sessions.get(request.cookies.get(SESSION_COOKIE))
        if session is None or session.username is None:
            raise HTTPException(status_code=401, detail="Sign in to continue.")
        return session

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/auth/login")
    async def login(request: Request) -> RedirectResponse:
        session = app.state.sessions.get(request.cookies.get(SESSION_COOKIE))
        if session is None:
            session = app.state.sessions.create()
        try:
            auth_uri = authenticator().start_login(session)
        except AuthError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        response = RedirectResponse(auth_uri, status_code=302)
        response.set_cookie(
            SESSION_COOKIE,
            session.session_id,
            httponly=True,
            samesite="lax",
            max_age=8 * 60 * 60,
        )
        return response

    @app.get("/auth/callback")
    async def callback(request: Request) -> Response:
        session = app.state.sessions.get(request.cookies.get(SESSION_COOKIE))
        if session is None:
            return RedirectResponse("/", status_code=302)
        try:
            next_uri = authenticator().complete_login(
                session, dict(request.query_params)
            )
        except AuthError as exc:
            return PlainTextResponse(f"Sign-in failed: {exc}", status_code=400)
        return RedirectResponse(next_uri or "/", status_code=302)

    @app.post("/auth/logout")
    async def logout(request: Request) -> JSONResponse:
        app.state.sessions.drop(request.cookies.get(SESSION_COOKIE))
        response = JSONResponse({"status": "signed-out"})
        response.delete_cookie(SESSION_COOKIE)
        return response

    @app.get("/api/me")
    async def me(request: Request) -> dict[str, Any]:
        session = app.state.sessions.get(request.cookies.get(SESSION_COOKIE))
        if session is None or session.username is None:
            return {"signedIn": False}
        return {
            "signedIn": True,
            "username": session.username,
            "name": session.name,
        }

    @app.get("/api/runs")
    async def runs(
        request: Request,
        days: int = 30,
        limit: int = 100,
        session: Session = Depends(current_session),
    ) -> dict[str, Any]:
        auth = authenticator()
        client = telemetry()
        try:
            read_token = auth.read_token(session)
            items = await client.list_runs(read_token, days=days, limit=limit)
            annotations = await client.list_annotations(read_token, days=days)
        except AuthError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except TelemetryError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        grouped: dict[str, list[dict[str, Any]]] = {}
        for annotation in annotations:
            grouped.setdefault(annotation["traceId"], []).append(annotation)
        for item in items:
            item["annotations"] = grouped.get(item["traceId"], [])
        return {"runs": items, "annotations": annotations}

    @app.get("/api/runs/{trace_id}")
    async def run_detail(
        trace_id: str, session: Session = Depends(current_session)
    ) -> dict[str, Any]:
        auth = authenticator()
        client = telemetry()
        try:
            read_token = auth.read_token(session)
            steps = await client.trace_detail(trace_id, read_token)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except AuthError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except TelemetryError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"traceId": trace_id, "steps": steps}

    @app.post("/api/annotations")
    async def create_annotation(
        request: Request, session: Session = Depends(current_session)
    ) -> dict[str, Any]:
        payload = await request.json()
        try:
            annotation = Annotation(
                trace_id=payload["traceId"],
                span_id=payload["spanId"],
                passed=bool(payload["passed"]),
                explanation=(payload.get("explanation") or "").strip() or None,
                reviewer=session.username,
                source=payload.get("source", "builder"),
                response_id=payload.get("responseId"),
                conversation_id=payload.get("conversationId"),
                agent_name=payload.get("agentName"),
                agent_version=payload.get("agentVersion"),
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=400, detail=f"Missing field: {exc.args[0]}"
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            ingest_token = authenticator().ingest_token(session)
            result = await telemetry().write_annotation(annotation, ingest_token)
        except AuthError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except TelemetryError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {
            **result,
            "reviewer": annotation.reviewer,
            "timestamp": annotation.timestamp,
        }

    @app.get("/api/evaluation-set")
    async def evaluation_set(
        days: int = 30,
        limit: int = 200,
        session: Session = Depends(current_session),
    ) -> Response:
        auth = authenticator()
        client = telemetry()
        try:
            read_token = auth.read_token(session)
            items = await client.list_runs(read_token, days=days, limit=limit)
            annotations = await client.list_annotations(read_token, days=days)
        except AuthError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except TelemetryError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        cases = build_evaluation_dataset(items, annotations)
        return PlainTextResponse(
            dataset_to_jsonl(cases),
            media_type="application/jsonl",
            headers={
                "Content-Disposition": 'attachment; filename="flagged-evaluation-set.jsonl"',
                "X-Case-Count": str(len(cases)),
            },
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
