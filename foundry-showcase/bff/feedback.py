"""In-app thumbs up/down, emitted as the same event Foundry annotations use.

An annotation is an OpenTelemetry custom event named ``gen_ai.evaluation.result`` correlated
to the trace it judges. Writing one from the chat gateway therefore needs no Foundry API and
no extra credential: the event is emitted onto the already-configured Azure Monitor exporter
with the original span context re-attached, so it lands on the run the user was looking at.

The ex-post reviewer app in ``annotation-review`` writes the identical event with
``source=end_user`` replaced by ``source=builder``, so live signal and considered review end
up in one place.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

EVENT_NAME = "gen_ai.evaluation.result"
DEFAULT_METRIC = "task_completion"

logger = logging.getLogger("foundry-showcase.feedback")
# The root logger defaults to WARNING, which would discard the annotation record before it
# reaches the Azure Monitor handler.
logger.setLevel(logging.INFO)

BINARY_SCALE = {
    "gen_ai.evaluation.type": "boolean",
    "gen_ai.evaluation.min_value": "0.0",
    "gen_ai.evaluation.max_value": "1.0",
    "gen_ai.evaluation.threshold": "1.0",
    "gen_ai.evaluation.desirable_direction": "increase",
}


def parse_hex_id(name: str, value: Any, length: int) -> int:
    if not isinstance(value, str) or len(value) != length:
        raise ValueError(f"{name} must be a {length}-character hexadecimal string.")
    try:
        parsed = int(value, 16)
    except ValueError as exc:
        raise ValueError(
            f"{name} must be a {length}-character hexadecimal string."
        ) from exc
    if parsed == 0:
        raise ValueError(f"{name} must not be all zeroes.")
    return parsed


def feedback_attributes(
    passed: bool,
    comment: str | None = None,
    reviewer: str | None = None,
    metric_name: str = DEFAULT_METRIC,
) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        "microsoft.custom_event.name": EVENT_NAME,
        "gen_ai.evaluation.name": metric_name,
        "gen_ai.evaluation.score.value": 1.0 if passed else 0.0,
        "gen_ai.evaluation.score.label": "pass" if passed else "fail",
        "microsoft.gen_ai.human_evaluation.source": "end_user",
        "microsoft.gen_ai.evaluation.actor.type": "human",
        "microsoft.gen_ai.human_evaluation.id": str(uuid.uuid4()),
        "internal_properties": json.dumps(BINARY_SCALE),
    }
    if comment:
        attributes["gen_ai.evaluation.explanation"] = comment
    if reviewer:
        attributes["microsoft.gen_ai.evaluation.tags.reviewer"] = reviewer
    return attributes


def record_feedback(
    trace_id: str,
    span_id: str,
    passed: bool,
    comment: str | None = None,
    reviewer: str | None = None,
) -> dict[str, Any]:
    """Attach a thumbs up/down to an existing agent run."""
    span_context = SpanContext(
        trace_id=parse_hex_id("traceId", trace_id, 32),
        span_id=parse_hex_id("spanId", span_id, 16),
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    attributes = feedback_attributes(passed, comment, reviewer)
    token = otel_context.attach(
        trace.set_span_in_context(NonRecordingSpan(span_context))
    )
    try:
        logger.info(EVENT_NAME, extra=attributes)
    finally:
        otel_context.detach(token)
    return {
        "traceId": trace_id,
        "spanId": span_id,
        "label": attributes["gen_ai.evaluation.score.label"],
        "annotationId": attributes["microsoft.gen_ai.human_evaluation.id"],
    }
