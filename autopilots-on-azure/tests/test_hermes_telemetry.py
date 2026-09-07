from __future__ import annotations

import asyncio
import importlib
import json
import os
import shutil
import subprocess
import sys
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from bridge import hermes_telemetry as native
from bridge import telemetry


_ROOT = Path(__file__).resolve().parents[1]
_RUNTIME_PYTHON = _ROOT / "runtimes" / "hermes" / ".venv" / (
    "Scripts/python.exe" if os.name == "nt" else "bin/python"
)


class HermesTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.home = _ROOT / f".test-hermes-telemetry-{uuid.uuid4().hex}"
        self.home.mkdir()
        self.addCleanup(shutil.rmtree, self.home)
        self.environment = patch.dict(os.environ, {
            "HERMES_HOME": str(self.home),
            "APPLICATIONINSIGHTS_CONNECTION_STRING": "",
            "FOUNDRY_AGENT_NAME": "external-hermes",
            "OTEL_AGENT_ID": "external-hermes",
            "WORKER_ID": "private-worker",
            "HERMES_SAFE_MODE": "0",
            "HERMES_BUNDLED_PLUGINS": str(self.home / "no-bundled-plugins"),
            "HERMES_ENABLE_PROJECT_PLUGINS": "0",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.context_directory = native.install_gateway_telemetry(self.home)
        (self.home / "config.yaml").write_text(
            f"plugins:\n  enabled:\n    - {native.PLUGIN_NAME}\n", encoding="utf-8",
        )
        self.settings = telemetry.TelemetrySettings(
            agent_id="external-hermes", worker_id="private-worker", component="hermes-runtime",
        )
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider(resource=self.settings.resource())
        self.addCleanup(self.provider.shutdown)
        self.provider.add_span_processor(SimpleSpanProcessor(telemetry.MetadataOnlyExporter(self.exporter)))
        tracer_patch = patch.object(telemetry.trace, "get_tracer", return_value=self.provider.get_tracer("autopilots"))
        settings_patch = patch.object(telemetry, "_settings", self.settings)
        tracer_patch.start()
        settings_patch.start()
        self.addCleanup(tracer_patch.stop)
        self.addCleanup(settings_patch.stop)

    def test_execution_handoff_covers_native_executor_threads_and_session_rotation(self):
        request = {"messages": [{"content": "private prompt"}]}
        result = SimpleNamespace(
            model="gpt-test", usage=SimpleNamespace(prompt_tokens=11, completion_tokens=3),
        )
        with telemetry.operation_span("runtime", session_id="private-session", operation_id="operation"):
            with native.gateway_trace_context("native-session"), ThreadPoolExecutor() as pool:
                actual = pool.submit(
                    native.llm_execution, request=request, next_call=lambda payload: result,
                    session_id="compressed-session", task_id="native-session",
                    model="gpt-test", provider="azure-foundry",
                ).result()
                self.assertIs(actual, result)
                pool.submit(
                    native.tool_execution, tool_name="read_file", args={"path": "private-path"},
                    next_call=lambda payload: "private result", session_id="native-session",
                ).result()
        model, tool, wrapper = self.exporter.get_finished_spans()
        for span in (model, tool):
            self.assertEqual(span.parent.span_id, wrapper.context.span_id)
            self.assertEqual(span.context.trace_id, wrapper.context.trace_id)
            self.assertEqual(span.attributes["autopilots.trace.correlation"], "propagated")
            self.assertEqual(
                span.attributes["gen_ai.conversation.id"], wrapper.attributes["gen_ai.conversation.id"],
            )
        self.assertEqual(model.attributes["gen_ai.usage.input_tokens"], 11)
        self.assertEqual(model.attributes["gen_ai.usage.output_tokens"], 3)
        self.assertEqual(model.attributes["gen_ai.provider.name"], "azure.ai.openai")
        self.assertEqual(list(self.context_directory.iterdir()), [])
        self.assertNotIn("private", repr([dict(span.attributes) for span in (model, tool)]))

    def test_execution_error_is_not_retried_or_serialized(self):
        calls = []

        def fail(payload):
            calls.append(payload)
            raise ValueError("private model error")

        with self.assertRaises(ValueError):
            native.llm_execution(request={}, next_call=fail, session_id="private-session")
        self.assertEqual(len(calls), 1)
        span, = self.exporter.get_finished_spans()
        self.assertEqual(span.status.status_code, StatusCode.ERROR)
        self.assertIsNone(span.status.description)
        self.assertEqual(span.attributes["autopilots.trace.correlation"], "missing")
        self.assertEqual(span.events, ())

    def test_handoff_contains_only_context_and_blocks_overlapping_native_session(self):
        with telemetry.operation_span("runtime", session_id="private-session"):
            with native.gateway_trace_context("native-session"):
                path, = self.context_directory.glob("*.json")
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(set(data), {"owner", "expires_at", "headers"})
                self.assertEqual(set(data["headers"]), {"traceparent", "x-autopilot-otel-session"})
                self.assertNotIn("native-session", path.name)
                with self.assertRaisesRegex(native.GatewayTraceConflictError, "already active"):
                    with native.gateway_trace_context("native-session"):
                        self.fail("Overlapping native session was accepted")
        self.assertEqual(list(self.context_directory.iterdir()), [])

    def test_cancelled_request_cannot_reassign_still_running_gateway_work(self):
        with telemetry.operation_span("runtime"):
            with self.assertRaises(TimeoutError):
                with native.gateway_trace_context("native-session"):
                    raise TimeoutError("gateway thread might still run")
            with self.assertRaisesRegex(ValueError, "already active"):
                with native.gateway_trace_context("native-session"):
                    self.fail("A failed request's session was reassigned")
        self.assertEqual(len(list(self.context_directory.iterdir())), 2)

    def test_expired_handoff_stops_attribution_without_stealing_live_session(self):
        with telemetry.operation_span("runtime"):
            with native.gateway_trace_context("native-session", ttl_seconds=1):
                with patch.object(native.time, "time", return_value=10**12):
                    self.assertEqual(native._handoff("native-session", ""), {})
                    with self.assertRaisesRegex(ValueError, "already active"):
                        with native.gateway_trace_context("native-session"):
                            self.fail("An expired lease was stolen")

    def test_cleanup_cannot_remove_a_different_invocation_owner(self):
        with telemetry.operation_span("runtime"):
            with native.gateway_trace_context("native-session"):
                path, = self.context_directory.glob("*.json")
                lease = path.with_suffix(".lease")
                record = json.loads(path.read_text(encoding="utf-8"))
                record["owner"] = "new-owner"
                path.write_text(json.dumps(record), encoding="utf-8")
                lease.write_text("new-owner", encoding="utf-8")
            self.assertTrue(path.exists())
            self.assertEqual(lease.read_text(encoding="utf-8"), "new-owner")

    def test_wrong_profile_is_rejected_without_publishing_context(self):
        with telemetry.operation_span("runtime"), self.assertRaisesRegex(ValueError, "different runtime profile"):
            with native.gateway_trace_context("native-session", profile_home=self.home / "other"):
                self.fail("A different profile was accepted")
        self.assertEqual(list(self.context_directory.iterdir()), [])

    def test_metadata_publication_is_atomic_and_publish_failure_releases_lease(self):
        replace = native.os.replace

        def check_publication(source, destination):
            self.assertFalse(destination.exists())
            self.assertIn("traceparent", json.loads(source.read_text(encoding="utf-8"))["headers"])
            replace(source, destination)

        with telemetry.operation_span("runtime"):
            with patch.object(native.os, "replace", side_effect=check_publication):
                with native.gateway_trace_context("native-session", profile_home=self.home):
                    self.assertEqual(len(list(self.context_directory.glob("*.json"))), 1)
            with patch.object(native.os, "replace", side_effect=OSError("publish failed")):
                with self.assertRaises(OSError):
                    with native.gateway_trace_context("native-session"):
                        self.fail("A failed publication was accepted")
        self.assertEqual(list(self.context_directory.iterdir()), [])

    def test_missing_wrapper_span_is_explicit(self):
        with self.assertRaisesRegex(ValueError, "active wrapper span"):
            with native.gateway_trace_context("native-session"):
                self.fail("No wrapper span")

    def test_real_wrapper_context_handoff_and_ambiguous_http_failure(self):
        runtime_directory = _ROOT / "runtimes" / "hermes"
        with patch.object(sys, "path", [str(runtime_directory), *sys.path]):
            start_hermes = importlib.import_module("start_hermes")
        original_client = httpx.AsyncClient
        calls = []

        async def gateway(request):
            calls.append(request)
            if "uncertain-session" in request.url.path:
                return httpx.Response(504, json={"error": "gateway timeout"})
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: native.tool_execution(
                    tool_name="read_file", args={}, next_call=lambda args: "private result",
                    session_id="native-session", task_id="native-session",
                ),
            )
            return httpx.Response(200, json={"ok": True})

        async def exercise():
            runtime = start_hermes.create_health_app(self.home, self.home, None, None)
            async with original_client(
                transport=httpx.ASGITransport(app=runtime), base_url="http://runtime",
            ) as client:
                with telemetry.operation_span("invoke_agent", session_id="private-session"):
                    headers = telemetry.runtime_trace_headers()
                    success = await client.post(
                        "/api/sessions/native-session/chat", headers=headers, json={"input": "private prompt"},
                    )
                    self.assertEqual(success.status_code, 200)
                    self.assertEqual(list(self.context_directory.iterdir()), [])
                    timeout = await client.post("/api/sessions/uncertain-session/chat", headers=headers)
                    self.assertEqual(timeout.status_code, 504)
                    self.assertEqual(len(list(self.context_directory.glob("*.lease"))), 1)
                    conflict = await client.post("/api/sessions/uncertain-session/chat", headers=headers)
                    self.assertEqual(conflict.status_code, 409)

        with patch.object(
            start_hermes.httpx, "AsyncClient",
            side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(gateway), **kwargs),
        ):
            asyncio.run(exercise())
        self.assertEqual(len(calls), 2)
        spans = self.exporter.get_finished_spans()
        tool = next(span for span in spans if span.name == "execute_tool")
        wrapper = next(span for span in spans if span.context.span_id == tool.parent.span_id)
        invocation = next(span for span in spans if span.name == "invoke_agent")
        self.assertEqual(wrapper.parent.span_id, invocation.context.span_id)
        self.assertEqual(tool.context.trace_id, invocation.context.trace_id)
        self.assertEqual(tool.attributes["autopilots.trace.correlation"], "propagated")
        self.assertEqual(
            tool.attributes["gen_ai.conversation.id"], invocation.attributes["gen_ai.conversation.id"],
        )

    @unittest.skipUnless(_RUNTIME_PYTHON.is_file(), "Install the isolated Hermes runtime to exercise native plugins")
    def test_installed_hermes_plugin_executes_real_sdk_and_tool_in_separate_process(self):
        fixture = self.home / "fixture.txt"
        fixture.write_text("private fixture contents", encoding="utf-8")
        program = r'''
import json, os
from pathlib import Path
from types import SimpleNamespace
import httpx
from openai import OpenAI
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from bridge.telemetry import configure_telemetry, MetadataOnlyExporter
from hermes_cli.plugins import discover_plugins, get_plugin_manager
from hermes_cli.middleware import run_llm_execution_middleware
from agent.tool_executor import _run_agent_tool_execution_middleware
from tools.file_tools import read_file_tool

provider = configure_telemetry("hermes-gateway")
exporter = InMemorySpanExporter()
provider.add_span_processor(SimpleSpanProcessor(MetadataOnlyExporter(exporter)))
discover_plugins()
assert get_plugin_manager().has_middleware("llm_execution")
assert get_plugin_manager().has_middleware("tool_execution")

def respond(request):
    return httpx.Response(200, json={
        "id": "test", "object": "chat.completion", "created": 1, "model": "gpt-test",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "private reply"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    })

with OpenAI(api_key="offline-fixture", base_url="https://offline.test/v1",
            http_client=httpx.Client(transport=httpx.MockTransport(respond))) as client:
    result = run_llm_execution_middleware(
        {"model": "gpt-test", "messages": [{"role": "user", "content": "private prompt"}]},
        lambda request: client.chat.completions.create(**request),
        session_id="native-session", task_id="native-session", model="gpt-test",
        provider="azure-foundry",
    )
    assert result.usage.prompt_tokens == 5

result, args = _run_agent_tool_execution_middleware(
    SimpleNamespace(session_id="native-session"), function_name="read_file",
    function_args={"path": str(Path(os.environ["HERMES_HOME"]) / "fixture.txt")},
    effective_task_id="native-session", tool_call_id="test-call",
    execute=lambda args: read_file_tool(**args),
)
assert "private fixture contents" in result, result
print(json.dumps([{
    "name": span.name, "trace": span.context.trace_id, "parent": span.parent.span_id,
    "attributes": dict(span.attributes), "events": list(span.events),
} for span in exporter.get_finished_spans()]))
provider.shutdown()
'''
        with telemetry.operation_span("runtime", session_id="private-session"):
            with native.gateway_trace_context("native-session"):
                completed = subprocess.run(
                    [str(_RUNTIME_PYTHON), "-c", program],
                    cwd=_ROOT, env=os.environ.copy(), capture_output=True, text=True, timeout=90,
                )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        spans = json.loads(completed.stdout.strip().splitlines()[-1])
        wrapper, = self.exporter.get_finished_spans()
        self.assertEqual([span["name"] for span in spans], ["chat", "execute_tool"])
        for span in spans:
            self.assertEqual(span["trace"], wrapper.context.trace_id)
            self.assertEqual(span["parent"], wrapper.context.span_id)
            self.assertEqual(span["attributes"]["gen_ai.agent.id"], "external-hermes")
            self.assertEqual(span["attributes"]["autopilots.component"], "hermes-gateway")
            self.assertEqual(span["attributes"]["autopilots.trace.correlation"], "propagated")
        self.assertEqual(spans[0]["attributes"]["gen_ai.usage.input_tokens"], 5)
        self.assertNotIn("private", json.dumps(spans))


if __name__ == "__main__":
    unittest.main()
