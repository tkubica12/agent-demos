from __future__ import annotations

import asyncio
import importlib
import ipaddress
import os
import ssl
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
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

    async def test_real_sync_and_async_https_targets_survive_export_without_urls_or_credentials(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "HTTP tracing test")])
            now = datetime.now(timezone.utc)
            certificate = (
                x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                               critical=False)
                .sign(key, hashes.SHA256())
            )
            cert_path, key_path = directory / "cert.pem", directory / "key.pem"
            cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
            key_path.write_bytes(key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
            ))
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert_path, key_path)
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            telemetry.configure_httpx_telemetry(self.provider)
            try:
                port = server.server_address[1]
                url = f"https://user:secret@127.0.0.1:{port}/private/path?token=secret-query"
                trust = ssl.create_default_context(cafile=str(cert_path))
                with telemetry.model_span("configured-model"):
                    with httpx.Client(verify=trust, trust_env=False) as client:
                        self.assertEqual(client.post(url, json={"prompt": "private prompt"}).status_code, 200)
                    async with httpx.AsyncClient(verify=trust, trust_env=False) as client:
                        self.assertEqual((await client.post(url, json={"prompt": "private prompt"})).status_code, 200)
            finally:
                HTTPXClientInstrumentor().uninstrument()
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
        from azure.monitor.opentelemetry.exporter.export.trace._exporter import _convert_span_to_envelope

        spans = self.exporter.get_finished_spans()
        model = next(span for span in spans if span.name == "chat")
        clients = [span for span in spans if span.kind == SpanKind.CLIENT and "http.request.method" in span.attributes]
        self.assertEqual(len(clients), 2)
        for span in clients:
            self.assertEqual(span.parent.span_id, model.context.span_id)
            self.assertEqual(span.context.trace_id, model.context.trace_id)
            self.assertEqual(span.attributes["server.address"], "127.0.0.1")
            self.assertEqual(span.attributes["server.port"], port)
            self.assertEqual(span.attributes["http.response.status_code"], 200)
            envelope = _convert_span_to_envelope(span)
            self.assertEqual(envelope.data.base_data.target, f"127.0.0.1:{port}")
            self.assertEqual(envelope.data.base_data.type, "HTTP")
            self.assertEqual(envelope.data.base_data.result_code, "200")
            self.assertTrue(envelope.data.base_data.success)
            exported = repr(envelope.as_dict())
            for private in ("private/path", "secret", "private prompt", "user:"):
                self.assertNotIn(private, exported)

    async def test_target_allowlist_rejects_url_credentials_and_non_client_configured_targets(self):
        for target, kind in (
            ("https://user:secret@host/private?token=secret", SpanKind.CLIENT),
            ("host?token=secret", SpanKind.CLIENT),
            ("configured-model-host.example", SpanKind.INTERNAL),
        ):
            with self.tracer.start_as_current_span("outgoing", kind=kind) as span:
                span.set_attributes({"server.address": target, "server.port": 443, "http.request.method": "POST"})
        for span in self.exporter.get_finished_spans():
            self.assertNotIn("server.address", span.attributes)
            self.assertNotIn("server.port", span.attributes)

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

        @bridge.post("/api/messages")
        async def receive_activity():
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
                "/api/messages", headers={"traceparent": parent, "baggage": "prompt=private"}
            )
        self.assertEqual(response.status_code, 200)
        spans = self.exporter.get_finished_spans()
        self.assertEqual(len(spans), 5)
        by_name = {span.name: span for span in spans}
        incoming = by_name["POST /api/messages"]
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

    async def test_bridge_has_no_model_operator_or_polling_routes(self):
        import bridge.app as bridge_app

        routes = {route.path for route in bridge_app.app.routes}
        self.assertTrue({
            "/invoke", "/invocations/{request_id}", "/internal/dream",
            "/internal/scheduled-learning/run", "/internal/scheduled-learning/status",
        }.isdisjoint(routes))

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

        def claim(*args, **kwargs):
            with telemetry.tool_span("interaction_claim"):
                return {"claimed": True}

        runtime = start_hermes.create_health_app(runtime_dir, runtime_dir, None, None)
        self.assertIn(start_hermes.flush_telemetry, runtime.router.on_shutdown)
        with (
            patch.dict(os.environ, {"API_SERVER_KEY": "runtime-test-key"}),
            patch.object(start_hermes, "claim_interaction", claim),
            telemetry.operation_span("invoke_agent", session_id="private-session", operation_id="job:revision"),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=runtime), base_url="http://runtime"
            ) as client:
                response = await client.post(
                    "/internal/interactions/claim",
                    headers={
                        **telemetry.runtime_trace_headers(),
                        "X-Autopilot-Key": "runtime-test-key",
                    },
                    json={"interactionId": "private-interaction", "choiceId": "c", "expiresAtUnix": 4102444800},
                )
        self.assertEqual(response.status_code, 200)
        tool, boundary, invocation = self.exporter.get_finished_spans()
        self.assertEqual(boundary.name, "POST /internal/interactions/claim")
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
                patch.object(telemetry, "configure_httpx_telemetry") as instrument,
            ):
                provider = telemetry.configure_telemetry()
                instrument.assert_called_once_with(provider)
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
            patch.object(telemetry, "configure_httpx_telemetry") as instrument,
        ):
            provider = telemetry.configure_telemetry()
            self.assertIs(telemetry.configure_telemetry(), provider)
            self.assertIs(telemetry.configure_telemetry("hermes-gateway", reuse_existing=True), provider)
            with self.assertRaisesRegex(RuntimeError, "one telemetry component"):
                telemetry.configure_telemetry("hermes-gateway")
            install.assert_called_once_with(provider)
            instrument.assert_called_once_with(provider)
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
