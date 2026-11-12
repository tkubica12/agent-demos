"""Hermes-state endpoints served by the native Hermes API server through the autopilots plugin."""

from __future__ import annotations

import asyncio
import os
import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from aiohttp import web

from collective_learning import (
    CollectiveLearningError,
    approved_learning_packet,
    attest_learning_packet,
    attest_refresh_rejection,
    pending_learning_packet,
    pending_refresh_rejection,
    prepare_learning_packet,
    prepare_refresh_rejection,
    worker_refresh_readiness,
)
from cron_runtime import (
    acknowledge_cron_delivery,
    bind_cron_delivery,
    bind_cron_local,
    checkpoint_system_schedule,
    claim_system_schedule,
    complete_system_schedule,
    cron_delivery_receipt_status,
    cron_diagnostics,
    fire_cron_job,
    get_delivery_reference,
    get_system_schedule_checkpoint,
    list_cron_jobs,
    reconcile_cron_provider,
    upsert_delivery_reference,
)
from native_learning import (
    LearningConflictError,
    LearningDecisionError,
    decide_pending,
    learning_digest,
    record_share_choice,
    undo_entry,
)

Handler = Callable[[web.Request], Awaitable[Any]]


class RouteError(Exception):
    def __init__(self, status: int, detail: Any) -> None:
        super().__init__(str(detail))
        self.status = status
        self.detail = detail


def _authorized(request: web.Request) -> bool:
    expected = os.getenv("API_SERVER_KEY", "")
    supplied = request.headers.get("x-autopilot-key", "")
    return bool(expected) and secrets.compare_digest(supplied, expected)


async def _json_object(request: web.Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except ValueError as exc:
        raise RouteError(400, "Request body must be JSON.") from exc
    if not isinstance(payload, dict):
        raise RouteError(400, "Request body must be one JSON object.")
    return payload


def _string_list(payload: dict[str, Any], name: str) -> list[str]:
    values = payload.get(name)
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise RouteError(400, f"{name} must be an array of strings.")
    return values


def _endpoint(handler: Handler, errors: dict[type[Exception], int] | None = None) -> Handler:
    mapping = {ValueError: 400, **(errors or {})}

    async def wrapped(request: web.Request) -> web.StreamResponse:
        if not _authorized(request):
            return web.json_response({"detail": "A valid X-Autopilot-Key header is required."}, status=401)
        try:
            return web.json_response(await handler(request))
        except RouteError as exc:
            return web.json_response({"detail": exc.detail}, status=exc.status)
        except tuple(mapping) as exc:
            status = next(code for kind, code in mapping.items() if isinstance(exc, kind))
            return web.json_response({"detail": str(exc)}, status=status)

    return wrapped


def native_activity(adapter: Any) -> dict[str, int]:
    from gateway.platforms.api_server_runs import api_worker_live_count
    from runtime_keepalive import get_keepalive

    pending = getattr(adapter, "_pending_agent_requests", 0)
    inflight = adapter._inflight_agent_runs
    tasks = adapter._active_run_tasks
    if (
        any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (pending, inflight))
        or not isinstance(tasks, dict)
    ):
        raise RuntimeError("The pinned native API adapter returned invalid activity state.")
    supervisor = get_keepalive()
    counts = {
        "pendingAdmissions": pending, "inflightNativeTurns": inflight,
        "liveRunTasks": sum(not task.done() for task in tasks.values()),
        "nativeWorkerThreads": api_worker_live_count(),
        "modelToolScopes": supervisor.status()["active"] if supervisor is not None else 0,
    }
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts.values()):
        raise RuntimeError("Native activity counters are invalid; refusing to attest quiescence.")
    return {**counts, "active": sum(counts.values())}


