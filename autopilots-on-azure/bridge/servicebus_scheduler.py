from __future__ import annotations

import asyncio
import json
import os
import threading
from concurrent.futures import Future
from typing import Any, Awaitable, Callable

from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential
from azure.servicebus import (
    AutoLockRenewer,
    ServiceBusClient,
    ServiceBusReceivedMessage,
)
from azure.servicebus.exceptions import ServiceBusError


ScheduleHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def bool_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def message_body(message: ServiceBusReceivedMessage) -> dict[str, Any]:
    raw = b"".join(bytes(part) for part in message.body).decode("utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Scheduled message body must be one JSON object.")
    return payload


class ServiceBusScheduleConsumer:
    def __init__(
        self,
        *,
        handler: ScheduleHandler,
        client_factory: Callable[..., ServiceBusClient] = ServiceBusClient,
        credential_factory: Callable[[], Any] = DefaultAzureCredential,
    ) -> None:
        self._handler = handler
        self._client_factory = client_factory
        self._credential_factory = credential_factory
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._status: dict[str, Any] = {
            "enabled": bool_env("USER_SCHEDULING_ENABLED"),
            "running": False,
            "completed": 0,
            "abandoned": 0,
            "deadLettered": 0,
            "duplicates": 0,
            "stale": 0,
            "lastError": None,
            "lastMessage": None,
        }

    def status(self) -> dict[str, Any]:
        return dict(self._status)

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if not self._status["enabled"] or self._thread is not None:
            return
        self._loop = loop
        self._thread = threading.Thread(
            target=self._run,
            name=f"servicebus-scheduler-{os.getenv('WORKER_ID', 'worker')}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=30)
            self._thread = None

    def _run(self) -> None:
        self._status["running"] = True
        try:
            namespace = os.environ["SCHEDULER_SERVICEBUS_NAMESPACE"]
            queue = os.environ["SCHEDULER_SERVICEBUS_QUEUE"]
            credential = self._credential_factory()
            while not self._stop.is_set():
                try:
                    with self._client_factory(
                        namespace,
                        credential=credential,
                    ) as client:
                        with client.get_queue_receiver(
                            queue,
                            max_wait_time=20,
                            prefetch_count=0,
                        ) as receiver:
                            with AutoLockRenewer(max_workers=2) as renewer:
                                self._receive(receiver, renewer)
                except (AzureError, ServiceBusError, OSError) as exc:
                    self._status["lastError"] = {
                        "type": exc.__class__.__name__,
                        "message": str(exc),
                    }
                    self._stop.wait(5)
        finally:
            self._status["running"] = False

    def _receive(self, receiver: Any, renewer: AutoLockRenewer) -> None:
        while not self._stop.is_set():
            messages = receiver.receive_messages(
                max_message_count=1,
                max_wait_time=20,
            )
            for message in messages:
                renewer.register(
                    receiver,
                    message,
                    max_lock_renewal_duration=int(
                        os.getenv(
                            "SCHEDULER_MAX_LOCK_RENEWAL_SECONDS",
                            "1800",
                        )
                    ),
                )
                self._process(receiver, message)

    def _process(self, receiver: Any, message: ServiceBusReceivedMessage) -> None:
        future: Future | None = None
        try:
            payload = message_body(message)
            self._validate(payload)
            loop = self._loop
            if loop is None:
                raise RuntimeError("Service Bus consumer has no event loop.")
            future = asyncio.run_coroutine_threadsafe(
                self._handler(payload),
                loop,
            )
            result = future.result(
                timeout=int(os.getenv("SCHEDULER_MAX_LOCK_RENEWAL_SECONDS", "1800"))
            )
            status = str(result.get("status") or "completed")
            if status == "duplicate":
                self._status["duplicates"] += 1
            elif status == "stale":
                self._status["stale"] += 1
            receiver.complete_message(message)
            self._status["completed"] += 1
            self._status["lastMessage"] = {
                "type": payload["type"],
                "jobId": payload.get("jobId"),
                "status": status,
            }
        except (ValueError, KeyError) as exc:
            receiver.dead_letter_message(
                message,
                reason="invalid_schedule_message",
                error_description=str(exc)[:1024],
            )
            self._status["deadLettered"] += 1
        except Exception as exc:
            if future is not None:
                future.cancel()
            self._status["lastError"] = {
                "type": exc.__class__.__name__,
                "message": str(exc),
            }
            max_delivery = int(os.getenv("SCHEDULER_MAX_DELIVERY_COUNT", "5"))
            if int(getattr(message, "delivery_count", 0) or 0) >= max_delivery:
                receiver.dead_letter_message(
                    message,
                    reason="scheduled_execution_failed",
                    error_description=str(exc)[:1024],
                )
                self._status["deadLettered"] += 1
            else:
                receiver.abandon_message(message)
                self._status["abandoned"] += 1

    @staticmethod
    def _validate(payload: dict[str, Any]) -> None:
        if payload.get("version") != "1.0":
            raise ValueError("Unsupported scheduled message version.")
        if payload.get("type") not in {"hermes.cron.fire", "system.dream"}:
            raise ValueError("Unsupported scheduled message type.")
        if payload.get("workerId") != os.getenv("WORKER_ID"):
            raise ValueError("Scheduled message targets another Worker.")
