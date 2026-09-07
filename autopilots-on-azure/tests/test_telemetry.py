from __future__ import annotations

import asyncio
import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, Status, StatusCode

from bridge import telemetry
from bridge.telemetry_http import TelemetryMiddleware


class TelemetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.configuration = telemetry.TelemetrySettings(
            agent_name="hermes-demo",
            agent_id="hermes-external",
            worker_id="private-worker",
            role_blueprint="office",
            role_release="v2",
            container_image="example.azurecr.io/hermes@sha256:release",
        )
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider(resource=self.configuration.resource())
        self.provider.add_span_processor(telemetry.AgentIdentityProcessor(self.configuration))
        self.provider.add_span_processor(
            SimpleSpanProcessor(telemetry.MetadataOnlyExporter(self.exporter))
        )
        self.tracer = self.provider.get_tracer("telemetry-tests")
        self.settings_patch = patch.object(telemetry, "_settings", self.configuration)
        self.tracer_patch = patch.object(telemetry.trace, "get_tracer", return_value=self.tracer)
        self.settings_patch.start()
        self.tracer_patch.start()

    def tearDown(self):
        self.tracer_patch.stop()
        self.settings_patch.stop()
        self.provider.shutdown()

    async def test_real_asgi_chain_keeps_trace_and_opaque_session_through_runtime(self):
        runtime = FastAPI()
        runtime.add_middleware(TelemetryMiddleware, runtime=True)

        @runtime.post("/sessions/{session}")
        async def complete(session: str):
            with telemetry.model_span("gpt-5-mini"):
                with telemetry.tool_span("read_file"):
                    await asyncio.to_thread(lambda: None)
            return {"ok": True}

        bridge = FastAPI()
        bridge.add_middleware(TelemetryMiddleware)

        @bridge.post("/invoke")
        async def invoke():
            with telemetry.operation_span(
                "invoke_agent",
                session_id="private-session",
                operation_id="document-operation",
                attributes={"gen_ai.operation.name": "invoke_agent"},
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=runtime), base_url="http://runtime"
                ) as client:
                    await client.post(
                        "/sessions/private-session?token=secret-query",
                        headers={**telemetry.runtime_trace_headers(), "authorization": "Bearer secret"},
                        json={"prompt": "private prompt"},
                    )
            return {"ok": True}

        parent = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=bridge), base_url="http://bridge"
        ) as client:
            response = await client.post(
                "/invoke", headers={"traceparent": parent, "baggage": "prompt=private"}
            )
        self.assertEqual(response.status_code, 200)
        spans = self.exporter.get_finished_spans()
        self.assertEqual(len(spans), 5)
        by_name = {span.name: span for span in spans}
        incoming = by_name["POST /invoke"]
        invoke = by_name["invoke_agent"]
        runtime_span = by_name["POST /sessions/{session}"]
        model = by_name["chat"]
        tool = by_name["execute_tool"]
        self.assertEqual(incoming.parent.span_id, int("0123456789abcdef", 16))
        self.assertEqual(invoke.parent.span_id, incoming.context.span_id)
        self.assertEqual(runtime_span.parent.span_id, invoke.context.span_id)
        self.assertEqual(model.parent.span_id, runtime_span.context.span_id)
        self.assertEqual(tool.parent.span_id, model.context.span_id)
        self.assertEqual({span.context.trace_id for span in spans}, {incoming.context.trace_id})
        session = self.configuration.opaque_id("session", "private-session")
        for span in (invoke, runtime_span, model, tool):
            self.assertEqual(span.attributes["gen_ai.conversation.id"], session)
            self.assertEqual(
                span.attributes["autopilots.operation.id"],
                self.configuration.opaque_id("operation", "document-operation"),
            )
        for span in spans:
            self.assertEqual(span.attributes["gen_ai.agent.id"], "hermes-external")
            self.assertEqual(span.attributes["autopilots.role.release"], "v2")
            self.assertEqual(
                span.resource.attributes["container.image.name"],
                "example.azurecr.io/hermes@sha256:release",
            )
        exported = repr([(span.name, dict(span.attributes), span.events) for span in spans])
        for private in ("private-session", "private-worker", "private prompt", "secret-query", "Bearer"):
            self.assertNotIn(private, exported)
        self.assertNotIn("x-autopilot-otel-session", telemetry.runtime_trace_headers())

    async def test_exception_and_uncontrolled_attributes_are_removed_at_export(self):
        with self.assertRaises(ValueError):
            with telemetry.operation_span("invoke_agent") as span:
                span.set_attribute("gen_ai.input.messages", "private prompt")
                span.set_attribute("gen_ai.tool.call.arguments", "secret")
                span.set_attribute("http.request.header.authorization", "Bearer secret")
                span.set_attribute("gen_ai.usage.input_tokens", 7)
                span.add_event("private tool payload")
                raise ValueError("secret error payload")
        span = self.exporter.get_finished_spans()[0]
        self.assertEqual(span.status.status_code, StatusCode.ERROR)
        self.assertIsNone(span.status.description)
        self.assertEqual(span.events, ())
        self.assertEqual(span.attributes["error.type"], "ValueError")
        self.assertEqual(span.attributes["gen_ai.usage.input_tokens"], 7)
        self.assertNotIn("secret", repr(dict(span.attributes)))
        self.assertNotIn("gen_ai.input.messages", span.attributes)
        with self.tracer.start_as_current_span("sdk-span private SDK input") as sdk_span:
            sdk_span.set_status(Status(StatusCode.ERROR, "private SDK error"))
        self.assertIsNone(self.exporter.get_finished_spans()[1].status.description)
        self.assertEqual(self.exporter.get_finished_spans()[1].name, "internal")

    async def test_thread_and_servicebus_context_keep_operation_correlation(self):
        with telemetry.operation_span("schedule", operation_id="job:occurrence", kind=SpanKind.PRODUCER):
            carrier = await asyncio.to_thread(telemetry.trace_headers)
            producer_context = trace.get_current_span().get_span_context()
        with telemetry.operation_span(
            "process",
            kind=SpanKind.CONSUMER,
            operation_id="job:occurrence",
            carrier={**carrier, "baggage": "private=true"},
            attributes={"messaging.system": "servicebus"},
        ):
            forwarded = telemetry.trace_headers()
        producer, consumer = self.exporter.get_finished_spans()
        self.assertEqual(consumer.parent.span_id, producer_context.span_id)
        self.assertEqual(consumer.context.trace_id, producer.context.trace_id)
        self.assertEqual(
            consumer.attributes["autopilots.operation.id"],
            producer.attributes["autopilots.operation.id"],
        )
        self.assertEqual(set(forwarded), {"traceparent"})

    async def test_health_and_unknown_urls_do_not_export_sensitive_paths(self):
        app = FastAPI()
        app.add_middleware(TelemetryMiddleware)

        @app.get("/health")
        def health():
            return {"status": "ok"}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            self.assertEqual((await client.get("/health")).status_code, 200)
            self.assertEqual((await client.get("/private/customer")).status_code, 404)
        spans = self.exporter.get_finished_spans()
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].name, "HTTP GET")
        self.assertNotIn("http.route", spans[0].attributes)

    async def test_real_bridge_invoke_uses_agent_span(self):
        import bridge.app as bridge_app
        from bridge.runtime.base import AgentResponse

        adapter = AsyncMock()
        adapter.invoke.return_value = AgentResponse(text="done", raw={})
        with patch.object(bridge_app, "runtime_adapter", return_value=adapter):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=bridge_app.app), base_url="http://bridge"
            ) as client:
                response = await client.post(
                    "/invoke", json={"conversationId": "private-session", "message": "private prompt"}
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {span.name for span in self.exporter.get_finished_spans()}, {"invoke_agent", "POST /invoke"}
        )

    async def test_invalid_incoming_correlation_is_not_exported(self):
        with telemetry.runtime_correlation_context({"x-autopilot-otel-session": "private session"}):
            with telemetry.operation_span("runtime"):
                self.assertNotIn("x-autopilot-otel-session", telemetry.runtime_trace_headers())
        self.assertNotIn("gen_ai.conversation.id", self.exporter.get_finished_spans()[0].attributes)

    async def test_invocation_ids_are_unique_but_inherit_scheduled_operation(self):
        with telemetry.operation_span("invoke_agent"):
            first = telemetry.runtime_trace_headers()["x-autopilot-otel-operation"]
        with telemetry.operation_span("invoke_agent"):
            second = telemetry.runtime_trace_headers()["x-autopilot-otel-operation"]
        self.assertNotEqual(first, second)
        with telemetry.operation_span("servicebus.process", operation_id="scheduled-job"):
            scheduled = telemetry.runtime_trace_headers()["x-autopilot-otel-operation"]
            with telemetry.operation_span("invoke_agent"):
                self.assertEqual(telemetry.runtime_trace_headers()["x-autopilot-otel-operation"], scheduled)

    async def test_uncontrolled_resource_and_scope_metadata_are_not_exported(self):
        provider = TracerProvider(resource=Resource({
            "service.name": "hermes", "user.id": "private-user", "host.name": "private-host",
        }))
        provider.add_span_processor(SimpleSpanProcessor(telemetry.MetadataOnlyExporter(self.exporter)))
        with provider.get_tracer("private-scope").start_as_current_span("private-span"):
            pass
        provider.shutdown()
        span, = self.exporter.get_finished_spans()
        self.assertEqual(dict(span.resource.attributes), {"service.name": "hermes"})
        self.assertEqual(span.instrumentation_scope.name, "autopilots")
        self.assertEqual(span.name, "internal")

    async def test_activity_ingress_retains_trace_without_exporting_activity_body(self):
        import bridge.app as bridge_app
        from fastapi.responses import JSONResponse

        handler = AsyncMock(return_value=JSONResponse({"ok": True}))
        with patch.object(bridge_app, "start_agent_process", handler):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=bridge_app.app), base_url="http://bridge"
            ) as client:
                response = await client.post(
                    "/api/messages",
                    headers={
                        "traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
                        "authorization": "Bearer private-credential",
                    },
                    json={
                        "type": "message",
                        "text": "private activity",
                        "conversation": {"id": "private-conversation"},
                    },
                )
        self.assertEqual(response.status_code, 200)
        span, = self.exporter.get_finished_spans()
        self.assertEqual(span.name, "POST /api/messages")
        self.assertEqual(span.context.trace_id, int("0123456789abcdef0123456789abcdef", 16))
        self.assertEqual(span.kind, SpanKind.SERVER)
        self.assertNotIn("private-", repr(dict(span.attributes)))

    async def test_hermes_factory_installs_runtime_boundary_and_thread_correlation(self):
        runtime_dir = Path(__file__).resolve().parents[1] / "runtimes" / "hermes"
        with patch.object(sys, "path", [str(runtime_dir), *sys.path]):
            start_hermes = importlib.import_module("start_hermes")

        def fire_job(*args, **kwargs):
            with telemetry.tool_span("cronjob"):
                return {"status": "completed"}

        runtime = start_hermes.create_health_app(runtime_dir, runtime_dir, None, None)
        self.assertIn(start_hermes.flush_telemetry, runtime.router.on_shutdown)
        with (
            patch.dict(os.environ, {"API_SERVER_KEY": "runtime-test-key"}),
            patch.object(start_hermes, "fire_cron_job", fire_job),
            telemetry.operation_span("invoke_agent", session_id="private-session", operation_id="job:revision"),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=runtime), base_url="http://runtime"
            ) as client:
                response = await client.post(
                    "/internal/cron/fire",
                    headers={
                        **telemetry.runtime_trace_headers(),
                        "X-Autopilot-Key": "runtime-test-key",
                    },
                    json={"jobId": "private-job", "revision": "private-revision"},
                )
        self.assertEqual(response.status_code, 200)
        tool, boundary, invocation = self.exporter.get_finished_spans()
        self.assertEqual(boundary.name, "POST /internal/cron/fire")
        self.assertEqual(boundary.parent.span_id, invocation.context.span_id)
        self.assertEqual(tool.parent.span_id, boundary.context.span_id)
        self.assertEqual(
            tool.attributes["autopilots.operation.id"],
            invocation.attributes["autopilots.operation.id"],
        )
        self.assertEqual(
            boundary.attributes["gen_ai.conversation.id"],
            invocation.attributes["gen_ai.conversation.id"],
        )