def register_routes(app: web.Application, profile_home: Path, *, adapter: Any = None) -> None:
    """Expose cron, system schedule and collective-learning operations next to the native Hermes API."""

    def threaded(function: Callable[..., Any], **kwargs: Any) -> Awaitable[Any]:
        return asyncio.to_thread(function, profile_home, **kwargs)

    async def collective(function: Callable[..., Any], **kwargs: Any) -> Any:
        return function(profile_home, **kwargs)

    async def receipt_payload(request: web.Request) -> dict[str, Any]:
        receipt = (await _json_object(request)).get("receipt")
        if not isinstance(receipt, dict):
            raise RouteError(400, "receipt must be one JSON object.")
        return receipt

    async def jobs(request: web.Request) -> Any:
        return {"jobs": list_cron_jobs(profile_home)}

    async def activity(request: web.Request) -> Any:
        return native_activity(adapter)

    async def upsert_reference(request: web.Request) -> Any:
        payload = await _json_object(request)
        return upsert_delivery_reference(
            profile_home,
            reference_key=str(payload.get("referenceKey") or ""),
            conversation=payload.get("conversation") or {},
            boundary=str(payload.get("boundary") or ""),
        )

    async def read_reference(request: web.Request) -> Any:
        key = request.query.get("referenceKey", "")
        reference = get_delivery_reference(profile_home, key)
        if not isinstance(reference, dict):
            raise RouteError(404, "Delivery reference was not found.")
        return {"referenceKey": key, "deliveryReference": reference}

    async def bind_delivery(request: web.Request) -> Any:
        payload = await _json_object(request)
        return bind_cron_delivery(
            profile_home,
            job_ids=_string_list(payload, "jobIds"),
            reference_key=str(payload.get("referenceKey") or ""),
        )

    async def bind_local(request: web.Request) -> Any:
        return bind_cron_local(profile_home, job_ids=_string_list(await _json_object(request), "jobIds"))

    async def fire(request: web.Request) -> Any:
        payload = await _json_object(request)
        try:
            return await threaded(
                fire_cron_job, job_id=str(payload.get("jobId") or ""), revision=str(payload.get("revision") or ""),
            )
        except ValueError:
            raise
        except Exception as exc:
            raise RouteError(500, {"type": exc.__class__.__name__, "message": str(exc)[:2000]}) from exc

    def occurrence(payload: dict[str, Any]) -> str:
        return str(payload.get("occurrenceId") or payload.get("revision") or "")

    async def system_claim(request: web.Request) -> Any:
        payload = await _json_object(request)
        return await threaded(
            claim_system_schedule, job_id=str(payload.get("jobId") or ""),
            revision=str(payload.get("revision") or ""), occurrence_id=occurrence(payload),
        )

    async def system_checkpoint_read(request: web.Request) -> Any:
        payload = await _json_object(request)
        return await threaded(
            get_system_schedule_checkpoint, job_id=str(payload.get("jobId") or ""),
            revision=str(payload.get("revision") or ""), occurrence_id=str(payload.get("occurrenceId") or ""),
        )

    async def system_checkpoint(request: web.Request) -> Any:
        payload = await _json_object(request)
        if not isinstance(payload.get("payload"), dict):
            raise RouteError(400, "Checkpoint payload must be an object.")
        return await threaded(
            checkpoint_system_schedule, job_id=str(payload.get("jobId") or ""),
            revision=str(payload.get("revision") or ""), occurrence_id=str(payload.get("occurrenceId") or ""),
            owner_token=str(payload.get("ownerToken") or ""), expected_phase=str(payload.get("expectedPhase") or ""),
            phase=str(payload.get("phase") or ""), payload=payload["payload"],
        )

    async def system_complete(request: web.Request) -> Any:
        payload = await _json_object(request)
        summary = payload.get("summary")
        return await threaded(
            complete_system_schedule, job_id=str(payload.get("jobId") or ""),
            revision=str(payload.get("revision") or ""), occurrence_id=occurrence(payload),
            success=bool(payload.get("success")), error=str(payload.get("error") or ""),
            summary=summary if isinstance(summary, dict) else {}, owner_token=str(payload.get("ownerToken") or ""),
        )

    async def ack_delivery(request: web.Request) -> Any:
        payload = await _json_object(request)
        return await threaded(
            acknowledge_cron_delivery, job_id=str(payload.get("jobId") or ""),
            revision=str(payload.get("revision") or ""),
            delivery_activity_id=str(payload.get("deliveryActivityId") or ""),
        )

    async def delivery_receipt(request: web.Request) -> Any:
        return await threaded(
            cron_delivery_receipt_status, job_id=request.match_info["job_id"], revision=request.match_info["revision"],
        )

    async def _with_receipt(request: web.Request, function: Callable[..., Any]) -> Any:
        return function(profile_home, receipt=await receipt_payload(request))

    async def learning_digest_route(request: web.Request) -> Any:
        try:
            limit = int(request.query.get("limit", "20"))
        except ValueError as exc:
            raise RouteError(400, "limit must be an integer.") from exc
        mark_presented = request.query.get("markPresented", "").lower() in {"1", "true", "yes"}
        return await threaded(learning_digest, limit=limit, mark_presented=mark_presented)

    async def learning_undo(request: web.Request) -> Any:
        payload = await _json_object(request)
        return await threaded(
            undo_entry, entry_id=str(payload.get("entryId") or ""), decided_by=str(payload.get("decidedBy") or ""),
        )

    async def learning_pending_decide(request: web.Request) -> Any:
        payload = await _json_object(request)
        if not isinstance(payload.get("approve"), bool):
            raise RouteError(400, "approve must be a boolean.")
        return await threaded(
            decide_pending, pending_id=str(payload.get("pendingId") or ""), approve=payload["approve"],
            decided_by=str(payload.get("decidedBy") or ""),
        )

    async def learning_share(request: web.Request) -> Any:
        payload = await _json_object(request)
        artifact_hash = payload.get("artifactHash")
        if artifact_hash is not None and not isinstance(artifact_hash, str):
            raise RouteError(400, "artifactHash must be a string.")
        return await threaded(
            record_share_choice, skill=str(payload.get("skill") or ""), choice=str(payload.get("choice") or ""),
            decided_by=str(payload.get("decidedBy") or ""), artifact_hash=artifact_hash or None,
        )

    collective_errors = {CollectiveLearningError: 400}
    learning_errors = {LearningDecisionError: 400, LearningConflictError: 409}
    routes: list[tuple[str, str, Handler, dict[type[Exception], int] | None]] = [
        ("GET", "/internal/runtime/activity", activity, None),
        ("GET", "/internal/cron/jobs", jobs, None),
        ("POST", "/internal/cron/reconcile", lambda request: threaded(reconcile_cron_provider), None),
        ("GET", "/internal/cron/diagnostics", lambda request: threaded(cron_diagnostics), None),
        ("POST", "/internal/cron/delivery-reference", upsert_reference, None),
        ("GET", "/internal/cron/delivery-reference", read_reference, None),
        ("POST", "/internal/cron/bind-delivery", bind_delivery, None),
        ("POST", "/internal/cron/bind-local", bind_local, None),
        ("POST", "/internal/cron/fire", fire, None),
        ("POST", "/internal/cron/system/claim", system_claim, None),
        ("POST", "/internal/cron/system/checkpoint/read", system_checkpoint_read, None),
        ("POST", "/internal/cron/system/checkpoint", system_checkpoint, {RuntimeError: 409}),
        ("POST", "/internal/cron/system/complete", system_complete, {RuntimeError: 409}),
        ("POST", "/internal/cron/ack-delivery", ack_delivery, None),
        ("GET", "/internal/cron/delivery-receipt/{job_id}/{revision}", delivery_receipt, None),
        ("POST", "/internal/collective-learning/prepare",
         lambda request: collective(prepare_learning_packet), collective_errors),
        ("GET", "/internal/collective-learning/pending",
         lambda request: collective(pending_learning_packet), collective_errors),
        ("POST", "/internal/collective-learning/attest",
         lambda request: _with_receipt(request, attest_learning_packet), collective_errors),
        ("GET", "/internal/collective-learning/export",
         lambda request: collective(approved_learning_packet), collective_errors),
        ("POST", "/internal/collective-learning/prepare-rejection",
         lambda request: collective(prepare_refresh_rejection), collective_errors),
        ("GET", "/internal/collective-learning/pending-rejection",
         lambda request: collective(pending_refresh_rejection), collective_errors),
        ("POST", "/internal/collective-learning/attest-rejection",
         lambda request: _with_receipt(request, attest_refresh_rejection), collective_errors),
        ("GET", "/internal/collective-learning/refresh-ready",
         lambda request: collective(worker_refresh_readiness), {CollectiveLearningError: 409}),
        ("GET", "/internal/learning/digest", learning_digest_route, learning_errors),
        ("POST", "/internal/learning/undo", learning_undo, learning_errors),
        ("POST", "/internal/learning/pending/decide", learning_pending_decide, learning_errors),
        ("POST", "/internal/learning/share", learning_share, learning_errors),
    ]

    async def _with_receipt(request: web.Request, function: Callable[..., Any]) -> Any:
        return function(profile_home, receipt=await receipt_payload(request))

    for method, path, handler, errors in routes:
        app.router.add_route(method, path, _endpoint(handler, errors))
