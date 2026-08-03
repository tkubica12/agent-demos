"""Read agent traces and write human annotations, without using the Foundry portal.

Foundry trace annotations are not a proprietary resource. They are OpenTelemetry
``gen_ai.evaluation.result`` custom events stored in the Application Insights resource
connected to the Foundry project. The portal's Annotate button writes the same shape, so
this module can both read existing annotations and append new ones.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

QUERY_HOST = "https://api.applicationinsights.io"
EVALUATION_EVENT = "gen_ai.evaluation.result"
DEFAULT_METRIC = "task_completion"


class TelemetryError(RuntimeError):
    """Raised when Application Insights rejects a read or write."""


@dataclass(frozen=True)
class Annotation:
    """A human quality signal attached to one span of one trace."""

    trace_id: str
    span_id: str
    passed: bool
    explanation: str | None = None
    reviewer: str | None = None
    source: str = "builder"
    metric_name: str = DEFAULT_METRIC
    response_id: str | None = None
    conversation_id: str | None = None
    agent_name: str | None = None
    agent_version: str | None = None
    annotation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def __post_init__(self) -> None:
        validate_hex_id("trace_id", self.trace_id, 32)
        validate_hex_id("span_id", self.span_id, 16)
        if self.source not in ("builder", "end_user"):
            raise ValueError("source must be 'builder' or 'end_user'.")


def validate_hex_id(name: str, value: str, length: int) -> None:
    if not isinstance(value, str) or len(value) != length:
        raise ValueError(f"{name} must be a {length}-character hexadecimal string.")
    try:
        parsed = int(value, 16)
    except ValueError as exc:
        raise ValueError(
            f"{name} must be a {length}-character hexadecimal string."
        ) from exc
    if parsed == 0:
        raise ValueError(f"{name} cannot be all zeros.")


def annotation_properties(annotation: Annotation) -> dict[str, str]:
    """Build the documented ``gen_ai.evaluation.result`` attribute set."""
    internal: dict[str, str] = {
        "gen_ai.evaluation.type": "boolean",
        "gen_ai.evaluation.min_value": "0.0",
        "gen_ai.evaluation.max_value": "1.0",
        "gen_ai.evaluation.threshold": "1.0",
        "gen_ai.evaluation.desirable_direction": "increase",
    }
    if annotation.agent_version:
        internal["gen_ai.agent.version"] = annotation.agent_version

    properties: dict[str, str] = {
        "gen_ai.evaluation.name": annotation.metric_name,
        "gen_ai.evaluation.score.value": "1.0" if annotation.passed else "0.0",
        "gen_ai.evaluation.score.label": "pass" if annotation.passed else "fail",
        "microsoft.gen_ai.human_evaluation.source": annotation.source,
        "microsoft.gen_ai.evaluation.actor.type": "human",
        "microsoft.gen_ai.human_evaluation.id": annotation.annotation_id,
        "internal_properties": json.dumps(internal),
    }
    if annotation.explanation:
        properties["gen_ai.evaluation.explanation"] = annotation.explanation
    if annotation.reviewer:
        properties["microsoft.gen_ai.evaluation.tags.reviewer"] = annotation.reviewer
    if annotation.response_id:
        properties["gen_ai.response.id"] = annotation.response_id
    if annotation.conversation_id:
        properties["gen_ai.conversation.id"] = annotation.conversation_id
    if annotation.agent_name:
        properties["gen_ai.agent.name"] = annotation.agent_name
    if annotation.agent_version:
        properties["gen_ai.agent.version"] = annotation.agent_version
    return properties


def annotation_envelope(
    annotation: Annotation,
    instrumentation_key: str,
    role_name: str = "annotation-review",
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Wrap an annotation in the Application Insights ingestion envelope.

    ``ai.operation.id`` and ``ai.operation.parentId`` become ``operation_Id`` and
    ``operation_ParentId``, which is how the annotation binds to the original trace.
    """
    moment = timestamp or datetime.now(UTC)
    return {
        "name": "Microsoft.ApplicationInsights.Event",
        "time": moment.strftime("%Y-%m-%dT%H:%M:%S.%f0Z"),
        "iKey": instrumentation_key,
        "tags": {
            "ai.operation.id": annotation.trace_id,
            "ai.operation.parentId": annotation.span_id,
            "ai.cloud.role": role_name,
            "ai.user.authUserId": annotation.reviewer or "",
        },
        "data": {
            "baseType": "EventData",
            "baseData": {
                "ver": 2,
                "name": EVALUATION_EVENT,
                "properties": annotation_properties(annotation),
            },
        },
    }


