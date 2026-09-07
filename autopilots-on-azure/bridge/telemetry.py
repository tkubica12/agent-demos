"""Shared, metadata-only tracing for the bridge and the Sandbox runtime."""

from __future__ import annotations

import hashlib
import os
import re
import threading
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


_PROPAGATOR = TraceContextTextMapPropagator()
_CORRELATIONS: ContextVar[dict[str, str]] = ContextVar("telemetry_correlations", default={})
_LOCK = threading.Lock()
_provider: TracerProvider | None = None
_settings: TelemetrySettings | None = None
_SESSION_HEADER = "x-autopilot-otel-session"
_OPERATION_HEADER = "x-autopilot-otel-operation"
_SAFE_RESOURCE_ATTRIBUTES = frozenset({
    "service.name", "service.version", "service.instance.id", "container.image.name",
})
_SAFE_SPAN_NAMES = frozenset({
    "invoke_agent", "chat", "execute_tool",
    "servicebus.schedule", "servicebus.send", "servicebus.process",
})
_SAFE_ATTRIBUTES = frozenset(
    {
        "gen_ai.agent.id", "gen_ai.agent.name", "gen_ai.agent.version",
        "gen_ai.operation.name", "gen_ai.conversation.id", "gen_ai.provider.name",
        "gen_ai.request.model", "gen_ai.response.model", "gen_ai.tool.name",
        "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens",
        "http.request.method", "http.response.status_code", "http.route",
        "messaging.system", "messaging.operation.type",
        "autopilots.worker.id", "autopilots.operation.id", "autopilots.role.blueprint",
        "autopilots.role.release", "autopilots.role.commit", "autopilots.component",
        "autopilots.outcome", "autopilots.invocation.source", "error.type",
        "autopilots.trace.correlation",
    }
)


@dataclass(frozen=True)
class TelemetrySettings:
    agent_name: str = "autopilots-hermes"
    agent_id: str = "autopilots-hermes"
    component: str = "bridge"
    worker_id: str = "local"
    service_name: str = "autopilots-bridge"
    service_version: str = "development"
    instance_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    role_blueprint: str = ""
    role_release: str = ""
    role_commit: str = ""
    container_image: str = ""
    sampling_ratio: float = 1.0
    connection_string: str = field(default="", repr=False)

    @classmethod
    def from_environment(cls, component: str = "bridge") -> TelemetrySettings:
        agent_name = os.getenv("FOUNDRY_AGENT_NAME", "autopilots-hermes")
        ratio = float(os.getenv("OTEL_TRACES_SAMPLER_ARG", "1.0"))
        if not 0 <= ratio <= 1:
            raise ValueError("OTEL_TRACES_SAMPLER_ARG must be between 0 and 1.")
        return cls(
            agent_name=agent_name,
            agent_id=os.getenv("OTEL_AGENT_ID", agent_name),
            component=component,
            worker_id=os.getenv("WORKER_ID", os.getenv("AUTOPILOT_NAME", "local")),
            service_name=os.getenv("OTEL_SERVICE_NAME", f"autopilots-{component}"),
            service_version=os.getenv("OTEL_SERVICE_VERSION", "development"),
            role_blueprint=os.getenv("HERMES_ROLE_BLUEPRINT", ""),
            role_release=os.getenv("HERMES_ROLE_RELEASE", ""),
            role_commit=os.getenv("HERMES_ROLE_RELEASE_COMMIT", ""),
            container_image=os.getenv("OTEL_CONTAINER_IMAGE", ""),
            sampling_ratio=ratio,
            connection_string=os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", ""),
        )

    def opaque_id(self, namespace: str, value: str) -> str:
        material = "\0".join((self.agent_id, self.worker_id, namespace, value))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]

    def span_attributes(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "gen_ai.agent.id": self.agent_id,
                "gen_ai.agent.name": self.agent_name,
                "gen_ai.agent.version": self.role_release or self.service_version,
                "autopilots.worker.id": self.opaque_id("worker", self.worker_id),
                "autopilots.component": self.component,
                "autopilots.role.blueprint": self.role_blueprint,
                "autopilots.role.release": self.role_release,
                "autopilots.role.commit": self.role_commit,
            }.items()
            if value
        }

    def resource(self) -> Resource:
        attributes = {
            "service.name": self.service_name,
            "service.version": self.service_version,
            "service.instance.id": self.instance_id,
        }
        if self.container_image:
            attributes["container.image.name"] = self.container_image
        # Explicit resources avoid exporting arbitrary OTEL_RESOURCE_ATTRIBUTES.
        return Resource(attributes)


def settings() -> TelemetrySettings:
    return _settings or TelemetrySettings.from_environment()


class AgentIdentityProcessor(SpanProcessor):
    def __init__(self, configuration: TelemetrySettings) -> None:
        self.attributes = configuration.span_attributes()

    def on_start(self, span: Any, parent_context: Context | None = None) -> None:
        span.set_attributes(self.attributes)
        span.set_attributes(_CORRELATIONS.get())


