"""ASGI tracing shared by bridge ingress and the authenticated Hermes runtime."""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

from opentelemetry.trace import SpanKind, Status, StatusCode

from bridge.telemetry import operation_span, runtime_correlation_context


class TelemetryMiddleware:
    def __init__(self, app: Any, *, runtime: bool = False) -> None:
        self.app = app
        self.runtime = runtime

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("path") in {"/health", "/healthz"}:
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin1").lower(): value.decode("latin1")
            for key, value in scope.get("headers", [])
        }
        method = scope.get("method", "")
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
            method = "_OTHER"
        correlation = runtime_correlation_context(headers) if self.runtime else nullcontext()
        with correlation, operation_span(
            f"HTTP {method}",
            kind=SpanKind.SERVER,
            carrier=headers,
            attributes={"http.request.method": method},
        ) as span:
            async def traced_send(message: dict[str, Any]) -> None:
                if message["type"] == "http.response.start":
                    status = message["status"]
                    span.set_attribute("http.response.status_code", status)
                    if status >= 500:
                        span.set_status(Status(StatusCode.ERROR))
                await send(message)

            try:
                await self.app(scope, receive, traced_send)
            finally:
                route = getattr(scope.get("route"), "path", "")
                if route:
                    span.set_attribute("http.route", route)
                    span.update_name(f"{method} {route}")