def parse_connection_string(connection_string: str) -> tuple[str, str]:
    """Return ``(instrumentation_key, ingestion_endpoint)``."""
    parts = dict(
        piece.split("=", 1)
        for piece in connection_string.split(";")
        if "=" in piece
    )
    key = parts.get("InstrumentationKey")
    endpoint = parts.get("IngestionEndpoint")
    if not key or not endpoint:
        raise ValueError(
            "Connection string must contain InstrumentationKey and IngestionEndpoint."
        )
    return key, endpoint.rstrip("/")


def rows_to_dicts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    tables = payload.get("tables") or []
    if not tables:
        return []
    table = tables[0]
    columns = [column["name"] for column in table["columns"]]
    return [dict(zip(columns, row)) for row in table["rows"]]


def _text_from_parts(message: dict[str, Any]) -> str:
    chunks: list[str] = []
    for part in message.get("parts") or []:
        if part.get("type") == "text" and part.get("content"):
            chunks.append(str(part["content"]))
    return "\n".join(chunks).strip()


def summarize_messages(raw: str | None, role: str) -> str:
    """Pull the last plain-text message for ``role`` out of a gen_ai messages payload."""
    if not raw:
        return ""
    try:
        messages = json.loads(raw)
    except (TypeError, ValueError):
        return ""
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != role:
            continue
        text = _text_from_parts(message)
        if text:
            return text
    return ""


def strip_memory_preamble(text: str) -> str:
    """Drop the injected memory preamble so reviewers see the real question."""
    marker = "Current user message:"
    if marker in text:
        return text.split(marker, 1)[1].strip()
    return text


RUNS_QUERY = """
let window = ago({days}d);
let chats = dependencies
| where timestamp > window
| where isnotempty(tostring(customDimensions["gen_ai.output.messages"]))
| project timestamp, operation_Id, spanId = id,
          inputMessages = tostring(customDimensions["gen_ai.input.messages"]),
          outputMessages = tostring(customDimensions["gen_ai.output.messages"]),
          responseId = tostring(customDimensions["gen_ai.response.id"]),
          conversationId = tostring(customDimensions["gen_ai.conversation.id"]),
          model = tostring(customDimensions["gen_ai.response.model"]),
          durationMs = duration;
let runs = requests
| where timestamp > window
| where name == "invoke_agent"
| extend agentName = tostring(customDimensions["gen_ai.agent.name"]),
         agentVersion = tostring(customDimensions["gen_ai.agent.version"])
| where isnotempty(agentName)
| summarize agentName = take_any(agentName), agentVersion = take_any(agentVersion)
  by operation_Id;
chats
| join kind=leftouter runs on operation_Id
| project timestamp, traceId = operation_Id, spanId, inputMessages, outputMessages,
          responseId, conversationId, model, durationMs, agentName, agentVersion
| order by timestamp desc
| take {limit}
"""

ANNOTATIONS_QUERY = """
customEvents
| where timestamp > ago({days}d)
| where name == "{event}"
| where tostring(customDimensions["microsoft.gen_ai.evaluation.actor.type"]) == "human"
| project timestamp, traceId = operation_Id, spanId = operation_ParentId,
          metric = tostring(customDimensions["gen_ai.evaluation.name"]),
          score = todouble(customDimensions["gen_ai.evaluation.score.value"]),
          label = tostring(customDimensions["gen_ai.evaluation.score.label"]),
          explanation = tostring(customDimensions["gen_ai.evaluation.explanation"]),
          source = tostring(customDimensions["microsoft.gen_ai.human_evaluation.source"]),
          reviewer = tostring(customDimensions["microsoft.gen_ai.evaluation.tags.reviewer"]),
          annotationId = tostring(customDimensions["microsoft.gen_ai.human_evaluation.id"])
| order by timestamp desc
"""