class ConfigurationTests(unittest.TestCase):
    def test_exporter_uses_only_selected_keyless_identity_and_disables_offline_storage(self):
        for managed_identity in (False, True):
            environment = {"APPLICATIONINSIGHTS_CONNECTION_STRING": "unit-test-connection"}
            if managed_identity:
                environment.update({"IDENTITY_ENDPOINT": "http://local-identity", "AZURE_CLIENT_ID": "worker-mi"})
            with (
                self.subTest(managed_identity=managed_identity),
                patch.dict(os.environ, environment, clear=True),
                patch.object(telemetry, "_provider", None),
                patch.object(telemetry, "_settings", None),
                patch.object(trace, "get_tracer_provider", return_value=trace.ProxyTracerProvider()),
                patch.object(trace, "set_tracer_provider"),
                patch("azure.identity.AzureCliCredential") as cli,
                patch("azure.identity.ManagedIdentityCredential") as mi,
                patch("azure.monitor.opentelemetry.exporter.AzureMonitorTraceExporter") as exporter,
                patch.object(telemetry, "BatchSpanProcessor"),
            ):
                provider = telemetry.configure_telemetry()
                if managed_identity:
                    mi.assert_called_once_with(client_id="worker-mi")
                    cli.assert_not_called()
                else:
                    cli.assert_called_once_with(process_timeout=120)
                    mi.assert_not_called()
                exporter.assert_called_once_with(
                    connection_string="unit-test-connection",
                    credential=(mi if managed_identity else cli).return_value,
                    disable_offline_storage=True,
                )
                provider.shutdown()

    def test_shared_defaults_and_opaque_ids(self):
        with patch.dict(os.environ, {"FOUNDRY_AGENT_NAME": "my-agent"}, clear=True):
            configuration = telemetry.TelemetrySettings.from_environment()
        self.assertEqual(configuration.agent_id, "my-agent")
        self.assertEqual(configuration.opaque_id("session", "one"), configuration.opaque_id("session", "one"))
        self.assertNotEqual(configuration.opaque_id("session", "one"), configuration.opaque_id("operation", "one"))

    def test_provider_configures_once_without_an_exporter_for_local_runs(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(telemetry, "_provider", None),
            patch.object(telemetry, "_settings", None),
            patch.object(trace, "get_tracer_provider", return_value=trace.ProxyTracerProvider()),
            patch.object(trace, "set_tracer_provider") as install,
        ):
            provider = telemetry.configure_telemetry()
            self.assertIs(telemetry.configure_telemetry(), provider)
            self.assertIs(telemetry.configure_telemetry("hermes-gateway", reuse_existing=True), provider)
            with self.assertRaisesRegex(RuntimeError, "one telemetry component"):
                telemetry.configure_telemetry("hermes-gateway")
            install.assert_called_once_with(provider)
            provider.shutdown()

    def test_preexisting_provider_fails_instead_of_adding_another_exporter(self):
        with (
            patch.object(telemetry, "_provider", None),
            patch.object(trace, "get_tracer_provider", return_value=TracerProvider()),
        ):
            with self.assertRaisesRegex(RuntimeError, "already installed"):
                telemetry.configure_telemetry()

    def test_sampling_must_be_a_ratio(self):
        with patch.dict(os.environ, {"OTEL_TRACES_SAMPLER_ARG": "1.5"}):
            with self.assertRaises(ValueError):
                telemetry.TelemetrySettings.from_environment()

    def test_hermes_main_configures_telemetry_before_runtime_setup(self):
        runtime_dir = Path(__file__).resolve().parents[1] / "runtimes" / "hermes"
        with patch.object(sys, "path", [str(runtime_dir), *sys.path]):
            start_hermes = importlib.import_module("start_hermes")
        with (
            patch.object(start_hermes, "configure_telemetry") as configure,
            patch.object(start_hermes, "hermes_home", side_effect=RuntimeError("stop before filesystem")),
        ):
            with self.assertRaisesRegex(RuntimeError, "stop before filesystem"):
                start_hermes.main()
        configure.assert_called_once_with("hermes-runtime")


if __name__ == "__main__":
    unittest.main()
