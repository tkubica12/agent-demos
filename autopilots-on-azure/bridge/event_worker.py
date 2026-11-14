from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import shutil
from collections.abc import Awaitable, Callable
from contextlib import closing
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request

from bridge.durable_transport import (
    Journal, bus, canonical, decode_callback, delivery_identity, delivery_queue,
    enabled, journal, settings,
)
from bridge.telemetry import operation_span, runtime_correlation_context
from bridge.operator_auth import require_collective_learning_operator


logger = logging.getLogger(__name__)
_active: dict[tuple[str, str], asyncio.Task[None]] = {}
_admitting: set[tuple[str, str]] = set()
_admission_lock = asyncio.Lock()


class InvocationPendingError(TimeoutError):
    status = "in_progress"


class AmbiguousEffectError(RuntimeError):
    status = "uncertain"


async def external_effect(name: str, operation: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
    """Checkpoint a non-idempotent outbound effect; an ambiguous attempt is never replayed."""
    message_id, queue = delivery_identity.get(), delivery_queue.get()
    if not message_id or not enabled():
        return await operation()
    body = json.loads(journal().get(queue, message_id)["payload"])
    occurrence = [
        body.get("type"), body.get("workerId"), body.get("jobId"), body.get("revision"),
        body.get("occurrenceId") or body.get("revision") or body.get("operationId") or message_id,
    ]
    identity = hashlib.sha256(canonical(occurrence).encode()).hexdigest()
    key = f"schedule-effect:{queue}:{identity}:{name}"
    saved = journal().claim_effect(key)
    if saved:
        if saved["phase"] == "completed":
            return saved["result"]
        raise AmbiguousEffectError(f"Outbound {name} has no durable acknowledgement; operator inspection required.")
    journal().update(queue, message_id, effect_stage="external_started")
    await checkpoint()
    try:
        result = await operation()
    except Exception as exc:
        raise AmbiguousEffectError(f"Outbound {name} has no durable acknowledgement: {exc}") from exc
    journal().save_state(key, {"phase": "completed", "result": result})
    journal().update(queue, message_id, effect_stage="external_completed")
    await checkpoint()
    return result


async def native_effect_started() -> None:
    message_id, queue = delivery_identity.get(), delivery_queue.get()
    if message_id and queue and enabled():
        journal().update(queue, message_id, effect_stage="native")
        await checkpoint()


def _live() -> set[tuple[str, str]]:
    return _admitting | {key for key, task in _active.items() if not task.done()}


async def sweep_stranded() -> int:
    count = journal().sweep(
        _live(), max_attempts=int(os.getenv("SCHEDULER_MAX_DELIVERY_COUNT", "10")),
        stale_seconds=int(os.getenv("SCHEDULER_MAX_LOCK_RENEWAL_SECONDS", "1800")),
    )
    if count:
        await checkpoint()
    return count


def _outcome(result: dict[str, Any]) -> str:
    status = result.get("status")
    if status in {"uncertain", "unknown", "interrupted"}:
        return "unknown"
    if status in {"failed", "error"}:
        return "failed"
    if status in {"in_progress", "known_run"}:
        return "known_run"
    if status in {"retryable", "retryable_preclaim"}:
        return "retryable"
    if status in {
        "completed", "duplicate", "stale", "delivered", "scheduled", "ack_scheduled", "delivery_in_progress",
    }:
        return "completed"
    raise AmbiguousEffectError(f"Scheduled handler returned no recognized outcome: {status!r}.")


async def _settle_terminal(queue: str, message_id: str) -> None:
    current = journal().get(queue, message_id)
    if current["settled"] or not current["lock_token"]:
        return
    operation = "complete" if current["phase"] == "completed" else "deadletter"
    with bus() as receiver:
        await asyncio.to_thread(receiver.settle, queue, operation, current["lock_token"])
    journal().mark_settled(queue, message_id, current["lock_token"])
    await checkpoint()


def initialize_journal() -> Journal:
    config = settings()
    backup = os.getenv("TRANSPORT_JOURNAL_BACKUP", "").strip()
    if not backup:
        raise ValueError("Native transport requires a durable journal backup destination.")
    if not config.journal_path.exists() and Path(backup).exists():
        config.journal_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(backup, config.journal_path)
    instance = journal()
    with closing(instance.connect()) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("The restored transport journal failed SQLite integrity validation.")
    removed = instance.remove_obsolete_recovery_data(f"worker-{config.worker}-activities")
    recovered = instance.sweep(
        _live(), max_attempts=int(os.getenv("SCHEDULER_MAX_DELIVERY_COUNT", "10")),
        stale_seconds=int(os.getenv("SCHEDULER_MAX_LOCK_RENEWAL_SECONDS", "1800")),
    )
    if removed or recovered:
        instance.backup(Path(backup))
    return instance


async def checkpoint() -> None:
    destination = os.getenv("TRANSPORT_JOURNAL_BACKUP", "").strip()
    if not destination:
        raise RuntimeError("Transport journal backup is not configured.")
    await asyncio.to_thread(journal().backup, Path(destination))


async def _renew(queue: str, message_id: str) -> None:
    with bus() as receiver:
        while True:
            row = journal().get(queue, message_id)
            if row["connection_id"] != settings().connection_id:
                raise RuntimeError("A native lease changed its owning connection during processing.")
            if not row["lock_token"]:
                await asyncio.sleep(1)
                continue
            await asyncio.to_thread(receiver.settle, queue, "renewlock", row["lock_token"])
            await asyncio.sleep(40)


async def _keep_active() -> None:
    url = os.getenv("TRANSPORT_SELF_URL", "").rstrip("/")
    if url:
        health_url = url + "/health"
    else:
        health_url = ""
    if not health_url.startswith("https://"):
        raise ValueError("Active transport work requires its native HTTPS self-ingress URL.")
    async with httpx.AsyncClient(timeout=20) as client:
        while True:
            response = await client.get(health_url)
            response.raise_for_status()
            await asyncio.sleep(60)


async def _guarded(work: Awaitable[dict[str, Any]], queue: str, message_id: str) -> dict[str, Any]:
    worker = asyncio.create_task(work)
    renewer = asyncio.create_task(_renew(queue, message_id))
    keepalive = asyncio.create_task(_keep_active())
    tasks = (worker, renewer, keepalive)
    try:
        done, _ = await asyncio.wait(
            tasks, timeout=int(os.getenv("SCHEDULER_MAX_LOCK_RENEWAL_SECONDS", "1800")),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            raise TimeoutError("Native transport work exceeded its bounded active-operation window.")
        if worker in done:
            return await worker
        for task in done:
            await task
        raise RuntimeError("A native transport guard exited before work completed.")
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _process(
    queue: str, message_id: str,
    schedule_handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None,
) -> None:
    admitted = journal().get(queue, message_id)
    body = json.loads(admitted["payload"])
    source = body.get("type")
    if source not in {"hermes.cron.fire", "system.dream", "document.publish.retry"}:
        raise ValueError("Scheduled processing requires a validated scheduling message type.")
    carrier = body.get("trace", {})
    with runtime_correlation_context(carrier), operation_span(
        "transport.process", carrier=carrier, operation_id=message_id,
        attributes={
            "messaging.system": "servicebus", "messaging.destination.name": queue,
            "autopilots.invocation.source": source,
        },
    ) as span:
        try:
            await _execute_delivery(queue, message_id, schedule_handler)
        finally:
            current = journal().get(queue, message_id)
            outcome = current["phase"]
            if outcome == "completed":
                if admitted["phase"] == "completed":
                    outcome = "duplicate"
                else:
                    result = json.loads(current["result"] or "{}")
                    status = result.get("status")
                    outcome = status if status in {"duplicate", "stale"} else "completed"
            span.set_attribute("autopilots.outcome", outcome)


async def _execute_delivery(
    queue: str, message_id: str,
    schedule_handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None,
) -> None:
    token = delivery_identity.set(message_id)
    queue_token = delivery_queue.set(queue)
    try:
        row = journal().get(queue, message_id)
        if row["settled"]:
            return
        if row["phase"] in {"unknown", "failed"}:
            await _settle_terminal(queue, message_id)
            return
        body = json.loads(row["payload"])
        if row["phase"] != "completed":
            journal().update(queue, message_id, phase="running")
            await checkpoint()
            if queue != settings().schedule_queue or schedule_handler is None:
                raise ValueError("No handler exists for the actor's admitted queue.")
            result = await _guarded(schedule_handler(body), queue, message_id)
            phase = _outcome(result)
            current = journal().get(queue, message_id)
            if phase in {"known_run", "retryable"} and current["delivery_count"] >= int(
                os.getenv("SCHEDULER_MAX_DELIVERY_COUNT", "10")
            ):
                phase = "unknown" if phase == "known_run" else "failed"
            journal().update(
                queue, message_id, phase=phase, result=canonical(result),
                error=str(result.get("reason") or result.get("error") or "") if phase != "completed" else "",
            )
            await checkpoint()
        if journal().get(queue, message_id)["phase"] in {"completed", "unknown", "failed"}:
            await _settle_terminal(queue, message_id)
    except (Exception, asyncio.CancelledError) as error:
        logger.exception("Native transport delivery failed: queue=%s correlation=%s", queue, message_id)
        values: dict[str, Any] = {"error": f"{type(error).__name__}: {str(error)[:1500]}"}
        current = journal().get(queue, message_id)
        if current["phase"] not in {"completed", "unknown", "failed"}:
            status = getattr(error, "status", "")
            if status == "uncertain" or current["effect_stage"] == "external_started":
                values["phase"] = "unknown"
            elif status == "failed":
                values["phase"] = "failed"
            elif status == "in_progress" or current["native_run_id"]:
                values["phase"] = "known_run"
            else:
                values["phase"] = "retryable"
            if values["phase"] not in {"unknown", "failed"} and current["delivery_count"] >= int(
                os.getenv("SCHEDULER_MAX_DELIVERY_COUNT", "10")
            ):
                values["phase"] = (
                    "failed" if current["effect_stage"] == "preclaim"
                    and not current["native_scope"] and not current["native_run_id"]
                    else "unknown"
                )
        elif current["error"]:
            values["error"] = current["error"]
        journal().update(queue, message_id, **values)
        await checkpoint()
        current = journal().get(queue, message_id)
        if current["phase"] in {"unknown", "failed"} and not current["settled"] and current["lock_token"]:
            await _settle_terminal(queue, message_id)
        raise
    finally:
        delivery_queue.reset(queue_token)
        delivery_identity.reset(token)

def start_work(
    queue: str, message_id: str,
    schedule_handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None,
) -> None:
    key = (queue, message_id)
    previous = _active.get(key)
    if previous is not None and not previous.done():
        return
    task = asyncio.create_task(_process(queue, message_id, schedule_handler))
    _active[key] = task

    def completed(done: asyncio.Task[None]) -> None:
        error = None if done.cancelled() else done.exception()
        if error is not None:
            logger.error("Durable work stopped: queue=%s correlation=%s: %s", queue, message_id, error)
        if _active.get(key) is done:
            _active.pop(key, None)
            if error is None and not done.cancelled():
                row = journal().get(queue, message_id)
                if row["phase"] in {"completed", "unknown", "failed"} and row["lock_token"] and not row["settled"]:
                    start_work(queue, message_id, schedule_handler)

    task.add_done_callback(completed)


def attach(
    app: FastAPI, *,
    schedule_handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    schedule_repair_handler: Callable[[str, dict[str, Any], str], Awaitable[dict[str, Any]]] | None = None,
    known_result_handler: Callable[[dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
) -> None:
    if not enabled():
        @app.get("/internal/transport/status")
        async def local_transport_status(request: Request) -> dict[str, Any]:
            expected = os.getenv("API_SERVER_KEY", "")
            supplied = request.headers.get("x-autopilot-key", "")
            if not expected or not secrets.compare_digest(expected, supplied):
                raise HTTPException(status_code=401, detail="A valid operator key is required.")
            from bridge.work_activity import active_work_count

            active = active_work_count()
            return {
                "enabled": False,
                "active": active, "gatewayActive": active, "schedulingActive": 0,
                "unfinished": 0, "recoverable": 0, "unsettled": 0, "quarantined": 0,
                "dlqInspectionRequired": False, "lastAttemptsAwaitingSettlement": 0,
                "receiptRetention": "not-applicable",
                "worker": os.getenv("WORKER_ID", ""), "role": "gateway",
            }
        return
    initialize_journal()

    @app.middleware("http")
    async def maintenance_barrier(request: Request, call_next):
        if (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and Path("/app/.application-maintenance").exists()
        ):
            from fastapi.responses import JSONResponse

            return JSONResponse(
                {"error": "Application maintenance; the request was not admitted."},
                status_code=503, headers={"Retry-After": "10"},
            )
        return await call_next(request)

    @app.get("/internal/transport/status")
    async def transport_status(request: Request) -> dict[str, Any]:
        expected = os.getenv("API_SERVER_KEY", "")
        supplied = request.headers.get("x-autopilot-key", "")
        if not expected or not secrets.compare_digest(expected, supplied):
            raise HTTPException(status_code=401, detail="A valid operator key is required.")
        await sweep_stranded()
        from bridge.work_activity import active_work_count

        scheduling_active = len(_live())
        gateway_active = active_work_count()
        with closing(journal().connect()) as connection:
            unfinished = connection.execute(
                "SELECT count(*) FROM deliveries WHERE phase NOT IN ('completed','failed','unknown')",
            ).fetchone()[0]
            unsettled = connection.execute(
                "SELECT count(*) FROM deliveries WHERE settled=0",
            ).fetchone()[0]
            quarantined = connection.execute(
                "SELECT count(*) FROM deliveries WHERE phase IN ('failed','unknown') AND resolution=''",
            ).fetchone()[0]
            last_attempts = connection.execute(
                "SELECT count(*) FROM deliveries WHERE delivery_count>=? AND settled=0",
                (int(os.getenv("SCHEDULER_MAX_DELIVERY_COUNT", "10")),),
            ).fetchone()[0]
        return {
            "active": scheduling_active + gateway_active, "gatewayActive": gateway_active,
            "schedulingActive": scheduling_active,
            "unfinished": scheduling_active, "recoverable": unfinished,
            "unsettled": unsettled, "quarantined": quarantined,
            "dlqInspectionRequired": bool(quarantined or last_attempts),
            "lastAttemptsAwaitingSettlement": last_attempts,
            "receiptRetention": "indefinite-replay-tombstones",
            "worker": settings().worker, "role": settings().role,
        }

    @app.post("/internal/transport/recover")
    async def recover(request: Request) -> dict[str, Any]:
        await require_collective_learning_operator(request)
        return {"terminalized": await sweep_stranded()}

    @app.post("/internal/transport/resolve")
    async def resolve(request: Request) -> dict[str, Any]:
        principal = await require_collective_learning_operator(request)
        payload = await request.json()
        if not isinstance(payload, dict) or "operator" in payload:
            raise HTTPException(status_code=422, detail="Operator identity is derived from the owner access token.")
        queue, message_id = str(payload.get("queue") or ""), str(payload.get("messageId") or "")
        if (queue, message_id) in _live():
            raise HTTPException(status_code=409, detail="Live work cannot be resolved.")
        try:
            journal().resolve(
                queue, message_id, outcome=str(payload.get("outcome") or ""),
                operator=principal, reason=str(payload.get("reason") or ""),
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await checkpoint()
        return {"messageId": message_id, "phase": journal().get(queue, message_id)["phase"], "replayed": False}

    async def repair_schedule(request: Request, action: str) -> dict[str, Any]:
        principal = await require_collective_learning_operator(request)
        payload = await request.json()
        if not isinstance(payload, dict) or "operator" in payload:
            raise HTTPException(status_code=422, detail="Repair requires an object without a caller-supplied operator.")
        if schedule_repair_handler is None:
            raise HTTPException(status_code=503, detail="Native schedule repair is not configured.")
        return await schedule_repair_handler(action, payload, principal)

    @app.post("/internal/transport/rearm-preclaim")
    async def rearm_schedule(request: Request) -> dict[str, Any]:
        return await repair_schedule(request, "rearm-preclaim")

    @app.post("/internal/transport/resume-known-result")
    async def resume_known_result(request: Request) -> dict[str, Any]:
        principal = await require_collective_learning_operator(request)
        payload = await request.json()
        if not isinstance(payload, dict) or set(payload) != {
            "queue", "messageId", "executionId", "outputSha256", "reason",
        } or not all(isinstance(value, str) and value.strip() for value in payload.values()):
            raise HTTPException(status_code=422, detail="Exact delivery/execution identity, output digest and reason are required.")
        queue, message_id = payload["queue"], payload["messageId"]
        if queue != settings().schedule_queue or known_result_handler is None:
            raise HTTPException(status_code=409, detail="Known-result delivery is not configured for this queue.")
        key = (queue, message_id)
        async with _admission_lock:
            if key in _live() or len(_live()) >= 32 or Path("/app/.application-maintenance").exists():
                raise HTTPException(status_code=409, detail="The delivery is busy or in maintenance.")
            _admitting.add(key)
        token, queue_token = delivery_identity.set(message_id), delivery_queue.set(queue)
        try:
            row = journal().get(queue, message_id)
            body = json.loads(row["payload"])
            audit_key = f"known-result:{queue}:{message_id}"
            audit = journal().load_state(audit_key)
            proof = {name: payload[name] for name in ("executionId", "outputSha256")}
            if audit and audit["proof"] != proof and (
                row["phase"] == "completed" or row["effect_stage"] not in {"preclaim", "native"}
            ):
                raise HTTPException(status_code=409, detail="A different result was already authorized for this occurrence.")
            if row["phase"] == "completed" and audit:
                return {"messageId": message_id, "status": "completed", **json.loads(row["result"])}
            if body.get("type") != "hermes.cron.fire" or row["phase"] not in {"unknown", "failed"} or not row["settled"]:
                raise HTTPException(status_code=409, detail="Only a settled quarantined cron result can be resumed.")
            if row["effect_stage"] == "external_started":
                raise HTTPException(status_code=409, detail="The previous Teams send has an unknown outcome; resending is forbidden.")
            if not audit or audit["proof"] != proof:
                journal().save_state(audit_key, {
                    "proof": proof, "operator": principal, "reason": payload["reason"],
                })
                await checkpoint()
            from bridge.work_activity import active_work

            async with active_work():
                result = await known_result_handler(body, proof)
            if result.get("status") != "completed" or not result.get("deliveryActivityId"):
                raise RuntimeError("Known-result recovery has no durable delivery acknowledgement.")
            journal().update(queue, message_id, phase="completed", result=canonical(result), error="",
                             resolution=canonical({"action": "resume-known-result", "operator": principal,
                                                   "reason": payload["reason"], **proof}))
            await checkpoint()
            return {"messageId": message_id, **result}
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            delivery_identity.reset(token)
            delivery_queue.reset(queue_token)
            _admitting.discard(key)

    @app.post("/internal/transport/resolve-schedule")
    async def resolve_native_schedule(request: Request) -> dict[str, Any]:
        return await repair_schedule(request, "resolve")

    @app.post("/internal/transport/callback")
    async def callback(request: Request) -> dict[str, Any]:
        from bridge.transport_ingress import require_native_callback

        require_native_callback(request)
        if len(await request.body()) > 64 * 1024 * 3:
            raise HTTPException(status_code=413, detail="Native callback exceeds the bounded envelope size.")
        payload = await request.json()
        queue = settings().schedule_queue
        try:
            envelope, body = decode_callback(payload, queue, settings())
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(status_code=422, detail="Invalid native scheduling envelope.") from error
        await sweep_stranded()
        key = (queue, envelope["MessageId"])
        async with _admission_lock:
            if Path("/app/.application-maintenance").exists():
                raise HTTPException(status_code=503, detail="Scheduling admission is in application maintenance.")
            if key in _admitting:
                raise HTTPException(status_code=503, detail="An operator is repairing this exact occurrence.")
            if key not in _live() and len(_live()) >= 32:
                raise HTTPException(status_code=503, detail="Scheduling concurrency limit reached; lease not admitted.")
            _admitting.add(key)
            try:
                row = journal().admit(queue, envelope, body, settings().connection_id)
                await checkpoint()
                start_work(queue, envelope["MessageId"], schedule_handler)
            finally:
                _admitting.discard(key)
        return {"messageId": envelope["MessageId"], "phase": row["phase"], "acknowledged": False}

    @app.on_event("shutdown")
    async def stop_workers() -> None:
        tasks = [task for task in _active.values() if not task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