class MetadataOnlyExporter(SpanExporter):
    """Drop content, exception text, events, and link attributes at the export boundary."""

    def __init__(self, exporter: SpanExporter) -> None:
        self.exporter = exporter

    def export(self, spans: Sequence[ReadableSpan]):
        sanitized = [
            ReadableSpan(
                name=self._name(span),
                context=span.context,
                parent=span.parent,
                resource=Resource({
                    key: value for key, value in span.resource.attributes.items()
                    if key in _SAFE_RESOURCE_ATTRIBUTES
                }),
                attributes={
                    key: value
                    for key, value in (span.attributes or {}).items()
                    if key in _SAFE_ATTRIBUTES
                },
                events=(),
                links=[trace.Link(link.context) for link in span.links],
                kind=span.kind,
                instrumentation_scope=InstrumentationScope("autopilots"),
                status=Status(span.status.status_code),
                start_time=span.start_time,
                end_time=span.end_time,
            )
            for span in spans
        ]
        return self.exporter.export(sanitized)

    @staticmethod
    def _name(span: ReadableSpan) -> str:
        attributes = span.attributes or {}
        operation = attributes.get("gen_ai.operation.name")
        if operation in {"invoke_agent", "chat", "execute_tool"}:
            return str(operation)
        method = attributes.get("http.request.method")
        if method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "_OTHER"}:
            route = attributes.get("http.route")
            return f"{method} {route}" if route else f"HTTP {method}"
        return span.name if span.name in _SAFE_SPAN_NAMES else "internal"

    def shutdown(self) -> None:
        self.exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.exporter.force_flush(timeout_millis)


def configure_telemetry(component: str = "bridge", *, reuse_existing: bool = False) -> TracerProvider:
    """Initialize exactly one provider/exporter per process; local runs do not export."""
    global _provider, _settings
    with _LOCK:
        if _provider is not None:
            if not reuse_existing and _settings is not None and _settings.component != component:
                raise RuntimeError("Each process must configure one telemetry component.")
            return _provider
        if not isinstance(trace.get_tracer_provider(), trace.ProxyTracerProvider):
            raise RuntimeError("A tracer provider is already installed; use one shared initializer.")
        configuration = TelemetrySettings.from_environment(component)
        provider = TracerProvider(
            resource=configuration.resource(),
            sampler=ParentBased(TraceIdRatioBased(configuration.sampling_ratio)),
        )
        provider.add_span_processor(AgentIdentityProcessor(configuration))
        if configuration.connection_string:
            from azure.identity import AzureCliCredential, ManagedIdentityCredential
            from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter

            credential = (
                ManagedIdentityCredential(client_id=os.getenv("AZURE_CLIENT_ID"))
                if os.getenv("IDENTITY_ENDPOINT") or os.getenv("MSI_ENDPOINT")
                else AzureCliCredential(process_timeout=120)
            )
            exporter = AzureMonitorTraceExporter(
                connection_string=configuration.connection_string,
                credential=credential,
                disable_offline_storage=True,
            )
            provider.add_span_processor(BatchSpanProcessor(MetadataOnlyExporter(exporter)))
        trace.set_tracer_provider(provider)
        _settings = configuration
        _provider = provider
        return provider


def flush_telemetry() -> None:
    if _provider is not None:
        _provider.force_flush(timeout_millis=5000)


def trace_headers() -> dict[str, str]:
    """Return W3C trace context only. Baggage and application credentials never cross."""
    carrier: dict[str, str] = {}
    _PROPAGATOR.inject(carrier)
    return carrier


def runtime_trace_headers() -> dict[str, str]:
    headers = trace_headers()
    correlations = _CORRELATIONS.get()
    for attribute, header in (
        ("gen_ai.conversation.id", _SESSION_HEADER),
        ("autopilots.operation.id", _OPERATION_HEADER),
    ):
        if value := correlations.get(attribute):
            headers[header] = value
    return headers


@contextmanager
def runtime_correlation_context(headers: Mapping[str, str]) -> Iterator[None]:
    """Accept only already-opaque IDs at the authenticated runtime HTTP boundary."""
    correlations = {}
    for header, attribute in (
        (_SESSION_HEADER, "gen_ai.conversation.id"),
        (_OPERATION_HEADER, "autopilots.operation.id"),
    ):
        value = headers.get(header, "")
        if re.fullmatch(r"[0-9a-f]{32}", value):
            correlations[attribute] = value
    token = _CORRELATIONS.set(correlations)
    try:
        yield
    finally:
        _CORRELATIONS.reset(token)


@contextmanager
def operation_span(
    name: str,
    *,
    kind: SpanKind = SpanKind.INTERNAL,
    session_id: str = "",
    operation_id: str = "",
    carrier: Mapping[str, str] | None = None,
    attributes: Mapping[str, Any] | None = None,
) -> Iterator[trace.Span]:
    configuration = settings()
    correlations = dict(_CORRELATIONS.get())
    if session_id:
        correlations["gen_ai.conversation.id"] = configuration.opaque_id("session", session_id)
    if operation_id:
        correlations["autopilots.operation.id"] = configuration.opaque_id("operation", operation_id)
    elif name == "invoke_agent" and "autopilots.operation.id" not in correlations:
        correlations["autopilots.operation.id"] = uuid.uuid4().hex
    token = _CORRELATIONS.set(correlations)
    context = (
        _PROPAGATOR.extract(dict(carrier), context=Context())
        if carrier is not None
        else None
    )
    try:
        with trace.get_tracer("autopilots").start_as_current_span(
            name,
            context=context,
            kind=kind,
            attributes={**configuration.span_attributes(), **correlations, **(attributes or {})},
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                yield span
            except BaseException as exc:
                span.set_attribute("error.type", type(exc).__name__)
                span.set_status(Status(StatusCode.ERROR))
                raise
    finally:
        _CORRELATIONS.reset(token)


def model_span(model: str, provider: str = "azure.ai.openai"):
    """Wrap a real model call, never a runtime HTTP request masquerading as a model."""
    return operation_span(
        "chat",
        kind=SpanKind.CLIENT,
        attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": model,
            "gen_ai.provider.name": provider,
        },
    )


def tool_span(tool_name: str):
    """Wrap actual tool execution without serializing arguments or results."""
    return operation_span(
        "execute_tool",
        attributes={"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": tool_name},
    )
