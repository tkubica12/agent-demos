from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bridge.local_state import locked_file


SYSTEM_DREAM_JOB_NAME = "Platform Dreaming"
SYSTEM_DREAM_PROMPT = "__AUTOPILOT_SYSTEM_DREAM__"


@contextmanager
def json_transaction(path: Path):
    """Serialize a short read/modify/replace across runtime threads and processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked_file(path.with_suffix(path.suffix + ".lock")):
        payload = read_json_object(path)
        yield payload
        atomic_write_json(path, payload)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.stem}-",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def read_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain one JSON object.")
    return payload


def delivery_references_path(profile_home: Path) -> Path:
    return profile_home / "local" / "delivery-references.json"


def cron_delivery_path(profile_home: Path) -> Path:
    return profile_home / "local" / "cron-delivery.json"


def cron_delivery_receipts_path(profile_home: Path) -> Path:
    return profile_home / "local" / "cron-delivery-receipts.json"


def system_schedule_receipts_path(profile_home: Path) -> Path:
    return profile_home / "local" / "system-schedule-receipts.json"


def _receipt_key(job_id: str, revision: str) -> str:
    return f"{job_id}:{revision}"


def upsert_delivery_reference(
    profile_home: Path,
    *,
    reference_key: str,
    conversation: dict[str, Any],
    boundary: str,
) -> dict[str, Any]:
    if not reference_key or len(reference_key) > 256:
        raise ValueError("referenceKey must be between 1 and 256 characters.")
    if boundary not in {"one_to_one", "shared_group", "public_channel"}:
        raise ValueError("Scheduled proactive delivery currently supports personal, group, and public channel boundaries.")
    path = delivery_references_path(profile_home)
    with json_transaction(path) as references:
        references[reference_key] = {
            "boundary": boundary,
            "conversation": conversation,
        }
    return {"referenceKey": reference_key, "boundary": boundary}


def get_delivery_reference(
    profile_home: Path,
    reference_key: str,
) -> dict[str, Any] | None:
    value = read_json_object(delivery_references_path(profile_home)).get(reference_key)
    return value if isinstance(value, dict) else None


def list_cron_jobs(
    profile_home: Path,
    *,
    include_system: bool = False,
) -> list[dict[str, Any]]:
    from cron.jobs import list_jobs
    from azure_cron_provider import schedule_revision

    scheduled = read_json_object(profile_home / "cron" / "azure-schedules.json")
    bindings = read_json_object(cron_delivery_path(profile_home))
    result = []
    for job in list_jobs(include_disabled=True):
        binding = bindings.get(str(job.get("id") or ""))
        system_type = (
            str(binding.get("systemType") or "")
            if isinstance(binding, dict)
            else ""
        )
        if system_type and not include_system:
            continue
        revision = schedule_revision(job)
        external = scheduled.get(str(job.get("id") or "")) or {}
        result.append({
            "id": str(job.get("id") or ""),
            "name": str(job.get("name") or ""),
            "schedule": job.get("schedule"),
            "repeat": job.get("repeat"),
            "enabled": bool(job.get("enabled")),
            "state": str(job.get("state") or ""),
            "nextRunAt": job.get("next_run_at"),
            "revision": revision,
            "script": bool(job.get("script")),
            "noAgent": bool(job.get("no_agent")),
            "systemType": system_type,
            "externallyScheduled": (
                external.get("revision") == revision
                and external.get("fireAt") == job.get("next_run_at")
            ),
        })
    return result


def bind_cron_delivery(
    profile_home: Path,
    *,
    job_ids: list[str],
    reference_key: str,
) -> dict[str, Any]:
    from cron.jobs import get_job

    if not get_delivery_reference(profile_home, reference_key):
        raise ValueError("Delivery reference does not exist.")
    path = cron_delivery_path(profile_home)
    bound: list[str] = []
    for job_id in job_ids:
        job = get_job(job_id)
        if not job:
            continue
        if job.get("script") or job.get("no_agent"):
            raise ValueError("Hosted scheduled scripts are not allowed.")
        bound.append(job_id)
    with json_transaction(path) as bindings:
        for job_id in bound:
            current = bindings.get(job_id)
            if isinstance(current, dict) and current.get("referenceKey") not in {None, reference_key}:
                raise ValueError("Cron delivery origin cannot replace an existing different reference.")
            bindings[job_id] = {"referenceKey": reference_key}
    return {"bound": bound, "referenceKey": reference_key}


def bind_cron_local(
    profile_home: Path,
    *,
    job_ids: list[str],
) -> dict[str, Any]:
    from cron.jobs import get_job

    path = cron_delivery_path(profile_home)
    bound: list[str] = []
    for job_id in job_ids:
        job = get_job(job_id)
        if not job:
            continue
        if job.get("script") or job.get("no_agent"):
            raise ValueError("Hosted scheduled scripts are not allowed.")
        bound.append(job_id)
    with json_transaction(path) as bindings:
        for job_id in bound:
            bindings[job_id] = {"local": True}
    return {"bound": bound, "deliveryMode": "local"}


def ensure_system_dream_schedule(
    profile_home: Path,
    *,
    enabled: bool,
    schedule: str,
) -> dict[str, Any]:
    from cron.jobs import create_job, list_jobs, remove_job, update_job

    all_jobs = list_jobs(include_disabled=True)
    bindings_path = cron_delivery_path(profile_home)
    bindings = read_json_object(bindings_path)
    bound_ids = {
        job_id
        for job_id, binding in bindings.items()
        if isinstance(binding, dict)
        and binding.get("systemType") == "dream"
    }
    jobs = [
        job
        for job in all_jobs
        if str(job.get("id") or "") in bound_ids
        or (
            job.get("name") == SYSTEM_DREAM_JOB_NAME
            and job.get("prompt") == SYSTEM_DREAM_PROMPT
        )
    ]
    if not enabled:
        for job in jobs:
            remove_job(str(job["id"]))
            bindings.pop(str(job["id"]), None)
        with json_transaction(bindings_path) as current:
            for job in jobs:
                current.pop(str(job["id"]), None)
        return {"enabled": False, "removed": len(jobs)}
    if not schedule.strip():
        raise ValueError("SERVICEBUS_DREAM_CRON_EXPRESSION is required.")

    job = jobs[0] if jobs else create_job(
        prompt=SYSTEM_DREAM_PROMPT,
        schedule=schedule,
        name=SYSTEM_DREAM_JOB_NAME,
        deliver="local",
    )
    for duplicate in jobs[1:]:
        remove_job(str(duplicate["id"]))
        bindings.pop(str(duplicate["id"]), None)
    updates: dict[str, Any] = {
        "name": SYSTEM_DREAM_JOB_NAME,
        "prompt": SYSTEM_DREAM_PROMPT,
        "deliver": "local",
    }
    if str(job.get("schedule_display") or "") != schedule:
        updates["schedule"] = schedule
    if not job.get("enabled") or job.get("state") != "scheduled":
        updates.update({"enabled": True, "state": "scheduled"})
    if updates:
        updated = update_job(str(job["id"]), updates)
        if updated:
            job = updated
    job_id = str(job["id"])
    with json_transaction(bindings_path) as current:
        for duplicate in jobs[1:]:
            current.pop(str(duplicate["id"]), None)
        current[job_id] = {"systemType": "dream"}
    return {
        "enabled": True,
        "jobId": job_id,
        "schedule": str(job.get("schedule_display") or schedule),
        "nextRunAt": job.get("next_run_at"),
    }


def claim_system_schedule(
    profile_home: Path,
    *,
    job_id: str,
    revision: str,
    occurrence_id: str,
) -> dict[str, Any]:
    from cron.jobs import claim_job_for_fire, get_job
    from azure_cron_provider import schedule_revision

    path = system_schedule_receipts_path(profile_home)
    key = _receipt_key(job_id, occurrence_id)
    completing = False
    needs_claim = False
    with json_transaction(path) as receipts:
        receipt = receipts.get(key)
        if isinstance(receipt, dict):
            if receipt.get("revision", revision) != revision:
                return {"status": "stale", "reason": "revision_mismatch", "jobId": job_id}
            if receipt.get("state") == "completed":
                return _terminal_system_outcome(receipt, job_id)
            completing = receipt.get("state") == "completing"
            needs_claim = receipt.get("state") == "queued" and receipt.get("operationKind") == "scheduled"
            if needs_claim:
                if not _native_occurrence_unclaimed(get_job(job_id), revision, receipt):
                    return {"status": "unknown", "jobId": job_id, "reason": "Queued native claim changed during recovery."}
                receipt.update(state="claiming", ownerToken=uuid.uuid4().hex, startedAtEpoch=time.time())
            if not completing and not needs_claim and receipt.get("state") != "queued":
                started_at = float(receipt.get("startedAtEpoch") or 0)
                lease_seconds = int(os.getenv("SCHEDULER_MAX_LOCK_RENEWAL_SECONDS", "1800"))
                if started_at and time.time() - started_at < lease_seconds:
                    return {"status": "in_progress", "jobId": job_id}
                if receipt.get("state") == "claiming" or receipt.get("phase") not in {
                    "pending", "dream_started", "dream_completed", "status_completed"
                }:
                    receipt["ownerToken"] = uuid.uuid4().hex
                    return {
                        **_system_operation(receipt, job_id),
                        "status": "interrupted",
                        "reason": "execution_outcome_unknown",
                    }
            if not completing and not needs_claim:
                receipt.update({
                    "state": "running",
                    "ownerToken": uuid.uuid4().hex,
                    "startedAtEpoch": time.time(),
                })
                return {**_system_operation(receipt, job_id), "status": "claimed", "recovered": True}
        else:
            job = get_job(job_id)
            if not job:
                return {"status": "stale", "reason": "job_not_found", "jobId": job_id}
            if schedule_revision(job) != revision:
                return {"status": "stale", "reason": "revision_mismatch", "jobId": job_id}
            if occurrence_id != revision:
                raise ValueError("Ad-hoc Dreaming occurrence has not been registered.")
            binding = read_json_object(cron_delivery_path(profile_home)).get(job_id)
            if not isinstance(binding, dict) or binding.get("systemType") != "dream":
                raise ValueError("Cron job is not the managed Dreaming schedule.")
            due_at = datetime.fromisoformat(str(job["next_run_at"]).replace("Z", "+00:00"))
            if due_at.timestamp() > time.time():
                raise RuntimeError("System Dreaming occurrence is not due yet.")
            receipt = _new_system_operation(job, revision, occurrence_id, "scheduled")
            receipt["state"] = "claiming"
            receipts[key] = receipt
    if completing:
        _finish_system_schedule_completion(profile_home, job_id=job_id, receipt_key=key)
        return _terminal_system_outcome(read_json_object(path)[key], job_id)
    try:
        claimed = claim_job_for_fire(job_id)
    except Exception:
        if _native_occurrence_unclaimed(get_job(job_id), revision, receipt):
            with json_transaction(path) as receipts:
                receipts[key]["state"] = "queued"
        raise
    if not claimed:
        if _native_occurrence_unclaimed(get_job(job_id), revision, receipt):
            with json_transaction(path) as receipts:
                receipts[key]["state"] = "queued"
            return {"status": "retryable_preclaim", "jobId": job_id}
        return {"status": "in_progress", "jobId": job_id}
    with json_transaction(path) as receipts:
        current = receipts[key]
        if current.get("ownerToken") != receipt["ownerToken"]:
            raise RuntimeError("System schedule claim lost ownership.")
        current.update({
            "state": "running",
            "claimedNextRunAt": (get_job(job_id) or {}).get("next_run_at"),
        })
        return {**_system_operation(current, job_id), "status": "claimed", "recovered": False}


def _new_system_operation(
    job: dict[str, Any], revision: str, occurrence_id: str, kind: str
) -> dict[str, Any]:
    operation_id = hashlib.sha256(
        json.dumps([str(job["id"]), occurrence_id]).encode("utf-8")
    ).hexdigest()
    return {
        "operationId": operation_id,
        "sessionId": f"scheduled-dream:{operation_id}",
        "ownerToken": uuid.uuid4().hex,
        "operationKind": kind,
        "state": "queued",
        "phase": "pending",
        "checkpoint": {},
        "revision": revision,
        "occurrenceId": occurrence_id,
        "startedAtEpoch": time.time(),
        "completedAt": None,
        "success": None,
        "lastRunAtBefore": job.get("last_run_at"),
        "scheduledNextRunAt": job.get("next_run_at"),
        "claimedNextRunAt": None,
    }


def _native_occurrence_unclaimed(
    job: dict[str, Any] | None, revision: str, receipt: dict[str, Any],
) -> bool:
    from azure_cron_provider import schedule_revision

    return bool(
        job and "lastRunAtBefore" in receipt and "scheduledNextRunAt" in receipt
        and not job.get("fire_claim") and schedule_revision(job) == revision
        and job.get("last_run_at") == receipt["lastRunAtBefore"]
        and job.get("next_run_at") == receipt["scheduledNextRunAt"]
        and not receipt.get("nativeOutputPath") and not receipt.get("nativeExecutionId")
    )


def _terminal_system_outcome(receipt: dict[str, Any], job_id: str) -> dict[str, Any]:
    outcome = (receipt.get("summary") or {}).get("outcome")
    status = "duplicate" if receipt.get("success") or outcome == "skipped" else (
        "unknown" if outcome == "unknown" else "failed"
    )
    return {"status": status, "jobId": job_id, "reason": str(receipt.get("error") or "")}


def _system_operation(receipt: dict[str, Any], job_id: str) -> dict[str, Any]:
    return {
        "jobId": job_id,
        **{key: receipt.get(key) for key in (
            "revision", "occurrenceId", "operationId", "sessionId", "ownerToken",
            "operationKind", "phase", "checkpoint",
        )},
    }


def get_system_schedule_checkpoint(
    profile_home: Path, *, job_id: str, revision: str, occurrence_id: str
) -> dict[str, Any]:
    receipt = read_json_object(system_schedule_receipts_path(profile_home)).get(
        _receipt_key(job_id, occurrence_id)
    )
    if not isinstance(receipt, dict) or receipt.get("revision") != revision:
        raise ValueError("System schedule occurrence does not exist.")
    return _system_operation(receipt, job_id)


def checkpoint_system_schedule(
    profile_home: Path, *, job_id: str, revision: str, occurrence_id: str,
    owner_token: str, expected_phase: str, phase: str, payload: dict[str, Any],
) -> dict[str, Any]:
    phases = ["pending", "dream_started", "dream_completed", "status_completed"]
    if expected_phase not in phases or phase not in phases or (
        phases.index(phase) != phases.index(expected_phase) + 1
    ):
        raise ValueError("Invalid system schedule phase transition.")
    with json_transaction(system_schedule_receipts_path(profile_home)) as receipts:
        receipt = receipts.get(_receipt_key(job_id, occurrence_id))
        if not isinstance(receipt, dict) or receipt.get("revision") != revision:
            raise ValueError("System schedule occurrence does not exist.")
        if not owner_token or receipt.get("ownerToken") != owner_token:
            raise RuntimeError("System schedule checkpoint lost ownership.")
        if receipt.get("state") != "running":
            raise RuntimeError("System schedule is not running.")
        if receipt.get("phase") == phase:
            if all(receipt.get("checkpoint", {}).get(key) == value for key, value in payload.items()):
                return _system_operation(receipt, job_id)
            raise RuntimeError("System schedule checkpoint has conflicting data.")
        if receipt.get("phase") != expected_phase:
            raise RuntimeError("System schedule phase changed concurrently.")
        receipt["checkpoint"].update(payload)
        receipt["phase"] = phase
        receipt["startedAtEpoch"] = time.time()
        return _system_operation(receipt, job_id)


def complete_system_schedule(
    profile_home: Path,
    *,
    job_id: str,
    revision: str,
    occurrence_id: str,
    success: bool,
    error: str = "",
    summary: dict[str, Any] | None = None,
    owner_token: str = "",
) -> dict[str, Any]:
    path = system_schedule_receipts_path(profile_home)
    key = _receipt_key(job_id, occurrence_id)
    with json_transaction(path) as receipts:
        receipt = receipts.get(key)
        if not isinstance(receipt, dict) or receipt.get("revision", revision) != revision:
            raise ValueError("System schedule receipt does not exist.")
        if receipt.get("state") == "completed":
            return _terminal_system_outcome(receipt, job_id)
        if not owner_token or receipt.get("ownerToken") != owner_token:
            raise RuntimeError("System schedule completion lost ownership.")
        if success and receipt.get("phase") != "status_completed":
            raise RuntimeError("Successful system Dream completion requires its status checkpoint.")
        if receipt.get("state") != "completing":
            receipt.update({
                "state": "completing",
                "success": success,
                "errorType": "scheduled_dream_failed" if not success else None,
                "error": error[:1000],
                "summary": summary or {},
            })
    return _finish_system_schedule_completion(
        profile_home,
        job_id=job_id,
        receipt_key=key,
    )


def _finish_system_schedule_completion(
    profile_home: Path,
    *,
    job_id: str,
    receipt_key: str,
) -> dict[str, Any]:
    from cron.jobs import get_job, mark_job_run
    from cron.scheduler_provider import resolve_cron_scheduler

    path = system_schedule_receipts_path(profile_home)
    with json_transaction(path) as receipts:
        receipt = receipts[receipt_key]
        if receipt.get("state") == "completed":
            return {"status": "duplicate", "jobId": job_id}
        if receipt.get("operationKind") != "adhoc":
            job = get_job(job_id)
            if job is not None and job.get("last_run_at") == receipt.get("lastRunAtBefore"):
                if receipt.get("claimedNextRunAt") is not None and (
                    job.get("next_run_at") != receipt["claimedNextRunAt"]
                ):
                    raise RuntimeError("Native schedule advanced beyond this occurrence's claim.")
                mark_job_run(
                    job_id, bool(receipt.get("success")), str(receipt.get("error") or "") or None,
                )
    if receipt.get("operationKind") != "adhoc":
        provider = resolve_cron_scheduler()
        if receipt.get("scheduledNextRunAt"):
            provider.forget_fired_occurrence(job_id, receipt["revision"], receipt["scheduledNextRunAt"])
        provider.reconcile()
    with json_transaction(path) as receipts:
        receipt = receipts[receipt_key]
        receipt["state"] = "completed"
        receipt["completedAt"] = datetime.now(UTC).isoformat()
        _prune_system_schedule_receipts(receipts)
    refreshed = get_job(job_id)
    return {
        "status": "completed",
        "jobId": job_id,
        "success": bool(receipt.get("success")),
        "nextRunAt": (refreshed or {}).get("next_run_at"),
    }


def enqueue_system_dream_now(profile_home: Path) -> dict[str, Any]:
    from cron.jobs import list_jobs
    from cron.scheduler_provider import resolve_cron_scheduler
    from azure_cron_provider import schedule_revision

    job = next(
        (
            candidate
            for candidate in list_jobs(include_disabled=True)
            if candidate.get("name") == SYSTEM_DREAM_JOB_NAME
            and candidate.get("prompt") == SYSTEM_DREAM_PROMPT
        ),
        None,
    )
    if not job:
        raise ValueError("Platform Dreaming schedule does not exist.")
    binding = read_json_object(cron_delivery_path(profile_home)).get(
        str(job["id"])
    )
    if not isinstance(binding, dict) or binding.get("systemType") != "dream":
        raise ValueError("Platform Dreaming binding is missing.")
    provider = resolve_cron_scheduler()
    enqueue = getattr(provider, "enqueue_now", None)
    if not callable(enqueue):
        raise RuntimeError(
            "Active cron provider cannot enqueue Platform Dreaming."
        )
    revision = schedule_revision(job)
    occurrence_id = f"manual-{uuid.uuid4().hex}"
    with json_transaction(system_schedule_receipts_path(profile_home)) as receipts:
        receipts[_receipt_key(str(job["id"]), occurrence_id)] = _new_system_operation(
            job, revision, occurrence_id, "adhoc"
        )
    enqueued = enqueue(job, revision, binding, occurrence_id=occurrence_id)
    return {
        "status": "enqueued",
        "jobId": str(job["id"]),
        "revision": revision,
        "occurrenceId": enqueued["occurrenceId"],
        "messageId": enqueued["messageId"],
        "scheduledNextRunAt": job.get("next_run_at"),
    }


def _prune_system_schedule_receipts(
    receipts: dict[str, Any],
    *,
    keep_completed_per_job: int = 20,
) -> None:
    completed_by_job: dict[str, list[tuple[str, str]]] = {}
    for key, receipt in receipts.items():
        if not isinstance(receipt, dict) or receipt.get("state") != "completed":
            continue
        job_id, separator, _ = key.rpartition(":")
        if separator:
            completed_by_job.setdefault(job_id, []).append(
                (str(receipt.get("completedAt") or ""), key)
            )
    for values in completed_by_job.values():
        values.sort(reverse=True)
        for _, key in values[keep_completed_per_job:]:
            receipt = receipts[key]
            receipt["checkpoint"] = {}
            receipt["summary"] = {
                name: value for name, value in (receipt.get("summary") or {}).items() if name == "outcome"
            }


def _delivery_output(output: str) -> str:
    output = output.replace("\r\n", "\n")
    marker = "\n## Response\n\n"
    if marker not in output:
        return "The scheduled task failed. Check the Worker diagnostics for details."
    return output.rsplit(marker, 1)[1].strip()


def _record_cron_output(
    profile_home: Path,
    *,
    job_id: str,
    revision: str,
    execution_id: str,
    output_path: Path,
    native_execution_id: str = "",
) -> None:
    output_path = output_path.resolve()
    output_path.relative_to((profile_home / "cron" / "output" / job_id).resolve())
    content = output_path.read_bytes()
    with json_transaction(cron_delivery_receipts_path(profile_home)) as receipts:
        receipt = receipts.get(_receipt_key(job_id, revision))
        if (
            not isinstance(receipt, dict)
            or receipt.get("executionId") != execution_id
            or receipt.get("state") != "executing"
        ):
            raise RuntimeError("Cron execution no longer owns the output receipt.")
        receipt.update({
            "nativeOutputPath": str(output_path.relative_to(profile_home.resolve())),
            "nativeOutputSha256": hashlib.sha256(content).hexdigest(),
            "nativeExecutionId": native_execution_id,
            "output": _delivery_output(content.decode("utf-8")),
        })


def _bound_cron_output(profile_home: Path, receipt: dict[str, Any]) -> bool:
    relative = receipt.get("nativeOutputPath")
    digest = receipt.get("nativeOutputSha256")
    if not receipt.get("executionId") or not relative or not digest:
        return False
    path = (profile_home / str(relative)).resolve()
    try:
        path.relative_to((profile_home / "cron" / "output").resolve())
    except ValueError:
        return False
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest


def _cron_execution_outcome(
    profile_home: Path, job_id: str, receipt: dict[str, Any],
) -> dict[str, Any]:
    native_id = str(receipt.get("nativeExecutionId") or "")
    if native_id:
        from cron.executions import get_execution

        native = get_execution(native_id)
        expected = receipt.get("scheduledNextRunAt")
        actual = (native or {}).get("scheduled_instant")
        try:
            same_occurrence = bool(expected and actual) and (
                datetime.fromisoformat(str(expected).replace("Z", "+00:00"))
                == datetime.fromisoformat(str(actual).replace("Z", "+00:00"))
            )
        except ValueError:
            same_occurrence = False
        if not native or native.get("id") != native_id or native.get("job_id") != job_id or not same_occurrence:
            return {"state": "unknown", "reason": "The bound native execution does not authenticate this exact occurrence."}
        status = native.get("status")
        if status in {"claimed", "running"}:
            return {"state": "executing"}
        if status in {"failed", "cancelled"}:
            return {"state": "failed", "reason": str(native.get("error") or "Native execution failed."), "lastStatus": "error"}
        if status != "completed" or not _bound_cron_output(profile_home, receipt):
            return {"state": "unknown", "reason": "Native execution has no authenticated final output; inference replay is forbidden."}
        return {"state": "pending_delivery", "lastStatus": "ok"}
    if receipt.get("executionFinished") or receipt.get("nativeOutputPath"):
        return {"state": "unknown", "reason": "Native execution finished without an authenticated execution/output binding."}
    if time.time() - float(receipt.get("startedAtEpoch") or 0) < int(
        os.getenv("SCHEDULER_MAX_LOCK_RENEWAL_SECONDS", "1800")
    ):
        return {"state": "executing"}
    return {
        "state": "unknown",
        "reason": "The scheduled task was interrupted; no unambiguous output is bound to this execution.",
        "lastStatus": "error",
    }


def _reconcile_delivery(profile_home: Path, key: str) -> dict[str, Any]:
    from cron.scheduler_provider import resolve_cron_scheduler
    from cron.jobs import get_job

    path = cron_delivery_receipts_path(profile_home)
    job_id, _, revision = key.rpartition(":")
    with json_transaction(path) as receipts:
        receipt = receipts[key]
        if receipt.get("delivered"):
            return dict(receipt)
        receipt.update(_cron_execution_outcome(profile_home, job_id, receipt))
        if receipt["state"] != "pending_delivery":
            return dict(receipt)
        receipt["nextRunAt"] = (get_job(job_id) or {}).get("next_run_at")
        receipt = dict(receipt)
    if not receipt.get("reconciled"):
        reconciled = False
        error = ""
        try:
            provider = resolve_cron_scheduler()
            provider.forget_fired_occurrence(job_id, revision, receipt["scheduledNextRunAt"])
            provider.reconcile()
            reconciled = True
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:1000]}"
        with json_transaction(path) as receipts:
            current = receipts[key]
            if current.get("executionId") == receipt.get("executionId"):
                current.update(reconciled=reconciled, reconciliationError=error)
            receipt = dict(current)
    return receipt


def _cron_result(receipt: dict[str, Any], job_id: str) -> dict[str, Any]:
    state = receipt["state"]
    status = "duplicate" if receipt.get("delivered") else "in_progress" if state == "executing" else state
    return {**receipt, "status": status, "jobId": job_id}


def fire_cron_job(
    profile_home: Path,
    *,
    job_id: str,
    revision: str,
) -> dict[str, Any]:
    from cron.jobs import get_job
    from cron.scheduler_provider import resolve_cron_scheduler
    from azure_cron_provider import schedule_revision

    receipts_path = cron_delivery_receipts_path(profile_home)
    receipt_key = _receipt_key(job_id, revision)
    execute = False
    with json_transaction(receipts_path) as receipts:
        receipt = receipts.get(receipt_key)
        if isinstance(receipt, dict):
            if receipt.get("delivered"):
                return {**receipt, "status": "duplicate", "jobId": job_id}
            if receipt.get("state") == "executing":
                receipt.update(_cron_execution_outcome(profile_home, job_id, receipt))
                if receipt["state"] == "executing":
                    return {**receipt, "status": "in_progress", "jobId": job_id}
            if receipt.get("state") in {"unknown", "failed"}:
                return {**receipt, "status": receipt["state"], "jobId": job_id}
            if receipt.get("state") == "preclaim_failed":
                if not _native_occurrence_unclaimed(get_job(job_id), revision, receipt):
                    return {"status": "unknown", "jobId": job_id, "reason": "Native occurrence changed before retry."}
                receipt.update(state="executing", startedAtEpoch=time.time())
                execute = True
        else:
            job = get_job(job_id)
            if not job:
                return {"status": "stale", "reason": "job_not_found", "jobId": job_id}
            if schedule_revision(job) != revision:
                return {"status": "stale", "reason": "revision_mismatch", "jobId": job_id}
            if job.get("script") or job.get("no_agent"):
                raise ValueError("Hosted scheduled scripts are not allowed.")
            due_at = datetime.fromisoformat(str(job["next_run_at"]).replace("Z", "+00:00"))
            if due_at.timestamp() > time.time():
                raise RuntimeError("Cron occurrence is not due yet.")
            binding = read_json_object(cron_delivery_path(profile_home)).get(job_id) or {}
            reference_key = str(binding.get("referenceKey") or "")
            receipt = {
                "executionId": uuid.uuid4().hex,
                "output": "",
                "lastStatus": None,
                "nextRunAt": job.get("next_run_at"),
                "deliveryReference": (
                    get_delivery_reference(profile_home, reference_key) if reference_key else None
                ),
                "deliveryMode": str(job.get("deliver") or "local"),
                "delivered": False,
                "state": "executing",
                "startedAtEpoch": time.time(),
                "reconciled": False,
                "lastRunAtBefore": job.get("last_run_at"),
                "scheduledNextRunAt": job.get("next_run_at"),
            }
            receipts[receipt_key] = receipt
            execute = True
        execution_id = receipt.get("executionId")
    if not execute:
        receipt = _reconcile_delivery(profile_home, receipt_key)
        return _cron_result(receipt, job_id)

    provider = resolve_cron_scheduler()
    try:
        ran = provider.fire_due(
            job_id,
            output_callback=lambda path, native_id="": _record_cron_output(
                profile_home,
                job_id=job_id,
                revision=revision,
                execution_id=execution_id,
                output_path=path,
                native_execution_id=native_id,
            ),
        )
    except Exception:
        captured = False
        with json_transaction(receipts_path) as receipts:
            receipt = receipts[receipt_key]
            captured = bool(receipt.get("nativeExecutionId") or receipt.get("nativeOutputPath"))
            if not captured and _native_occurrence_unclaimed(get_job(job_id), revision, receipt):
                receipt["state"] = "preclaim_failed"
        if captured:
            return _cron_result(_reconcile_delivery(profile_home, receipt_key), job_id)
        raise
    refreshed = get_job(job_id)
    with json_transaction(receipts_path) as receipts:
        receipt = receipts[receipt_key]
        if receipt.get("delivered"):
            return {**receipt, "status": "duplicate", "jobId": job_id}
        if receipt.get("executionId") != execution_id:
            raise RuntimeError("Cron execution lost receipt ownership.")
        if not ran and not receipt.get("nativeOutputPath") and (refreshed or {}).get("fire_claim"):
            return {**receipt, "status": "in_progress", "jobId": job_id}
        if not ran and _native_occurrence_unclaimed(refreshed, revision, receipt):
            receipt["state"] = "preclaim_failed"
            return {**receipt, "status": "retryable_preclaim", "jobId": job_id}
        receipt.update({
            "executionFinished": True,
            "lastStatus": (refreshed or {}).get("last_status"),
            "nextRunAt": (refreshed or {}).get("next_run_at"),
            "reconciled": False,
        })
    return _cron_result(_reconcile_delivery(profile_home, receipt_key), job_id)


def known_cron_result(
    profile_home: Path, *, job_id: str, revision: str,
    execution_id: str, output_sha256: str,
) -> dict[str, Any]:
    """Return only an authenticated completed result; never claim or execute a job."""
    receipt = read_json_object(cron_delivery_receipts_path(profile_home)).get(_receipt_key(job_id, revision))
    if not isinstance(receipt, dict) or receipt.get("nativeExecutionId") != execution_id:
        raise ValueError("The exact native execution is not bound to this occurrence.")
    if receipt.get("delivered"):
        if receipt.get("outputSha256") != output_sha256:
            raise ValueError("The acknowledged output digest does not match.")
        from cron.scheduler_provider import resolve_cron_scheduler

        provider = resolve_cron_scheduler()
        provider.forget_fired_occurrence(job_id, revision, receipt["scheduledNextRunAt"])
        provider.reconcile()
        return {**receipt, "status": "delivered"}
    if _cron_execution_outcome(profile_home, job_id, receipt)["state"] != "pending_delivery":
        raise ValueError("The native execution has no authenticated completed result.")
    output = str(receipt.get("output") or "")
    if not output.strip() or hashlib.sha256(output.encode("utf-8")).hexdigest() != output_sha256:
        raise ValueError("The stored delivery output does not match the reviewed digest.")
    if receipt.get("state") != "pending_delivery":
        with json_transaction(cron_delivery_receipts_path(profile_home)) as receipts:
            current = receipts[_receipt_key(job_id, revision)]
            if current.get("nativeExecutionId") != execution_id or current.get("nativeOutputSha256") != receipt.get("nativeOutputSha256"):
                raise ValueError("The native result changed during validation.")
            current["state"] = "pending_delivery"
    return {**_reconcile_delivery(profile_home, _receipt_key(job_id, revision)), "status": "pending_delivery"}


def acknowledge_cron_delivery(
    profile_home: Path,
    *,
    job_id: str,
    revision: str,
    delivery_activity_id: str = "",
) -> dict[str, Any]:
    path = cron_delivery_receipts_path(profile_home)
    key = _receipt_key(job_id, revision)
    with json_transaction(path) as receipts:
        receipt = receipts.get(key)
        if not isinstance(receipt, dict):
            raise ValueError("Cron delivery receipt does not exist.")
        if not receipt.get("delivered"):
            if receipt.get("state") != "pending_delivery":
                raise ValueError("Cron result is not ready for delivery acknowledgement.")
            output = str(receipt.get("output") or "")
            receipt["outputSha256"] = hashlib.sha256(output.encode("utf-8")).hexdigest()
            receipt["hasOutput"] = bool(output)
            receipt["output"] = ""
            receipt["deliveryReference"] = None
            receipt["delivered"] = True
            receipt["deliveredAt"] = datetime.now(UTC).isoformat()
            receipt["deliveryActivityId"] = delivery_activity_id
            receipt["state"] = "delivered"
            _prune_delivery_receipts(receipts)
    return {"status": "delivered", "jobId": job_id}


def _prune_delivery_receipts(
    receipts: dict[str, Any],
    *,
    keep_delivered_per_job: int = 20,
) -> None:
    delivered_by_job: dict[str, list[tuple[str, str]]] = {}
    for key, value in receipts.items():
        if not isinstance(value, dict):
            continue
        job_id, separator, _ = key.rpartition(":")
        if not separator:
            continue
        if value.get("delivered"):
            delivered_by_job.setdefault(job_id, []).append(
                (str(value.get("deliveredAt") or ""), key)
            )
    for values in delivered_by_job.values():
        values.sort(reverse=True)
        for _, key in values[keep_delivered_per_job:]:
            receipt = receipts[key]
            receipts[key] = {
                name: receipt[name] for name in (
                    "state", "delivered", "deliveredAt", "deliveryActivityId", "outputSha256",
                    "hasOutput", "executionId", "lastStatus", "nextRunAt",
                ) if name in receipt
            }


def rearm_preclaim_schedule(
    profile_home: Path, *, job_id: str, revision: str, repair_id: str, operator: str, reason: str,
) -> dict[str, Any]:
    from cron.jobs import get_job
    from cron.scheduler_provider import resolve_cron_scheduler
    from azure_cron_provider import schedule_revision

    if not repair_id.strip() or len(repair_id) > 128 or not operator.strip() or not reason.strip():
        raise ValueError("Repair requires a stable bounded repairId, operator identity and reason.")
    repair_key = hashlib.sha256(json.dumps([job_id, revision, repair_id]).encode()).hexdigest()
    path = profile_home / "local" / "schedule-repairs.json"
    previous = read_json_object(path).get(repair_key)
    if previous and previous.get("result"):
        return {**previous["result"], "status": "duplicate"}
    job = get_job(job_id)
    if not job or schedule_revision(job) != revision or job.get("fire_claim"):
        raise ValueError("Repair requires the unchanged native occurrence with no native fire claim.")
    binding = read_json_object(cron_delivery_path(profile_home)).get(job_id)
    if not isinstance(binding, dict):
        raise ValueError("Repair requires a native delivery binding.")
    if job.get("script") or job.get("no_agent") or not job.get("enabled") or job.get("state") == "paused":
        raise ValueError("Repair requires an enabled agent schedule.")
    if datetime.fromisoformat(str(job["next_run_at"]).replace("Z", "+00:00")).timestamp() > time.time():
        raise ValueError("Repair is only available after the occurrence is due.")
    key = _receipt_key(job_id, revision)
    cron_receipt = read_json_object(cron_delivery_receipts_path(profile_home)).get(key)
    if cron_receipt and not (
        cron_receipt.get("state") == "preclaim_failed" and _native_occurrence_unclaimed(job, revision, cron_receipt)
    ):
        raise ValueError("Native execution was already admitted; rearm is forbidden.")
    system = read_json_object(system_schedule_receipts_path(profile_home)).get(key)
    if system and system.get("state") != "queued":
        raise ValueError("System occurrence already crossed native admission; rearm is forbidden.")
    with json_transaction(path) as repairs:
        previous = repairs.get(repair_key)
        if previous and previous.get("result"):
            return {**previous["result"], "status": "duplicate"}
        repairs[repair_key] = {
            "jobId": job_id, "revision": revision, "repairId": repair_id,
            "operator": operator.strip(), "reason": reason.strip(),
        }
    result = resolve_cron_scheduler().rearm(job, revision, binding, repair_id=repair_id)
    with json_transaction(path) as repairs:
        repairs[repair_key]["result"] = result
    return {**result, "status": "rearmed"}


def resolve_schedule(
    profile_home: Path, *, job_id: str, revision: str, occurrence_id: str,
    operator: str, reason: str, kind: str,
) -> dict[str, Any]:
    if kind not in {"dream", "cron"} or not operator.strip() or not reason.strip():
        raise ValueError("Resolution requires cron/dream, operator identity and a reason; it only skips, never replays.")
    path = system_schedule_receipts_path(profile_home) if kind == "dream" else cron_delivery_receipts_path(profile_home)
    key = _receipt_key(job_id, occurrence_id if kind == "dream" else revision)
    finish_dream = False
    owner_token = ""
    with json_transaction(path) as receipts:
        receipt = receipts.get(key)
        if not isinstance(receipt, dict) or (kind == "dream" and receipt.get("revision") != revision):
            raise ValueError("Schedule occurrence does not exist.")
        finish_dream = kind == "dream" and receipt.get("state") in {"running", "completing"}
        if receipt.get("resolution") and not finish_dream:
            return {"status": "duplicate", "jobId": job_id}
        if finish_dream and receipt.get("state") == "running" and (
            receipt.get("phase") not in {"dream_completed", "status_completed"}
            or time.time() - float(receipt.get("startedAtEpoch") or 0)
            < int(os.getenv("SCHEDULER_MAX_LOCK_RENEWAL_SECONDS", "1800"))
        ):
            raise ValueError("A live or inference-ambiguous Dream cannot be resolved as completed work.")
        if not finish_dream and receipt.get("state") not in {"unknown", "failed", "completed", "pending_delivery"}:
            raise ValueError("Only a terminal native occurrence can be explicitly skipped.")
        if kind == "dream" and receipt.get("success"):
            raise ValueError("A successful Dream cannot be reclassified.")
        receipt.setdefault("resolution", {
            "operator": operator.strip(), "reason": reason.strip(),
            "skippedAt": datetime.now(UTC).isoformat(),
        })
        if kind == "dream":
            receipt.setdefault("summary", {})["outcome"] = "skipped"
            owner_token = str(receipt.get("ownerToken") or "")
        else:
            receipt.update(state="skipped", delivered=True, output="", deliveryReference=None)
    if finish_dream:
        complete_system_schedule(
            profile_home, job_id=job_id, revision=revision, occurrence_id=occurrence_id,
            success=False, owner_token=owner_token, error=reason, summary={"outcome": "skipped"},
        )
    return {"status": "skipped", "jobId": job_id, "replayed": False}


def verify_schedule_repair_grant(grant: dict[str, Any], *, action: str) -> tuple[dict[str, Any], str]:
    from collective_learning import _verify_receipt_signature

    if set(grant) != {
        "version", "action", "workerId", "operator", "authorizedAt", "expiresAt", "payload", "signature",
    } or grant.get("version") != "1.0" or grant.get("action") != action:
        raise ValueError("A gateway-authorized schedule repair grant is required.")
    if not os.getenv("WORKER_ID") or grant.get("workerId") != os.environ["WORKER_ID"]:
        raise ValueError("Schedule repair authorization belongs to another Worker.")
    issued_at, expires_at = grant.get("authorizedAt"), grant.get("expiresAt")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (issued_at, expires_at)):
        raise ValueError("Schedule repair authorization has invalid timestamps.")
    if not issued_at <= time.time() <= expires_at or not 0 < expires_at - issued_at <= 300:
        raise ValueError("Schedule repair authorization is expired or not yet valid.")
    if not isinstance(grant.get("payload"), dict) or not isinstance(grant.get("operator"), str) or not grant["operator"].startswith("entra:"):
        raise ValueError("Schedule repair authorization has no verified owner or request.")
    _verify_receipt_signature(grant)
    return grant["payload"], grant["operator"]


def cron_delivery_receipt_status(
    profile_home: Path,
    *,
    job_id: str,
    revision: str,
) -> dict[str, Any]:
    receipt = read_json_object(cron_delivery_receipts_path(profile_home)).get(
        _receipt_key(job_id, revision)
    )
    if not isinstance(receipt, dict):
        return {"status": "pending", "jobId": job_id}
    output = str(receipt.get("output") or "")
    output_sha256 = str(receipt.get("outputSha256") or "")
    if not output_sha256:
        output_sha256 = hashlib.sha256(output.encode("utf-8")).hexdigest()
    state = receipt.get("state")
    if not receipt.get("delivered") and state in {"executing", "pending_delivery"}:
        outcome = _cron_execution_outcome(profile_home, job_id, receipt)
        state = outcome["state"]
        receipt = {**receipt, **outcome}
    return {
        "status": (
            "delivered" if receipt.get("delivered") else state if state in {"unknown", "failed"}
            else "in_progress" if state == "executing"
            else "retryable_preclaim" if state == "preclaim_failed" else "pending_delivery"
        ),
        "jobId": job_id,
        "outputSha256": output_sha256,
        "hasOutput": bool(receipt.get("hasOutput")) if receipt.get("delivered") else bool(output),
        "lastStatus": receipt.get("lastStatus"),
        "nextRunAt": receipt.get("nextRunAt"),
    }


def reconcile_cron_provider(profile_home: Path) -> dict[str, Any]:
    from cron.scheduler_provider import resolve_cron_scheduler

    if os.getenv("SERVICEBUS_DREAM_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        ensure_system_dream_schedule(
            profile_home,
            enabled=True,
            schedule=os.getenv(
                "SERVICEBUS_DREAM_CRON_EXPRESSION",
                "0 2 * * *",
            ),
        )
    provider = resolve_cron_scheduler()
    try:
        provider.reconcile()
    except Exception as exc:
        return {
            "provider": provider.name,
            "available": provider.is_available(),
            "status": "error",
            "errorType": exc.__class__.__name__,
            "error": str(exc),
        }
    return {
        "provider": provider.name,
        "available": provider.is_available(),
        "status": "ok",
        "scheduledJobCount": len(
            read_json_object(profile_home / "cron" / "azure-schedules.json")
        ),
    }


def _delivery_reference_diagnostic(
    reference_key: str,
    stored: dict[str, Any],
) -> dict[str, Any]:
    envelope = stored.get("conversation")
    envelope = envelope if isinstance(envelope, dict) else {}
    claims = envelope.get("claims")
    claims = claims if isinstance(claims, dict) else {}
    reference = envelope.get("conversation_reference")
    reference = reference if isinstance(reference, dict) else {}
    conversation = reference.get("conversation")
    conversation = conversation if isinstance(conversation, dict) else {}
    service_url = str(reference.get("serviceUrl") or "")
    return {
        "referenceKey": reference_key,
        "boundary": str(stored.get("boundary") or ""),
        "channelId": str(reference.get("channelId") or ""),
        "conversationType": str(conversation.get("conversationType") or ""),
        "serviceHost": urlparse(service_url).hostname or "",
        "hasConversationId": bool(conversation.get("id")),
        "hasBot": isinstance(reference.get("bot"), dict),
        "hasUser": isinstance(reference.get("user"), dict),
        "claimKeys": sorted(str(key) for key in claims),
        "tokenAudience": str(
            claims.get("aud")
            or claims.get("azp")
            or claims.get("appid")
            or ""
        ),
    }


def cron_diagnostics(profile_home: Path) -> dict[str, Any]:
    references = read_json_object(delivery_references_path(profile_home))
    bindings = read_json_object(cron_delivery_path(profile_home))
    receipts = read_json_object(cron_delivery_receipts_path(profile_home))
    schedules = read_json_object(profile_home / "cron" / "azure-schedules.json")
    system_receipts = read_json_object(system_schedule_receipts_path(profile_home))
    unresolved = [
        receipt for receipt in receipts.values()
        if isinstance(receipt, dict) and not receipt.get("delivered")
    ]
    dream_completed = [
        receipt for receipt in system_receipts.values()
        if isinstance(receipt, dict) and receipt.get("state") == "completed" and receipt.get("success")
    ]
    last_dream = max((str(receipt.get("completedAt") or "") for receipt in dream_completed), default="")
    last_skipped = max(
        (str((receipt.get("resolution") or {}).get("skippedAt") or "")
         for receipt in system_receipts.values() if isinstance(receipt, dict)), default="",
    )
    last_healthy_dream = max(last_dream, last_skipped)
    jobs = list_cron_jobs(profile_home, include_system=True)
    return {
        "scheduleHealth": {
            "unarmedJobIds": [
                job["id"] for job in jobs
                if job["enabled"] and job["state"] != "paused" and job["nextRunAt"]
                and not job["externallyScheduled"]
            ],
            "unknownCronOccurrences": sum(
                receipt.get("state") == "unknown" for receipt in unresolved
            ),
            "pendingReconciliationCount": sum(
                bool(receipt.get("reconciliationError"))
                for receipt in receipts.values() if isinstance(receipt, dict)
            ),
            "overdueUnclaimedJobIds": [
                job["id"] for job in jobs
                if job["enabled"] and job["state"] != "paused" and job["nextRunAt"]
                and time.time() - datetime.fromisoformat(str(job["nextRunAt"]).replace("Z", "+00:00")).timestamp()
                > int(os.getenv("SCHEDULER_MAX_LOCK_RENEWAL_SECONDS", "1800"))
                and _receipt_key(job["id"], job["revision"]) not in receipts
                and _receipt_key(job["id"], job["revision"]) not in system_receipts
            ],
            "lastSuccessfulDreamAt": last_dream or None,
            "lastSuccessfulDreamAgeSeconds": (
                max(0, time.time() - datetime.fromisoformat(last_dream).timestamp()) if last_dream else None
            ),
            "lastExplicitlySkippedDreamAt": last_skipped or None,
            "lastSuccessfulOrSkippedDreamAgeSeconds": (
                max(0, time.time() - datetime.fromisoformat(last_healthy_dream).timestamp())
                if last_healthy_dream else None
            ),
            "receiptRetention": "indefinite-replay-tombstones",
            "dlqDepth": None,
            "dlqDepthSource": "Service Bus ActiveMessages/DeadletteredMessages metrics; journal is not broker depth",
        },
        "unresolvedDeliveryCount": len(unresolved),
        "oldestUnresolvedDeliveryAgeSeconds": max(
            (
                max(0, time.time() - float(receipt["startedAtEpoch"]))
                for receipt in unresolved if receipt.get("startedAtEpoch")
            ),
            default=0,
        ),
        "jobs": jobs,
        "bindings": [
            {
                "jobId": job_id,
                "deliveryMode": (
                    "system"
                    if binding.get("systemType") == "dream"
                    else "local"
                    if binding.get("local") is True
                    else "teams"
                ),
                "referenceKey": str(binding.get("referenceKey") or ""),
                "referenceExists": bool(
                    binding.get("local") is True
                    or binding.get("systemType") == "dream"
                    or binding.get("referenceKey") in references
                ),
                "boundary": str(
                    (
                        references.get(binding.get("referenceKey")) or {}
                    ).get("boundary")
                    or ""
                ),
            }
            for job_id, binding in bindings.items()
            if isinstance(binding, dict)
        ],
        "deliveryReferences": [
            _delivery_reference_diagnostic(reference_key, reference)
            for reference_key, reference in references.items()
            if isinstance(reference, dict)
        ],
        "receipts": [
            {
                "jobId": key.rpartition(":")[0],
                "revision": key.rpartition(":")[2],
                "state": str(receipt.get("state") or ""),
                "delivered": bool(receipt.get("delivered")),
                "hasOutput": bool(
                    receipt.get("hasOutput") or receipt.get("output")
                ),
                "lastStatus": receipt.get("lastStatus"),
                "executionId": receipt.get("executionId"),
                "nativeExecutionId": receipt.get("nativeExecutionId"),
                "reconciliationError": receipt.get("reconciliationError") or None,
                "hasBoundNativeOutput": bool(
                    receipt.get("nativeOutputPath") and receipt.get("nativeOutputSha256")
                ),
                "startedAtEpoch": receipt.get("startedAtEpoch"),
                "deliveredAt": receipt.get("deliveredAt"),
                "hasDeliveryActivityId": bool(
                    receipt.get("deliveryActivityId")
                ),
            }
            for key, receipt in receipts.items()
            if isinstance(receipt, dict)
        ],
        "providerSchedules": [
            {
                "jobId": job_id,
                "fireAt": schedule.get("fireAt"),
                "revision": schedule.get("revision"),
                "hasSequenceNumber": schedule.get("sequenceNumber") is not None,
            }
            for job_id, schedule in schedules.items()
            if isinstance(schedule, dict)
        ],
        "systemReceipts": [
            {
                "jobId": key.rpartition(":")[0],
                "revision": receipt.get("revision"),
                "occurrenceId": (
                    receipt.get("occurrenceId")
                    or key.rpartition(":")[2]
                ),
                "state": str(receipt.get("state") or ""),
                "operationKind": receipt.get("operationKind"),
                "phase": receipt.get("phase"),
                "success": receipt.get("success"),
                "startedAtEpoch": receipt.get("startedAtEpoch"),
                "completedAt": receipt.get("completedAt"),
                "errorType": receipt.get("errorType"),
                "summary": receipt.get("summary") or {},
            }
            for key, receipt in system_receipts.items()
            if isinstance(receipt, dict)
        ],
    }
