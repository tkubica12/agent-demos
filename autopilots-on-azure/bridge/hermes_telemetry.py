"""Hermes 0.19 native execution middleware and local subprocess trace handoff.

The native gateway drops ContextVars at run_in_executor and has no W3C ingress
hook. The wrapper lends its context to one awaited, explicit-session request.
Only W3C context and opaque correlation IDs cross the local process boundary.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import math
import os
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from bridge.telemetry import (
    configure_telemetry,
    flush_telemetry,
    operation_span,
    runtime_correlation_context,
    runtime_trace_headers,
)


PLUGIN_NAME = "autopilots-telemetry"
CONTEXT_DIRECTORY_ENV = "AUTOPILOTS_TRACE_CONTEXT_DIR"
_HANDOFF_HEADERS = frozenset({
    "traceparent", "tracestate", "x-autopilot-otel-session", "x-autopilot-otel-operation",
})


class GatewayTraceConflictError(ValueError):
    """An existing lease may still belong to running native gateway work."""


def install_gateway_telemetry(profile_home: Path) -> Path:
    """Install an opt-in native plugin; caller must enable PLUGIN_NAME in config.

    Call once per wrapper startup, before starting the gateway. A new directory
    isolates this gateway generation from cancelled requests of older processes.
    The subprocess must inherit CONTEXT_DIRECTORY_ENV and be able to import bridge.
    """
    directory = profile_home / ".telemetry" / uuid.uuid4().hex
    directory.mkdir(parents=True, mode=0o700)
    os.environ[CONTEXT_DIRECTORY_ENV] = str(directory.resolve())
    plugin = profile_home / "plugins" / PLUGIN_NAME
    plugin.mkdir(parents=True, exist_ok=True)
    (plugin / "plugin.yaml").write_text(
        f"name: {PLUGIN_NAME}\nversion: '1.0.0'\n"
        "description: Metadata-only native model and tool execution tracing\n",
        encoding="utf-8",
    )
    (plugin / "__init__.py").write_text(
        "from bridge.hermes_telemetry import register\n", encoding="utf-8",
    )
    return directory


def _context_path(session_id: str, profile_home: Path | None = None) -> Path | None:
    directory = os.getenv(CONTEXT_DIRECTORY_ENV)
    if not directory or not session_id:
        return None
    root = Path(directory).resolve()
    if profile_home is not None and root.parent != (profile_home / ".telemetry").resolve():
        raise ValueError("Gateway trace context belongs to a different runtime profile.")
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return root / f"{digest}.json"


def _release_context(path: Path, lease: Path, owner: str) -> None:
    try:
        if lease.read_text(encoding="utf-8") != owner:
            return
    except FileNotFoundError:
        return
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        record = None
    if record is not None:
        if not isinstance(record, dict) or record.get("owner") != owner:
            return
        path.unlink()
    lease.unlink()


@contextmanager
def gateway_trace_context(
    session_id: str, *, profile_home: Path | None = None, ttl_seconds: float = 900,
) -> Iterator[None]:
    """Lend the current trace to an awaited gateway call for an explicit session.

    Concurrent requests for the same native session are rejected, not misattributed.
    On cancellation/transport failure the lease remains: the native executor can
    still run. Restart the wrapper/gateway before reusing that session rather than
    associating a still-running old turn with a new request. Expiry stops trace
    attribution but never steals a lease from a potentially running executor.
    """
    if not math.isfinite(ttl_seconds) or not 1 <= ttl_seconds <= 3600:
        raise ValueError("Gateway trace context lifetime must be between 1 and 3600 seconds.")
    path = _context_path(session_id, profile_home)
    if path is None:
        raise ValueError("Gateway tracing requires installation and an explicit native session ID.")
    headers = runtime_trace_headers()
    if "traceparent" not in headers:
        raise ValueError("Gateway tracing requires an active wrapper span.")
    owner = uuid.uuid4().hex
    lease = path.with_suffix(".lease")
    pending = path.with_name(f"{path.stem}.{owner}.pending")
    try:
        descriptor = os.open(lease, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise GatewayTraceConflictError(
            "A gateway trace lease is already active; finish the turn or restart the worker."
        ) from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(owner)
    published = False
    completed = False
    try:
        descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({
                "owner": owner, "expires_at": time.time() + ttl_seconds, "headers": headers,
            }, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(pending, path)
        published = True
        yield
        completed = True
    finally:
        pending.unlink(missing_ok=True)
        if completed or not published:
            _release_context(path, lease, owner)


def _handoff(session_id: str, task_id: str) -> dict[str, str]:
    for identifier in dict.fromkeys((task_id, session_id)):
        path = _context_path(identifier)
        if path is None:
            continue
        try:
            with path.open(encoding="utf-8") as handle:
                data = json.loads(handle.read(4097))
        except FileNotFoundError:
            continue
        if not isinstance(data, dict) or not isinstance(data.get("headers"), dict):
            raise ValueError("Invalid gateway trace handoff.")
        expiry = data.get("expires_at")
        if type(expiry) not in {int, float} or not math.isfinite(expiry):
            raise ValueError("Invalid gateway trace handoff expiry.")
        if expiry <= time.time():
            return {}
        headers = data["headers"]
        if any(
            key not in _HANDOFF_HEADERS or not isinstance(value, str) for key, value in headers.items()
        ):
            raise ValueError("Invalid gateway trace handoff.")
        parent = TraceContextTextMapPropagator().extract(headers, context=Context())
        if not trace.get_current_span(parent).get_span_context().is_valid:
            raise ValueError("Gateway trace handoff has no valid W3C parent.")
        return headers
    return {}


@contextmanager
def _execution_span(
    operation: str, *, session_id: str, task_id: str, attributes: Mapping[str, Any]
) -> Iterator[trace.Span]:
    if trace.get_current_span().get_span_context().is_valid:
        with operation_span(
            operation,
            kind=SpanKind.CLIENT if operation == "chat" else SpanKind.INTERNAL,
            attributes={**attributes, "autopilots.trace.correlation": "in-process"},
        ) as span:
            yield span
        return
    headers = _handoff(session_id, task_id)
    with runtime_correlation_context(headers), operation_span(
        operation,
        kind=SpanKind.CLIENT if operation == "chat" else SpanKind.INTERNAL,
        carrier=headers,
        session_id="" if headers else session_id,
        attributes={
            **attributes,
            "autopilots.trace.correlation": "propagated" if headers else "missing",
        },
    ) as span:
        yield span


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def llm_execution(
    *, request: dict[str, Any], next_call: Callable[..., Any], session_id: str = "",
    task_id: str = "", model: str = "", provider: str = "", **_: Any,
) -> Any:
    with _execution_span(
        "chat", session_id=session_id, task_id=task_id,
        attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": model,
            "gen_ai.provider.name": "azure.ai.openai" if provider == "azure-foundry" else provider,
        },
    ) as span:
        result = next_call(request)
        response_model = _field(result, "model")
        if isinstance(response_model, str):
            span.set_attribute("gen_ai.response.model", response_model)
        usage = _field(result, "usage")
        for attribute, names in (
            ("gen_ai.usage.input_tokens", ("prompt_tokens", "input_tokens")),
            ("gen_ai.usage.output_tokens", ("completion_tokens", "output_tokens")),
        ):
            for name in names:
                count = _field(usage, name)
                if type(count) is int and count >= 0:
                    span.set_attribute(attribute, count)
                    break
        return result


def tool_execution(
    *, tool_name: str, args: dict[str, Any], next_call: Callable[..., Any],
    session_id: str = "", task_id: str = "", **_: Any,
) -> Any:
    with _execution_span(
        "execute_tool", session_id=session_id, task_id=task_id,
        attributes={"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": tool_name},
    ) as span:
        result = next_call(args)
        # Structured MCP errors are metadata; never inspect or parse textual tool output.
        if isinstance(result, Mapping) and result.get("isError") is True:
            span.set_status(Status(StatusCode.ERROR))
            span.set_attribute("error.type", "ToolError")
        return result


def register(context: Any) -> None:
    """Hermes' supported plugin entry point; no SDK or gateway monkeypatching."""
    configure_telemetry("hermes-gateway", reuse_existing=True)
    context.register_middleware("llm_execution", llm_execution)
    context.register_middleware("tool_execution", tool_execution)
    atexit.register(flush_telemetry)
