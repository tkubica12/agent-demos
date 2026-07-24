from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


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
    references = read_json_object(path)
    references[reference_key] = {
        "boundary": boundary,
        "conversation": conversation,
    }
    atomic_write_json(path, references)
    return {"referenceKey": reference_key, "boundary": boundary}


def get_delivery_reference(
    profile_home: Path,
    reference_key: str,
) -> dict[str, Any] | None:
    value = read_json_object(delivery_references_path(profile_home)).get(reference_key)
    return value if isinstance(value, dict) else None


def list_cron_jobs(profile_home: Path) -> list[dict[str, Any]]:
    from cron.jobs import list_jobs
    from azure_cron_provider import schedule_revision

    scheduled = read_json_object(profile_home / "cron" / "azure-schedules.json")
    result = []
    for job in list_jobs(include_disabled=True):
        revision = schedule_revision(job)
        external = scheduled.get(str(job.get("id") or "")) or {}
        result.append({
            "id": str(job.get("id") or ""),
            "name": str(job.get("name") or ""),
            "enabled": bool(job.get("enabled")),
            "state": str(job.get("state") or ""),
            "nextRunAt": job.get("next_run_at"),
            "revision": revision,
            "script": bool(job.get("script")),
            "noAgent": bool(job.get("no_agent")),
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
    bindings = read_json_object(path)
    bound: list[str] = []
    for job_id in job_ids:
        job = get_job(job_id)
        if not job:
            continue
        if job.get("script") or job.get("no_agent"):
            raise ValueError("Hosted scheduled scripts are not allowed.")
        bindings[job_id] = {"referenceKey": reference_key}
        bound.append(job_id)
    atomic_write_json(path, bindings)
    return {"bound": bound, "referenceKey": reference_key}


def bind_cron_local(
    profile_home: Path,
    *,
    job_ids: list[str],
) -> dict[str, Any]:
    from cron.jobs import get_job

    path = cron_delivery_path(profile_home)
    bindings = read_json_object(path)
    bound: list[str] = []
    for job_id in job_ids:
        job = get_job(job_id)
        if not job:
            continue
        if job.get("script") or job.get("no_agent"):
            raise ValueError("Hosted scheduled scripts are not allowed.")
        bindings[job_id] = {"local": True}
        bound.append(job_id)
    atomic_write_json(path, bindings)
    return {"bound": bound, "deliveryMode": "local"}


def _latest_output_path(profile_home: Path, job_id: str) -> Path | None:
    output_dir = profile_home / "cron" / "output" / job_id
    files = sorted(output_dir.glob("*.md"), reverse=True) if output_dir.is_dir() else []
    return files[0] if files else None


def _output_fingerprint(path: Path | None) -> str:
    if path is None or not path.is_file():
        return ""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"{path.name}:{digest}"


def _delivery_output(profile_home: Path, job_id: str) -> str:
    path = _latest_output_path(profile_home, job_id)
    output = path.read_text(encoding="utf-8") if path else ""
    marker = "\n## Response\n\n"
    if marker not in output:
        return "The scheduled task failed. Check the Worker diagnostics for details."
    return output.rsplit(marker, 1)[1].strip()


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
    receipts = read_json_object(receipts_path)
    receipt_key = _receipt_key(job_id, revision)
    existing_receipt = receipts.get(receipt_key)
    if isinstance(existing_receipt, dict):
        delivered = bool(existing_receipt.get("delivered"))
        if existing_receipt.get("state") == "executing" and not delivered:
            current_output = _latest_output_path(profile_home, job_id)
            current_fingerprint = _output_fingerprint(current_output)
            if current_fingerprint != existing_receipt.get("outputFingerprintBefore"):
                existing_receipt["output"] = _delivery_output(profile_home, job_id)
                existing_receipt["state"] = "pending_delivery"
            else:
                current_job = get_job(job_id)
                can_retry_unclaimed = (
                    current_job is not None
                    and schedule_revision(current_job) == revision
                    and not current_job.get("fire_claim")
                    and current_job.get("last_run_at") == existing_receipt.get("lastRunAtBefore")
                )
                if can_retry_unclaimed:
                    receipts.pop(receipt_key, None)
                    atomic_write_json(receipts_path, receipts)
                    existing_receipt = None
                else:
                    started_at = float(existing_receipt.get("startedAtEpoch") or 0)
                    lease_seconds = int(
                        os.getenv("SCHEDULER_MAX_LOCK_RENEWAL_SECONDS", "1800")
                    )
                    if started_at and time.time() - started_at < lease_seconds:
                        return {
                            **existing_receipt,
                            "status": "in_progress",
                            "jobId": job_id,
                        }
                    existing_receipt.update({
                        "output": (
                            "The scheduled task was interrupted before it produced a result. "
                            "Check the Worker diagnostics and run it again if needed."
                        ),
                        "lastStatus": "error",
                        "state": "pending_delivery",
                        "reconciled": False,
                    })
            if isinstance(existing_receipt, dict):
                if (
                    existing_receipt.get("state") == "pending_delivery"
                    and not existing_receipt.get("reconciled")
                ):
                    resolve_cron_scheduler().reconcile()
                    existing_receipt["reconciled"] = True
                receipts[receipt_key] = existing_receipt
                _prune_delivery_receipts(receipts)
                atomic_write_json(receipts_path, receipts)
                return {
                    **existing_receipt,
                    "status": "pending_delivery",
                    "jobId": job_id,
                }
        if existing_receipt is not None:
            if (
                not delivered
                and existing_receipt.get("state") == "pending_delivery"
                and not existing_receipt.get("reconciled")
            ):
                resolve_cron_scheduler().reconcile()
                existing_receipt["reconciled"] = True
                receipts[receipt_key] = existing_receipt
                _prune_delivery_receipts(receipts)
                atomic_write_json(receipts_path, receipts)
            return {
                **existing_receipt,
                "status": "duplicate" if delivered else "pending_delivery",
                "jobId": job_id,
            }

    job = get_job(job_id)
    if not job:
        return {"status": "stale", "reason": "job_not_found", "jobId": job_id}
    current_revision = schedule_revision(job)
    if current_revision != revision:
        return {
            "status": "stale",
            "reason": "revision_mismatch",
            "jobId": job_id,
            "currentRevision": current_revision,
        }
    if job.get("script") or job.get("no_agent"):
        raise ValueError("Hosted scheduled scripts are not allowed.")
    binding = read_json_object(cron_delivery_path(profile_home)).get(job_id) or {}
    reference_key = str(binding.get("referenceKey") or "")
    reference = get_delivery_reference(profile_home, reference_key) if reference_key else None
    output_before = _latest_output_path(profile_home, job_id)
    receipts[receipt_key] = {
        "output": "",
        "lastStatus": None,
        "nextRunAt": job.get("next_run_at"),
        "deliveryReference": reference,
        "deliveryMode": str(job.get("deliver") or "local"),
        "delivered": False,
        "state": "executing",
        "lastRunAtBefore": job.get("last_run_at"),
        "outputFingerprintBefore": _output_fingerprint(output_before),
        "startedAtEpoch": time.time(),
        "reconciled": False,
    }
    _prune_delivery_receipts(receipts)
    atomic_write_json(receipts_path, receipts)

    provider = resolve_cron_scheduler()
    ran = provider.fire_due(job_id)
    refreshed = get_job(job_id)
    current_output = _latest_output_path(profile_home, job_id)
    output_changed = (
        _output_fingerprint(current_output)
        != receipts[receipt_key]["outputFingerprintBefore"]
    )
    failed_run = bool(
        not ran
        and (
            refreshed is None
            or (
                refreshed.get("last_status") == "error"
                and refreshed.get("last_run_at") != job.get("last_run_at")
            )
        )
    )
    if not ran and not output_changed and not failed_run:
        if refreshed and refreshed.get("fire_claim"):
            return {
                **receipts[receipt_key],
                "status": "in_progress",
                "jobId": job_id,
            }
        receipts.pop(receipt_key, None)
        atomic_write_json(receipts_path, receipts)
        return {"status": "duplicate", "jobId": job_id}

    latest_receipts = read_json_object(receipts_path)
    latest_receipt = latest_receipts.get(receipt_key)
    if isinstance(latest_receipt, dict):
        if latest_receipt.get("delivered"):
            return {"status": "duplicate", "jobId": job_id}
        if latest_receipt.get("state") == "pending_delivery":
            return {
                **latest_receipt,
                "status": "pending_delivery",
                "jobId": job_id,
            }
    receipts = latest_receipts
    result = {
        "status": "completed",
        "jobId": job_id,
        "output": (
            _delivery_output(profile_home, job_id)
            if output_changed
            else "The scheduled task failed. Check the Worker diagnostics for details."
        ),
        "lastStatus": (refreshed or {}).get("last_status"),
        "nextRunAt": (refreshed or {}).get("next_run_at"),
        "deliveryReference": reference,
        "deliveryMode": str(job.get("deliver") or "local"),
    }
    receipts[receipt_key] = {
        "output": result["output"],
        "lastStatus": result["lastStatus"],
        "nextRunAt": result["nextRunAt"],
        "deliveryReference": reference,
        "deliveryMode": result["deliveryMode"],
        "delivered": False,
        "state": "pending_delivery",
        "lastRunAtBefore": job.get("last_run_at"),
        "outputFingerprintBefore": latest_receipt.get("outputFingerprintBefore")
        if isinstance(latest_receipt, dict)
        else "",
        "startedAtEpoch": latest_receipt.get("startedAtEpoch")
        if isinstance(latest_receipt, dict)
        else time.time(),
        "reconciled": True,
    }
    _prune_delivery_receipts(receipts)
    atomic_write_json(receipts_path, receipts)
    return result


def acknowledge_cron_delivery(
    profile_home: Path,
    *,
    job_id: str,
    revision: str,
    delivery_activity_id: str = "",
) -> dict[str, Any]:
    path = cron_delivery_receipts_path(profile_home)
    receipts = read_json_object(path)
    key = _receipt_key(job_id, revision)
    receipt = receipts.get(key)
    if not isinstance(receipt, dict):
        raise ValueError("Cron delivery receipt does not exist.")
    output = str(receipt.get("output") or "")
    receipt["outputSha256"] = hashlib.sha256(output.encode("utf-8")).hexdigest()
    receipt["hasOutput"] = bool(output)
    receipt["output"] = ""
    receipt["deliveryReference"] = None
    receipt["delivered"] = True
    receipt["deliveredAt"] = datetime.now(UTC).isoformat()
    receipt["deliveryActivityId"] = delivery_activity_id
    receipt["state"] = "delivered"
    receipts[key] = receipt
    _prune_delivery_receipts(receipts)
    atomic_write_json(path, receipts)
    return {"status": "delivered", "jobId": job_id}


def _prune_delivery_receipts(
    receipts: dict[str, Any],
    *,
    keep_delivered_per_job: int = 20,
    keep_pending_per_job: int = 20,
) -> None:
    delivered_by_job: dict[str, list[tuple[str, str]]] = {}
    pending_by_job: dict[str, list[tuple[float, str]]] = {}
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
        elif value.get("state") != "executing":
            pending_by_job.setdefault(job_id, []).append(
                (float(value.get("startedAtEpoch") or 0), key)
            )
    for values in delivered_by_job.values():
        values.sort(reverse=True)
        for _, key in values[keep_delivered_per_job:]:
            receipts.pop(key, None)
    for values in pending_by_job.values():
        values.sort(reverse=True)
        for _, key in values[keep_pending_per_job:]:
            receipts.pop(key, None)


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
    return {
        "status": "delivered" if receipt.get("delivered") else "pending_delivery",
        "jobId": job_id,
        "outputSha256": output_sha256,
        "hasOutput": bool(receipt.get("hasOutput")) if receipt.get("delivered") else bool(output),
        "lastStatus": receipt.get("lastStatus"),
        "nextRunAt": receipt.get("nextRunAt"),
    }


def reconcile_cron_provider(profile_home: Path) -> dict[str, Any]:
    from cron.scheduler_provider import resolve_cron_scheduler

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
    return {
        "jobs": list_cron_jobs(profile_home),
        "bindings": [
            {
                "jobId": job_id,
                "deliveryMode": "local" if binding.get("local") is True else "teams",
                "referenceKey": str(binding.get("referenceKey") or ""),
                "referenceExists": bool(
                    binding.get("local") is True
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
    }
