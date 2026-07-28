from __future__ import annotations

import json
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from . import collaboration_mcp as collaboration


BACKGROUND_DEADLINE_SECONDS = 24 * 60 * 60
BACKGROUND_RETRY_DELAYS = (120, 300, 900, 1800, 3600)
RECEIPT_RETENTION_SECONDS = 7 * 24 * 60 * 60


def receipt_directory() -> Path:
    return collaboration.collaboration_workspace() / "_document-receipts"


def receipt_path(operation_id: str) -> Path:
    if not collaboration.PENDING_PUBLISH_ID_PATTERN.fullmatch(operation_id):
        raise ValueError("Invalid document operation ID.")
    return receipt_directory() / f"{operation_id}.json"


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def read_receipt(operation_id: str) -> dict[str, Any] | None:
    path = receipt_path(operation_id)
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def cleanup_receipts() -> int:
    root = receipt_directory()
    if not root.is_dir():
        return 0
    removed = 0
    now = time.time()
    for path in root.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            expires_at = float(payload.get("expiresAtUnix") or 0)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            expires_at = 0
        if expires_at <= now:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def public_drive_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in (
            "id",
            "name",
            "webUrl",
            "eTag",
            "lastModifiedDateTime",
            "lastModifiedBy",
        )
        if item.get(key) is not None
    }


def terminal_result(receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "duplicate",
        "operationId": receipt["operationId"],
        "terminalStatus": receipt["status"],
        "deliveryReference": receipt.get("deliveryReference"),
        "delivered": bool(receipt.get("deliveryActivityId")),
        "deliveryActivityId": str(
            receipt.get("deliveryActivityId") or ""
        ),
        "driveItem": receipt.get("driveItem"),
        "contentType": receipt.get("contentType"),
        "graphError": receipt.get("graphError"),
    }