TRACE_DETAIL_QUERY = """
let target = "{trace_id}";
let chats = dependencies
| where operation_Id == target
| where isnotempty(tostring(customDimensions["gen_ai.output.messages"]))
| project timestamp, spanId = id, kind = "chat",
          inputMessages = tostring(customDimensions["gen_ai.input.messages"]),
          outputMessages = tostring(customDimensions["gen_ai.output.messages"]),
          systemInstructions = tostring(customDimensions["gen_ai.system_instructions"]),
          responseId = tostring(customDimensions["gen_ai.response.id"]),
          conversationId = tostring(customDimensions["gen_ai.conversation.id"]),
          model = tostring(customDimensions["gen_ai.response.model"]),
          toolName = "", toolArguments = "", toolResult = "",
          durationMs = duration;
let tools = dependencies
| where operation_Id == target
| where isnotempty(tostring(customDimensions["gen_ai.tool.name"]))
| project timestamp, spanId = id, kind = "tool",
          inputMessages = "", outputMessages = "", systemInstructions = "",
          responseId = "", conversationId = "", model = "",
          toolName = tostring(customDimensions["gen_ai.tool.name"]),
          toolArguments = tostring(customDimensions["gen_ai.tool.call.arguments"]),
          toolResult = tostring(customDimensions["gen_ai.tool.call.result"]),
          durationMs = duration;
union chats, tools
| order by timestamp asc
"""


class TelemetryClient:
    """Reads traces and writes annotations with the signed-in user's own tokens."""

    def __init__(
        self,
        resource_id: str,
        connection_string: str,
        client: httpx.AsyncClient,
    ) -> None:
        self.resource_id = resource_id.strip("/")
        self.instrumentation_key, self.ingestion_endpoint = parse_connection_string(
            connection_string
        )
        self.client = client

    async def query(self, kql: str, read_token: str) -> list[dict[str, Any]]:
        response = await self.client.post(
            f"{QUERY_HOST}/v1/{self.resource_id}/query",
            headers={
                "Authorization": f"Bearer {read_token}",
                "Content-Type": "application/json",
            },
            json={"query": kql},
        )
        if response.status_code >= 400:
            raise TelemetryError(
                f"Log query failed with {response.status_code}: {response.text[:500]}"
            )
        return rows_to_dicts(response.json())

    async def list_runs(
        self, read_token: str, days: int = 30, limit: int = 100
    ) -> list[dict[str, Any]]:
        rows = await self.query(
            RUNS_QUERY.format(days=days, limit=limit), read_token
        )
        runs: list[dict[str, Any]] = []
        for row in rows:
            question = strip_memory_preamble(
                summarize_messages(row.get("inputMessages"), "user")
            )
            answer = summarize_messages(row.get("outputMessages"), "assistant")
            if not question and not answer:
                continue
            runs.append(
                {
                    "timestamp": row.get("timestamp"),
                    "traceId": row.get("traceId"),
                    "spanId": row.get("spanId"),
                    "question": question,
                    "answer": answer,
                    "responseId": row.get("responseId") or None,
                    "conversationId": row.get("conversationId") or None,
                    "model": row.get("model") or None,
                    "durationMs": row.get("durationMs"),
                    "agentName": row.get("agentName") or None,
                    "agentVersion": row.get("agentVersion") or None,
                }
            )
        return runs

    async def list_annotations(
        self, read_token: str, days: int = 30
    ) -> list[dict[str, Any]]:
        return await self.query(
            ANNOTATIONS_QUERY.format(days=days, event=EVALUATION_EVENT), read_token
        )

    async def trace_detail(
        self, trace_id: str, read_token: str
    ) -> list[dict[str, Any]]:
        validate_hex_id("trace_id", trace_id, 32)
        rows = await self.query(TRACE_DETAIL_QUERY.format(trace_id=trace_id), read_token)
        steps: list[dict[str, Any]] = []
        for row in rows:
            if row.get("kind") == "chat":
                steps.append(
                    {
                        "kind": "chat",
                        "timestamp": row.get("timestamp"),
                        "spanId": row.get("spanId"),
                        "question": strip_memory_preamble(
                            summarize_messages(row.get("inputMessages"), "user")
                        ),
                        "answer": summarize_messages(
                            row.get("outputMessages"), "assistant"
                        ),
                        "responseId": row.get("responseId") or None,
                        "conversationId": row.get("conversationId") or None,
                        "model": row.get("model") or None,
                        "durationMs": row.get("durationMs"),
                    }
                )
            else:
                steps.append(
                    {
                        "kind": "tool",
                        "timestamp": row.get("timestamp"),
                        "spanId": row.get("spanId"),
                        "toolName": row.get("toolName"),
                        "toolArguments": row.get("toolArguments"),
                        "toolResult": row.get("toolResult"),
                        "durationMs": row.get("durationMs"),
                    }
                )
        return steps

    async def write_annotation(
        self, annotation: Annotation, ingest_token: str
    ) -> dict[str, Any]:
        envelope = annotation_envelope(annotation, self.instrumentation_key)
        response = await self.client.post(
            f"{self.ingestion_endpoint}/v2.1/track",
            headers={
                "Authorization": f"Bearer {ingest_token}",
                "Content-Type": "application/json",
            },
            json=[envelope],
        )
        if response.status_code >= 400:
            raise TelemetryError(
                f"Annotation ingestion failed with {response.status_code}: "
                f"{response.text[:500]}"
            )
        result = response.json()
        if result.get("itemsAccepted", 0) < 1:
            raise TelemetryError(f"Annotation was not accepted: {result}")
        return {"annotationId": annotation.annotation_id, "ingestion": result}


