from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from bridge.runtime.base import DreamRequest


RETRYABLE_ERRORS = (
    httpx.HTTPError,
    OSError,
    RuntimeError,
    TimeoutError,
    asyncio.TimeoutError,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    value = int(raw)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
    return value


@dataclass(frozen=True)
class ScheduledLearningSettings:
    enabled: bool
    initial_delay_seconds: int
    interval_seconds: int
    focus: str
    max_records: int
    retry_limit: int
    retry_backoff_seconds: int
    prepare_packet: bool

    @classmethod
    def from_environment(cls) -> "ScheduledLearningSettings":
        return cls(
            enabled=bool_env("SCHEDULED_LEARNING_ENABLED"),
            initial_delay_seconds=int_env(
                "SCHEDULED_LEARNING_INITIAL_DELAY_SECONDS",
                60,
                minimum=0,
                maximum=86_400,
            ),
            interval_seconds=int_env(
                "SCHEDULED_LEARNING_INTERVAL_SECONDS",
                86_400,
                minimum=300,
                maximum=2_592_000,
            ),
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
            prepare_packet=bool_env("SCHEDULED_LEARNING_PREPARE_PACKET", True),
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
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._status: dict[str, Any] = {
            "enabled": settings.enabled,
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
            "lastPacket": None,
        }

    def status(self) -> dict[str, Any]:
        return dict(self._status)

    async def start(self) -> None:
        if not self.settings.enabled or self._task is not None:
            return
        self._task = asyncio.create_task(
            self._loop(),
            name=f"scheduled-learning-{self.worker_id}",
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def _loop(self) -> None:
        if self.settings.initial_delay_seconds:
            await self._wait_or_stop(self.settings.initial_delay_seconds)
        while not self._stop.is_set():
            try:
                await self.run_once()
            except RETRYABLE_ERRORS:
                pass
            await self._wait_or_stop(self.settings.interval_seconds)

    async def _wait_or_stop(self, seconds: int) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return

    async def run_once(self) -> dict[str, Any]:
        if self._run_lock.locked():
            raise RuntimeError("Scheduled learning is already running for this Worker.")
        async with self._run_lock:
            self._status["running"] = True
            self._status["runCount"] += 1
            self._status["lastStartedAt"] = utc_now()
            self._status["lastError"] = None
            try:
                result = await self._run_with_retry()
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

    async def _run_with_retry(self) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.settings.retry_limit + 1):
            try:
                return await self._run_operation()
            except RETRYABLE_ERRORS as exc:
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

    async def _run_operation(self) -> dict[str, Any]:
        adapter = self._adapter_factory()
        if adapter.runtime_kind != "hermes":
            raise RuntimeError("Scheduled learning is supported only by Hermes.")
        session_id = f"scheduled-dream:{self.worker_id}:{uuid.uuid4().hex}"
        dream = await adapter.dream(
            DreamRequest(
                session_id=session_id,
                focus=self.settings.focus,
                max_records=self.settings.max_records,
            )
        )
        records = dream.learning_status.get("records") or []
        dream_summary = {
            "sessionId": session_id,
            "recordCount": len(records),
            "rejectedRecordCount": len(
                dream.learning_status.get("rejectedRecords") or []
            ),
            "roleRelease": dream.learning_status.get("roleRelease"),
        }
        self._status["lastDream"] = dream_summary

        packet_summary = None
        if self.settings.prepare_packet and records:
            packet = await adapter.prepare_collective_learning()
            packet_summary = {
                "packetDigest": packet.get("packetDigest"),
                "improvementCount": len(packet.get("improvements") or []),
                "roleRelease": packet.get("roleRelease"),
                "approvalRequired": packet.get("approvalRequired"),
            }
            self._status["lastPacket"] = packet_summary

        return {
            "workerId": self.worker_id,
            "completedAt": utc_now(),
            "dream": dream_summary,
            "packet": packet_summary,
        }