def write_terminal_receipt(
    manifest: dict[str, Any],
    target: Path,
    *,
    status: str,
    drive_item: dict[str, Any] | None = None,
    graph_error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = time.time()
    receipt = {
        "version": "1.0",
        "operationId": manifest["operationId"],
        "status": status,
        "completedAt": datetime.fromtimestamp(now, UTC).isoformat(),
        "expiresAtUnix": now + RECEIPT_RETENTION_SECONDS,
        "deliveryReference": (
            manifest.get("background") or {}
        ).get("deliveryReference"),
        "recipientIdentifier": (
            manifest.get("background") or {}
        ).get("recipientIdentifier"),
        "driveItem": (
            public_drive_item(drive_item)
            if isinstance(drive_item, dict)
            else None
        ),
        "contentType": str(
            manifest.get("contentType")
            or "application/octet-stream"
        ),
        "graphError": graph_error,
        "deliveryActivityId": "",
        "deliveryState": "pending",
        "deliveryAttemptId": "",
        "deliveryStartedAtUnix": 0,
    }
    atomic_write_json(
        receipt_path(str(manifest["operationId"])),
        receipt,
    )
    collaboration.remove_private_workdir(target.parent)
    return {
        "status": status,
        "operationId": manifest["operationId"],
        "deliveryReference": receipt["deliveryReference"],
        "driveItem": receipt["driveItem"],
        "contentType": receipt["contentType"],
        "graphError": graph_error,
        "delivered": False,
    }


async def pending_choices(operation_scope: str) -> dict[str, Any]:
    cleanup_receipts()
    result = await collaboration.find_pending_office_publishes(
        operation_scope
    )
    operations = [
        operation
        for operation in result["operations"]
        if not read_receipt(str(operation.get("operationId") or ""))
    ]
    return {"operations": operations}


def configure_background_retry(
    operation_id: str,
    operation_scope: str,
    recipient_identifier: str,
    delivery_reference: dict[str, Any],
) -> dict[str, Any]:
    cleanup_receipts()
    receipt = read_receipt(operation_id)
    if receipt:
        return terminal_result(receipt)
    manifest, target = collaboration.load_pending_publish(
        operation_id,
        operation_scope,
    )
    if not target.is_file():
        raise ValueError("The retained document edit is missing.")
    existing = manifest.get("background")
    if isinstance(existing, dict):
        return {
            "status": "scheduled",
            "operationId": operation_id,
            "nextAttemptAt": existing["nextAttemptAt"],
            "nextAttemptUnix": existing["nextAttemptUnix"],
            "deadlineAt": existing["deadlineAt"],
            "attempt": int(existing.get("attempt") or 0),
        }
    now = time.time()
    next_attempt = now + BACKGROUND_RETRY_DELAYS[0]
    deadline = now + BACKGROUND_DEADLINE_SECONDS
    manifest["expiresAtUnix"] = deadline + 3600
    manifest["expiresAt"] = collaboration.utc_timestamp(
        manifest["expiresAtUnix"]
    )
    manifest["background"] = {
        "status": "scheduled",
        "attempt": 0,
        "recipientIdentifier": recipient_identifier,
        "deliveryReference": delivery_reference,
        "createdAt": collaboration.utc_timestamp(now),
        "nextAttemptUnix": next_attempt,
        "nextAttemptAt": collaboration.utc_timestamp(next_attempt),
        "deadlineUnix": deadline,
        "deadlineAt": collaboration.utc_timestamp(deadline),
        "lastGraphError": None,
    }
    collaboration.write_pending_publish_manifest(
        target.parent,
        manifest,
    )
    return {
        "status": "scheduled",
        "operationId": operation_id,
        "nextAttemptAt": collaboration.utc_timestamp(next_attempt),
        "nextAttemptUnix": next_attempt,
        "deadlineAt": collaboration.utc_timestamp(deadline),
        "attempt": 0,
    }


async def complete_copy(
    manifest: dict[str, Any],
    target: Path,
) -> dict[str, Any]:
    background = manifest.get("background") or {}
    recipient = str(background.get("recipientIdentifier") or "")
    if not recipient:
        raise ValueError(
            "The document operation has no copy recipient."
        )
    copied = await collaboration.upload_agent_results_copy(
        target,
        str(
            manifest.get("contentType")
            or "application/octet-stream"
        ),
    )
    try:
        await collaboration.invite_drive_item_user(
            copied,
            recipient,
            "write",
        )
    except (
        httpx.HTTPStatusError,
        RuntimeError,
        ValueError,
    ) as exc:
        await collaboration.delete_drive_item(copied)
        raise RuntimeError(
            "The fallback copy could not be shared and was deleted."
        ) from exc
    return write_terminal_receipt(
        manifest,
        target,
        status="completed_copy",
        drive_item=copied,
        graph_error=background.get("lastGraphError"),
    )


async def copy_now(
    operation_id: str,
    operation_scope: str,
    recipient_identifier: str,
    delivery_reference: dict[str, Any],
) -> dict[str, Any]:
    receipt = read_receipt(operation_id)
    if receipt:
        return terminal_result(receipt)
    manifest, target = collaboration.load_pending_publish(
        operation_id,
        operation_scope,
    )
    manifest["background"] = {
        "status": "copying",
        "attempt": 0,
        "recipientIdentifier": recipient_identifier,
        "deliveryReference": delivery_reference,
        "createdAt": collaboration.utc_timestamp(time.time()),
        "lastGraphError": None,
    }
    collaboration.write_pending_publish_manifest(
        target.parent,
        manifest,
    )
    return await complete_copy(manifest, target)


def next_retry_delay(attempt: int) -> int:
    index = min(attempt, len(BACKGROUND_RETRY_DELAYS) - 1)
    return BACKGROUND_RETRY_DELAYS[index]


async def process_background_retry(
    operation_id: str,
) -> dict[str, Any]:
    cleanup_receipts()
    receipt = read_receipt(operation_id)
    if receipt:
        return terminal_result(receipt)
    manifest, target = (
        collaboration.load_pending_publish_internal(operation_id)
    )
    background = manifest.get("background")
    if not isinstance(background, dict):
        raise ValueError(
            "The document operation is not configured for background retry."
        )
    now = time.time()
    if now >= float(background.get("deadlineUnix") or 0):
        return await complete_copy(manifest, target)
    next_attempt = float(
        background.get("nextAttemptUnix") or 0
    )
    if next_attempt > now + 1:
        return {
            "status": "locked",
            "operationId": operation_id,
            "attempt": int(background.get("attempt") or 0),
            "nextAttemptAt": background.get("nextAttemptAt"),
            "nextAttemptUnix": next_attempt,
            "deadlineAt": background.get("deadlineAt"),
            "graphError": background.get("lastGraphError"),
        }

    item = await collaboration.drive_item(
        str(manifest["sharingUrl"])
    )
    current_etag = str(item.get("eTag") or "")
    expected_etag = str(manifest.get("expectedETag") or "")
    if current_etag != expected_etag:
        if manifest.get("publishKind") != "word-text-node-patch":
            background["lastGraphError"] = {
                "statusCode": 412,
                "code": "sourceChanged",
                "message": (
                    "The original changed after the retained edit."
                ),
                "retryAfter": "",
                "requestId": "",
            }
            return await complete_copy(manifest, target)
        try:
            await collaboration.rebase_pending_word_patch(
                manifest,
                target,
                item,
            )
        except (RuntimeError, ValueError):
            background["lastGraphError"] = {
                "statusCode": 412,
                "code": "rebaseFailed",
                "message": (
                    "The guarded edit could not be reapplied to the "
                    "latest original."
                ),
                "retryAfter": "",
                "requestId": "",
            }
            return await complete_copy(manifest, target)
        expected_etag = str(manifest["expectedETag"])

    try:
        uploaded = await collaboration.upload_drive_item(
            item,
            target.read_bytes(),
            expected_etag,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 423:
            raise
        background["attempt"] = int(
            background.get("attempt") or 0
        ) + 1
        background["lastGraphError"] = (
            collaboration.graph_error_summary(exc.response)
        )
        if time.time() >= float(
            background.get("deadlineUnix") or 0
        ):
            return await complete_copy(manifest, target)
        delay = next_retry_delay(background["attempt"])
        next_attempt = time.time() + delay
        background["status"] = "scheduled"
        background["nextAttemptUnix"] = next_attempt
        background["nextAttemptAt"] = (
            collaboration.utc_timestamp(next_attempt)
        )
        collaboration.write_pending_publish_manifest(
            target.parent,
            manifest,
        )
        return {
            "status": "locked",
            "operationId": operation_id,
            "attempt": background["attempt"],
            "nextAttemptAt": background["nextAttemptAt"],
            "nextAttemptUnix": next_attempt,
            "deadlineAt": background["deadlineAt"],
            "graphError": background["lastGraphError"],
        }

    return write_terminal_receipt(
        manifest,
        target,
        status="completed",
        drive_item=uploaded,
    )


def acknowledge_delivery(
    operation_id: str,
    delivery_activity_id: str,
    delivery_attempt_id: str = "",
) -> dict[str, Any]:
    receipt = read_receipt(operation_id)
    if not receipt:
        raise ValueError("Document operation receipt was not found.")
    if receipt.get("deliveryActivityId"):
        return {
            "status": "duplicate",
            "operationId": operation_id,
            "deliveryActivityId": receipt["deliveryActivityId"],
        }
    activity_id = delivery_activity_id.strip()
    if not activity_id:
        raise ValueError("A delivery activity ID is required.")
    expected_attempt = str(
        receipt.get("deliveryAttemptId") or ""
    )
    if (
        expected_attempt
        and expected_attempt != delivery_attempt_id
    ):
        raise ValueError(
            "Document delivery attempt does not own the receipt."
        )
    receipt["deliveryActivityId"] = activity_id
    receipt["deliveryState"] = "delivered"
    receipt["deliveredAt"] = collaboration.utc_timestamp(time.time())
    atomic_write_json(receipt_path(operation_id), receipt)
    return {
        "status": "completed",
        "operationId": operation_id,
        "deliveryActivityId": activity_id,
    }


def claim_delivery(operation_id: str) -> dict[str, Any]:
    receipt = read_receipt(operation_id)
    if not receipt:
        raise ValueError("Document operation receipt was not found.")
    if receipt.get("deliveryActivityId"):
        return {
            "status": "delivered",
            "operationId": operation_id,
            "deliveryActivityId": receipt["deliveryActivityId"],
        }
    now = time.time()
    if (
        receipt.get("deliveryState") == "sending"
        and now
        - float(receipt.get("deliveryStartedAtUnix") or 0)
        < 600
    ):
        return {
            "status": "in_progress",
            "operationId": operation_id,
            "deliveryAttemptId": receipt.get(
                "deliveryAttemptId"
            ),
        }
    attempt_id = secrets.token_hex(12)
    receipt["deliveryState"] = "sending"
    receipt["deliveryAttemptId"] = attempt_id
    receipt["deliveryStartedAtUnix"] = now
    atomic_write_json(receipt_path(operation_id), receipt)
    return {
        "status": "ready",
        "operationId": operation_id,
        "deliveryAttemptId": attempt_id,
    }


def fail_delivery(
    operation_id: str,
    delivery_attempt_id: str,
    error: str,
) -> dict[str, Any]:
    receipt = read_receipt(operation_id)
    if not receipt:
        raise ValueError("Document operation receipt was not found.")
    if receipt.get("deliveryActivityId"):
        return {
            "status": "delivered",
            "operationId": operation_id,
            "deliveryActivityId": receipt["deliveryActivityId"],
        }
    if receipt.get("deliveryAttemptId") != delivery_attempt_id:
        return {
            "status": "stale",
            "operationId": operation_id,
        }
    receipt["deliveryState"] = "pending"
    receipt["deliveryAttemptId"] = ""
    receipt["deliveryStartedAtUnix"] = 0
    receipt["lastDeliveryError"] = error[:1000]
    atomic_write_json(receipt_path(operation_id), receipt)
    return {"status": "pending", "operationId": operation_id}
