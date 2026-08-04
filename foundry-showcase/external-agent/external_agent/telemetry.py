"""Configure OpenTelemetry with Azure Monitor exporter."""

from __future__ import annotations

import os

from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from .agent import AGENT_ID, AGENT_NAME

_configured = False


def setup_telemetry() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    connection_string = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not connection_string:
        return

    resource = Resource.create(
        {
            "service.name": AGENT_NAME,
            "service.instance.id": AGENT_ID,
        }
    )
    provider = TracerProvider(resource=resource)
    exporter = AzureMonitorTraceExporter(connection_string=connection_string)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
