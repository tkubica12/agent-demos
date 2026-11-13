from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from bridge.runtime.base import DreamRequest
from bridge.runtime.hermes import DreamExecutionUncertainError


RETRYABLE_ERRORS = (
    httpx.HTTPError,
    OSError,
    RuntimeError,
    TimeoutError,
    asyncio.TimeoutError,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    value = int(raw)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
    return value


@dataclass(frozen=True)
class ScheduledLearningSettings:
    focus: str
    max_records: int
    retry_limit: int
    retry_backoff_seconds: int

    @classmethod
    def from_environment(cls) -> "ScheduledLearningSettings":
        return cls(
            focus=os.getenv(
                "SCHEDULED_LEARNING_FOCUS",
                "Review recent meaningful work for reusable, privacy-safe Role Skill improvements.",
            ).strip(),
            max_records=int_env(
                "SCHEDULED_LEARNING_MAX_RECORDS",
                3,
                minimum=1,
                maximum=10,
            ),
            retry_limit=int_env(
                "SCHEDULED_LEARNING_RETRY_LIMIT",
                3,
                minimum=0,
                maximum=10,
            ),
            retry_backoff_seconds=int_env(
                "SCHEDULED_LEARNING_RETRY_BACKOFF_SECONDS",
                30,
                minimum=1,
                maximum=3_600,
            ),
        )


class ScheduledLearningCoordinator:
    def __init__(
        self,
        *,
        adapter_factory: Callable[[], Any],
        settings: ScheduledLearningSettings,
        worker_id: str,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self._adapter_factory = adapter_factory
        self.settings = settings
        self.worker_id = worker_id
        self._sleep = sleep
        self._run_lock = asyncio.Lock()
        self._status: dict[str, Any] = {
            "execution": "native-or-operator",
            "workerId": worker_id,
            "running": False,
            "runCount": 0,
            "successCount": 0,
            "failureCount": 0,
            "lastStartedAt": None,
            "lastCompletedAt": None,
            "lastSuccessAt": None,
            "lastError": None,
            "lastDream": None,
        }

    def status(self) -> dict[str, Any]:
        return dict(self._status)

    async def run_once(self, *, operation: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._run_lock.locked():
            raise RuntimeError("Scheduled learning is already running for this Worker.")
        async with self._run_lock:
            self._status["running"] = True
            self._status["runCount"] += 1
            self._status["lastStartedAt"] = utc_now()
            self._status["lastError"] = None
            try:
                session_id = str((operation or {}).get("sessionId") or (
                    f"scheduled-dream:{self.worker_id}:{uuid.uuid4().hex}"
                ))
                result = await self._run_with_retry(operation, session_id)
            except RETRYABLE_ERRORS as exc:
                self._status["failureCount"] += 1
                self._status["lastError"] = {
                    "type": exc.__class__.__name__,
                    "message": str(exc),
                }
                raise
            else:
                self._status["successCount"] += 1
                self._status["lastSuccessAt"] = utc_now()
                return result
            finally:
                self._status["running"] = False
                self._status["lastCompletedAt"] = utc_now()

    async def _run_with_retry(
        self, operation: dict[str, Any] | None, session_id: str
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        completed: dict[str, Any] = {}
        for attempt in range(self.settings.retry_limit + 1):
            try:
                return await self._run_operation(operation, session_id, completed)
            except DreamExecutionUncertainError:
                raise
            except RETRYABLE_ERRORS as exc:
                if getattr(exc, "status", None) in {"failed", "uncertain"}:
                    raise
                last_error = exc
                if attempt >= self.settings.retry_limit:
                    raise
                await self._sleep(
                    min(
                        self.settings.retry_backoff_seconds * (2**attempt),
                        3_600,
                    )
                )
        raise RuntimeError("Scheduled learning exhausted retries.") from last_error

    async def _run_operation(
        self, operation: dict[str, Any] | None, session_id: str, completed: dict[str, Any]
    ) -> dict[str, Any]:
        adapter = self._adapter_factory()
        identity = {
            "job_id": operation["jobId"],
            "revision": operation["revision"],
            "occurrence_id": operation["occurrenceId"],
        } if operation else {}
        checkpoint = await adapter.get_system_schedule_checkpoint(**identity) if operation else {}
        if operation and checkpoint.get("ownerToken") != operation["ownerToken"]:
            raise RuntimeError("Scheduled Dreaming lost occurrence ownership.")
        phase = checkpoint.get("phase") or "pending"
        saved = checkpoint.get("checkpoint") or {}
        if phase == "status_completed":
            dream_summary = saved["dream"]
        else:
            request = DreamRequest(
                session_id=session_id,
                focus=self.settings.focus,
                max_records=self.settings.max_records,
            )
            if "dream" not in completed:
                if not operation and completed.get("dream_started"):
                    raise DreamExecutionUncertainError(
                        "Uncheckpointed Dream execution has an unknown outcome; automatic inference retry is blocked."
                    )
                completed["dream_started"] = True
                completed["dream"] = (
                    await adapter.dream(request, operation=operation)
                    if operation else await adapter.dream(request)
                )
            dream = completed["dream"]
            digest = dream.learning_digest
            governed = digest.get("governed") or []
            dream_summary = {
                "sessionId": session_id,
                "skillChangeCount": len(digest.get("changes") or []),
                "pendingApprovalCount": len(digest.get("pending") or []),
                "governedChangeCount": len(governed),
                "sharedChangeCount": sum(
                    1 for item in governed
                    if (item.get("choice") or {}).get("choice") == "share"
                    and (item.get("choice") or {}).get("current")
                ),
                "worker": digest.get("worker"),
            }
            if operation:
                await adapter.checkpoint_system_schedule(
                    **identity,
                    owner_token=operation["ownerToken"],
                    expected_phase="dream_completed",
                    phase="status_completed",
                    payload={"dream": dream_summary},
                )
        self._status["lastDream"] = dream_summary

        return {
            "workerId": self.worker_id,
            "completedAt": utc_now(),
            "dream": dream_summary,
        }
