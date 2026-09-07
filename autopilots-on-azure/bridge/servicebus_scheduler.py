from __future__ import annotations

import asyncio
import json
import os
import threading
from concurrent.futures import Future
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential
from azure.servicebus import (
    AutoLockRenewer,
    ServiceBusClient,
    ServiceBusMessage,
    ServiceBusReceivedMessage,
)
from azure.servicebus.exceptions import ServiceBusError
from opentelemetry.trace import SpanKind

from bridge.telemetry import operation_span, trace_headers


ScheduleHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def bool_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def message_body(message: ServiceBusReceivedMessage) -> dict[str, Any]:
    raw = b"".join(bytes(part) for part in message.body).decode("utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Scheduled message body must be one JSON object.")
    return payload


def message_trace_headers(message: ServiceBusReceivedMessage) -> dict[str, str]:
    carrier: dict[str, str] = {}
    for key, value in (getattr(message, "application_properties", None) or {}).items():
        if isinstance(key, bytes):
            key = key.decode("ascii", errors="ignore")
        if key not in {"traceparent", "tracestate"}:
            continue
        if isinstance(value, bytes):
            value = value.decode("ascii", errors="ignore")
        if isinstance(value, str):
            carrier[key] = value
    return carrier


class ServiceBusScheduleSender:
    def __init__(
        self,
        *,
        client_factory: Callable[..., ServiceBusClient] = (
            ServiceBusClient
        ),
        credential_factory: Callable[[], Any] = (
            DefaultAzureCredential
        ),
    ) -> None:
        self._client_factory = client_factory
        self._credential_factory = credential_factory

    def schedule_document_retry(
        self,
        *,
        operation_id: str,
        attempt: int,
        due_at_unix: float,
    ) -> dict[str, Any]:
        with operation_span(
            "servicebus.schedule",
            kind=SpanKind.PRODUCER,
            operation_id=operation_id,
            attributes={"messaging.system": "servicebus", "messaging.operation.type": "send"},
        ):
            return self._schedule_document_retry(
                operation_id=operation_id, attempt=attempt, due_at_unix=due_at_unix,
            )

    def _schedule_document_retry(
        self, *, operation_id: str, attempt: int, due_at_unix: float
    ) -> dict[str, Any]:
        worker_id = os.environ["WORKER_ID"]
        body = {
            "version": "1.0",
            "type": "document.publish.retry",
            "workerId": worker_id,
            "operationId": operation_id,
            "attempt": attempt,
            "dueAt": datetime.fromtimestamp(
                due_at_unix,
                UTC,
            ).isoformat(),
        }
        message_id = (
            f"{worker_id}:document:{operation_id}:{attempt}"
        )
        message = ServiceBusMessage(
            json.dumps(
                body,
                sort_keys=True,
                separators=(",", ":"),
            ),
            message_id=message_id,
            content_type="application/json",
            subject="document.publish.retry",
            application_properties=trace_headers(),
        )
        due_at = datetime.fromtimestamp(due_at_unix, UTC)
        with self._client_factory(
            os.environ["SCHEDULER_SERVICEBUS_NAMESPACE"],
            credential=self._credential_factory(),
        ) as client:
            with client.get_queue_sender(
                os.environ["SCHEDULER_SERVICEBUS_QUEUE"]
            ) as sender:
                sequence_numbers = sender.schedule_messages(
                    message,
                    due_at,
                )
        return {
            "messageId": message_id,
            "sequenceNumber": int(sequence_numbers[0]),
            "dueAt": due_at.isoformat(),
        }

    def cancel_scheduled(self, sequence_number: int) -> None:
        with self._client_factory(
            os.environ["SCHEDULER_SERVICEBUS_NAMESPACE"],
            credential=self._credential_factory(),
        ) as client:
            with client.get_queue_sender(
                os.environ["SCHEDULER_SERVICEBUS_QUEUE"]
            ) as sender:
                sender.cancel_scheduled_messages(
                    [sequence_number]
                )


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
            "enabled": (
                bool_env("USER_SCHEDULING_ENABLED")
                or bool_env("DOCUMENT_RETRY_ENABLED")
                or bool_env("SERVICEBUS_DREAM_ENABLED")
            ),
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
                self._invoke_handler(payload, message_trace_headers(message)),
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

    async def _invoke_handler(
        self, payload: dict[str, Any], carrier: dict[str, str]
    ) -> dict[str, Any]:
        operation_id = str(payload.get("operationId") or (
            f"{payload['jobId']}:{payload.get('occurrenceId') or payload['revision']}"
        ))
        with operation_span(
            "servicebus.process",
            kind=SpanKind.CONSUMER,
            operation_id=operation_id,
            carrier=carrier,
            attributes={"messaging.system": "servicebus", "messaging.operation.type": "process"},
        ):
            return await self._handler(payload)

    @staticmethod
    def _validate(payload: dict[str, Any]) -> None:
        if payload.get("version") != "1.0":
            raise ValueError("Unsupported scheduled message version.")
        if payload.get("type") not in {
            "hermes.cron.fire",
            "system.dream",
            "document.publish.retry",
        }:
            raise ValueError("Unsupported scheduled message type.")
        if payload.get("workerId") != os.getenv("WORKER_ID"):
            raise ValueError("Scheduled message targets another Worker.")
        if (
            payload.get("type") == "document.publish.retry"
            and not payload.get("operationId")
        ):
            raise ValueError(
                "Document retry message requires operationId."
            )