def build_evaluation_dataset(
    runs: list[dict[str, Any]], annotations: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Turn thumbs-down annotations into Foundry evaluation cases.

    The shape matches the datasets this repository already evaluates with
    ``azd ai agent eval run``: ``id``, ``description``, ``query``, and
    ``candidate_response``. The reviewer's reason becomes the case description, so the
    evaluation report says why a human rejected the answer. Provenance is kept alongside
    so a case can always be traced back to the run it came from.
    """
    by_span = {run["spanId"]: run for run in runs if run.get("spanId")}
    by_trace: dict[str, dict[str, Any]] = {}
    for run in runs:
        by_trace.setdefault(run.get("traceId"), run)

    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for annotation in annotations:
        if annotation.get("label") != "fail":
            continue
        run = by_span.get(annotation.get("spanId")) or by_trace.get(
            annotation.get("traceId")
        )
        if not run or not run.get("question"):
            continue
        key = annotation.get("traceId")
        if key in seen:
            continue
        seen.add(key)
        cases.append(
            {
                "id": len(cases) + 1,
                "description": describe_case(annotation),
                "query": run["question"],
                "candidate_response": run.get("answer", ""),
                "trace_id": annotation.get("traceId"),
                "span_id": annotation.get("spanId"),
                "agent_name": run.get("agentName"),
                "agent_version": run.get("agentVersion"),
                "reviewer": annotation.get("reviewer") or None,
                "reviewer_comment": annotation.get("explanation") or None,
                "feedback_source": annotation.get("source"),
                "annotated_at": annotation.get("timestamp"),
            }
        )
    return cases


def describe_case(annotation: dict[str, Any]) -> str:
    who = (
        "an end user in the chat client"
        if annotation.get("source") == "end_user"
        else "a business reviewer"
    )
    reason = (annotation.get("explanation") or "").strip()
    base = f"Rejected by {who} during human review of a live agent run."
    return f"{base} Reason given: {reason}" if reason else base


def dataset_to_jsonl(cases: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(case, ensure_ascii=False) for case in cases)
