from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any, Callable

from azure.identity import DefaultAzureCredential
from azure.servicebus import (
    ServiceBusClient,
    ServiceBusMessage,
)
from opentelemetry.trace import SpanKind

from bridge.telemetry import operation_span, trace_headers


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
        delivery_activity_id: str = "",
        delivery_attempt_id: str = "",
    ) -> dict[str, Any]:
        with operation_span(
            "servicebus.schedule",
            kind=SpanKind.PRODUCER,
            operation_id=operation_id,
            attributes={"messaging.system": "servicebus", "messaging.operation.type": "send"},
        ):
            return self._schedule_document_retry(
                operation_id=operation_id,
                attempt=attempt,
                due_at_unix=due_at_unix,
                delivery_activity_id=delivery_activity_id,
                delivery_attempt_id=delivery_attempt_id,
            )

    def _schedule_document_retry(
        self,
        *,
        operation_id: str,
        attempt: int,
        due_at_unix: float,
        delivery_activity_id: str,
        delivery_attempt_id: str,
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
            "trace": trace_headers(),
        }
        if delivery_activity_id:
            body["deliveryActivityId"] = delivery_activity_id
            body["deliveryAttemptId"] = delivery_attempt_id
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
            application_properties=body["trace"],
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
