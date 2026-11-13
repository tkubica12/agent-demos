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
from unittest.mock import Mock, call, patch

import httpx
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from bridge import hermes_telemetry as native
from bridge import telemetry
from bridge.generated_files import GeneratedFileError, RUN_HEADER, SCOPE_HEADER


_ROOT = Path(__file__).resolve().parents[1]
_SMOKE_SPEC = importlib.util.spec_from_file_location(
    "native_telemetry_smoke", _ROOT / "runtimes" / "hermes" / "native_telemetry_smoke.py",
)
native_smoke = importlib.util.module_from_spec(_SMOKE_SPEC)
_SMOKE_SPEC.loader.exec_module(native_smoke)
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

    def test_native_build_guard_accepts_only_added_packages(self):
        before = {
            "opentelemetry-api": "1.45.1", "opentelemetry-sdk": "1.45.1",
            "opentelemetry-semantic-conventions": "0.66b1", "packaging": "26.3",
        }
        after = {**before, "opentelemetry-instrumentation-httpx": "0.66b1", "wrapt": "2.5.2"}
        native_smoke.verify_base_versions(before, after)

    def test_native_build_guard_refuses_replaced_or_removed_base_packages(self):
        before = {
            "opentelemetry-api": "1.45.1", "opentelemetry-sdk": "1.45.1",
            "opentelemetry-semantic-conventions": "0.66b1", "packaging": "26.3",
        }
        for name in before:
            with self.subTest(package=name, change="downgrade"):
                with self.assertRaisesRegex(RuntimeError, name):
                    native_smoke.verify_base_versions(before, {**before, name: "0.1"})
            with self.subTest(package=name, change="removal"):
                with self.assertRaisesRegex(RuntimeError, name):
                    native_smoke.verify_base_versions(before, {key: value for key, value in before.items() if key != name})

    def test_native_build_guard_refuses_incomplete_base_snapshot(self):
        with self.assertRaisesRegex(RuntimeError, "missing required"):
            native_smoke.verify_base_versions({}, {})

    def test_each_wrapper_start_replaces_dead_generations(self):
        stale = self.context_directory
        (stale / "lease.json").write_text("{}", encoding="utf-8")
        current = native.install_gateway_telemetry(self.home)
        self.assertFalse(stale.exists())
        self.assertEqual(list((self.home / ".telemetry").iterdir()), [current])
        self.assertEqual(os.environ[native.CONTEXT_DIRECTORY_ENV], str(current.resolve()))

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

    def test_exact_native_admission_can_resume_without_reassigning_trace(self):
        with telemetry.operation_span("first"):
            with self.assertRaises(TimeoutError):
                with native.gateway_trace_context("native-session", admission_id="same-admission"):
                    original = next(self.context_directory.glob("*.json")).read_text()
                    raise TimeoutError("The native run may still be active.")
        with telemetry.operation_span("resume"):
            with self.assertRaisesRegex(native.GatewayTraceConflictError, "different admission"):
                with native.gateway_trace_context("native-session", admission_id="another-admission"):
                    self.fail("Another admission stole the original trace.")
            with native.gateway_trace_context("native-session", admission_id="same-admission"):
                self.assertEqual(next(self.context_directory.glob("*.json")).read_text(), original)
        self.assertEqual(list(self.context_directory.iterdir()), [])

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

    def test_generated_file_wrapper_preserves_run_contract_and_releases_terminal_traces(self):
        with patch.object(sys, "path", [str(_ROOT / "runtimes" / "hermes"), *sys.path]):
            start_hermes = importlib.import_module("start_hermes")
        original_client = httpx.AsyncClient
        data = b"Generated unit report"
        entry = {
            "turnId": "a" * 32, "fileId": "b" * 32, "name": "report.txt", "contentType": "text/plain",
        }
        store = Mock()
        store.admit.side_effect = [{"turnId": value * 32} for value in ("a", "d", "e", "f")]
        store.instructions.return_value = "\nScoped file instructions"
        store.seal.return_value = {"output": "Report created.", "files": [entry]}
        store.download.return_value = (entry, data)

        async def gateway(request):
            if request.url.path.endswith("/messages"):
                return httpx.Response(200, json={"session_id": "native-session"})
            if request.method == "POST":
                body = json.loads(request.content)
                self.assertEqual(body["instructions"], "Original instructions\nScoped file instructions")
                self.assertEqual(int(request.headers["content-length"]), len(request.content))
                return httpx.Response(202, json={
                    "run_id": request.headers["Idempotency-Key"].replace("-admission", "-run"),
                })
            run_id = request.url.path.split("/")[-1]
            return httpx.Response(200, json={
                "run_id": run_id,
                "status": run_id.split("-")[0] if run_id in {"cancelled-run", "interrupted-run"} else "completed",
                "output": "Report created.\nMEDIA:/native/turn/report.txt", "usage": {"input_tokens": 7},
            })

        async def exercise():
            runtime = start_hermes.create_health_app(self.home, self.home, None, None)
            async with original_client(transport=httpx.ASGITransport(app=runtime), base_url="http://runtime") as client:
                with telemetry.operation_span("invoke_agent", session_id="private-session"):
                    headers = {
                        **telemetry.runtime_trace_headers(), "Authorization": "Bearer native-test",
                        "Idempotency-Key": "generated-admission", SCOPE_HEADER: "c" * 64,
                    }
                    admitted = await client.post(
                        "/v1/runs", headers=headers,
                        json={"input": "Generate report", "instructions": "Original instructions", "session_id": "session-1"},
                    )
                    self.assertEqual(admitted.status_code, 202)
                    self.assertEqual(len(list(self.context_directory.glob("*.lease"))), 1)
                    finished = await client.get("/v1/runs/generated-run", headers=headers)
                    self.assertEqual(finished.status_code, 200)
                    self.assertEqual(finished.json()["usage"], {"input_tokens": 7})
                    self.assertEqual(finished.json()["output"], "Report created.")
                    self.assertEqual(finished.json()["generatedAttachments"], {"protocolVersion": 1, "files": [entry]})
                    self.assertNotIn("/native/turn", finished.text)
                    self.assertEqual(list(self.context_directory.iterdir()), [])
                    file_headers = {
                        "X-Autopilot-Key": "native-test", SCOPE_HEADER: "c" * 64, RUN_HEADER: "generated-run",
                    }
                    url = f"/internal/generated-files/{entry['turnId']}/{entry['fileId']}"
                    self.assertEqual((await client.get(url)).status_code, 401)
                    store.download.assert_not_called()
                    downloaded = await client.get(url, headers=file_headers)
                    self.assertEqual(downloaded.status_code, 200)
                    self.assertEqual(downloaded.content, data)
                    store.download.assert_called_once_with(
                        entry["turnId"], entry["fileId"], scope="c" * 64, run_id="generated-run",
                    )
                    store.download.side_effect = GeneratedFileError("Another conversation")
                    self.assertEqual((await client.get(url, headers=file_headers)).status_code, 403)
                    store.seal.side_effect = GeneratedFileError("Outside the current turn")
                    refused = await client.get("/v1/runs/generated-run", headers=headers)
                    self.assertEqual(refused.status_code, 422)
                    self.assertIn("Outside the current turn", refused.json()["detail"])
                    self.assertEqual(list(self.context_directory.iterdir()), [])
                    headers["Idempotency-Key"] = "refused-admission"
                    admitted = await client.post(
                        "/v1/runs", headers=headers,
                        json={"input": "Generate report", "instructions": "Original instructions", "session_id": "session-1"},
                    )
                    self.assertEqual(admitted.status_code, 202)
                    self.assertEqual(len(list(self.context_directory.glob("*.lease"))), 1)
                    refused = await client.get("/v1/runs/refused-run", headers=headers)
                    self.assertEqual(refused.status_code, 422)
                    self.assertEqual(list(self.context_directory.iterdir()), [])
                    seals = store.seal.call_count
                    for status in ("cancelled", "interrupted"):
                        headers["Idempotency-Key"] = f"{status}-admission"
                        admitted = await client.post(
                            "/v1/runs", headers=headers,
                            json={"input": "Generate report", "instructions": "Original instructions",
                                  "session_id": "session-1"},
                        )
                        self.assertEqual(admitted.status_code, 202)
                        self.assertEqual(len(list(self.context_directory.glob("*.lease"))), 1)
                        stopped = await client.get(f"/v1/runs/{status}-run", headers=headers)
                        self.assertEqual(stopped.status_code, 200)
                        self.assertEqual(stopped.json()["status"], status)
                        self.assertNotIn("generatedAttachments", stopped.json())
                        self.assertEqual(list(self.context_directory.iterdir()), [])
                    self.assertEqual(store.seal.call_count, seals)

        with (
            patch.object(start_hermes, "GeneratedFileStore", return_value=store),
            patch.object(start_hermes, "hermes_account", return_value=(1000, 1000)),
            patch.object(start_hermes.httpx, "AsyncClient", side_effect=lambda **kwargs: original_client(
                transport=httpx.MockTransport(gateway), **kwargs,
            )),
            patch.dict(os.environ, {
                "API_SERVER_KEY": "native-test", "GENERATED_CHAT_FILES_ENABLED": "true", "WORKER_ID": "worker-1",
            }),
        ):
            asyncio.run(exercise())
        self.assertEqual(store.bind_run.call_args_list, [
            call(entry["turnId"], "generated-run"), call("d" * 32, "refused-run"),
            call("e" * 32, "cancelled-run"), call("f" * 32, "interrupted-run"),
        ])

    def test_native_run_evidence_is_read_only_authenticated_bounded_and_projected(self):
        with patch.object(sys, "path", [str(_ROOT / "runtimes" / "hermes"), *sys.path]):
            start_hermes = importlib.import_module("start_hermes")
        original_client = httpx.AsyncClient
        calls = []
        mode = "ok"

        async def gateway(request):
            calls.append(request)
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.host, "127.0.0.1")
            self.assertEqual(request.headers["Authorization"], "Bearer native-test")
            self.assertNotIn(SCOPE_HEADER, request.headers)
            if mode == "redirect":
                return httpx.Response(302, headers={"Location": "https://outside.invalid/secret"})
            if request.url.path.endswith("/events"):
                event = "replay.truncated" if mode == "truncated" else "run.completed"
                return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=(
                    b"x" * (1024 * 1024 + 1) if mode == "oversized" else
                    ("data: " + json.dumps({"run_id": "native-run", "event": event, "output": "secret"}) + "\n\n").encode()
                ))
            return httpx.Response(200, json={
                "run_id": "native-run", "status": "running" if mode == "running" else "completed",
                "output": "secret", "generatedAttachments": {"claim": "secret"},
                "runtime": {"provider": "azure-foundry", "model": "gpt-5", "route_source": "global"},
                "usage": {"input_tokens": 7},
            })

        async def exercise():
            nonlocal mode
            runtime = start_hermes.create_health_app(self.home, self.home, None, None)
            async with original_client(transport=httpx.ASGITransport(app=runtime), base_url="http://runtime") as client:
                url = "/internal/runs/native-run/evidence"
                self.assertEqual((await client.get(url)).status_code, 401)
                self.assertEqual(calls, [])
                headers = {"X-Autopilot-Key": "native-test"}
                response = await client.get(url, headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["runId"], "native-run")
                self.assertTrue(response.json()["runtimeMetadataAvailable"])
                self.assertNotIn("secret", response.text)
                self.assertEqual(len(calls), 2)
                for failure, expected in (("running", 409), ("truncated", 502), ("oversized", 502), ("redirect", 502)):
                    mode = failure
                    response = await client.get(url, headers=headers)
                    self.assertEqual(response.status_code, expected, response.text)
                    self.assertNotIn("secret", response.text)
                self.assertEqual((await client.get(
                    "/internal/runs/invalid%3Aid/evidence", headers=headers,
                )).status_code, 400)
            self.assertEqual({request.url.host for request in calls}, {"127.0.0.1"})

        with (
            patch.object(start_hermes, "GeneratedFileStore", return_value=Mock()),
            patch.object(start_hermes, "hermes_account", return_value=(1000, 1000)),
            patch.object(start_hermes.httpx, "AsyncClient", side_effect=lambda **kwargs: original_client(
                transport=httpx.MockTransport(gateway), **kwargs,
            )),
            patch.dict(os.environ, {
                "API_SERVER_KEY": "native-test", "GENERATED_CHAT_FILES_ENABLED": "true", "WORKER_ID": "worker-1",
            }),
        ):
            asyncio.run(exercise())

    def test_real_wrapper_context_handoff_and_ambiguous_http_failure(self):
        runtime_directory = _ROOT / "runtimes" / "hermes"
        with patch.object(sys, "path", [str(runtime_directory), *sys.path]):
            start_hermes = importlib.import_module("start_hermes")
        original_client = httpx.AsyncClient
        calls = []

        async def gateway(request):
            if request.url.path.endswith("/messages"):
                identifier = request.url.path.split("/")[3]
                return httpx.Response(200, json={
                    "session_id": "native-session" if identifier == "pre-compression-session" else identifier,
                })
            calls.append(request)
            if request.method == "POST":
                if json.loads(request.content)["session_id"] == "uncertain-session":
                    return httpx.Response(504, json={"error": "gateway timeout"})
                return httpx.Response(202, json={"run_id": "run-1", "status": "queued"})
            if "uncertain-session" in request.url.path:
                return httpx.Response(504, json={"error": "gateway timeout"})
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: native.tool_execution(
                    tool_name="read_file", args={}, next_call=lambda args: "private result",
                    session_id="native-session", task_id="native-session",
                ),
            )
            return httpx.Response(200, json={"run_id": "run-1", "status": "completed", "output": "done"})

        async def exercise():
            runtime = start_hermes.create_health_app(self.home, self.home, None, None)
            async with original_client(
                transport=httpx.ASGITransport(app=runtime), base_url="http://runtime",
            ) as client:
                with telemetry.operation_span("invoke_agent", session_id="private-session"):
                    headers = {
                        **telemetry.runtime_trace_headers(),
                        "Authorization": "Bearer native-test",
                        "Idempotency-Key": "admission-1",
                    }
                    success = await client.post(
                        "/v1/runs", headers=headers,
                        json={"input": "private prompt", "session_id": "pre-compression-session"},
                    )
                    self.assertEqual(success.status_code, 202)
                    self.assertEqual(len(list(self.context_directory.glob("*.lease"))), 1)
                    finished = await client.get("/v1/runs/run-1", headers=headers)
                    self.assertEqual(finished.status_code, 200)
                    self.assertEqual(list(self.context_directory.iterdir()), [])
                    uncertain_headers = {**headers, "Idempotency-Key": "admission-2"}
                    timeout = await client.post(
                        "/v1/runs", headers=uncertain_headers, json={"session_id": "uncertain-session"},
                    )
                    self.assertEqual(timeout.status_code, 504)
                    self.assertEqual(len(list(self.context_directory.glob("*.lease"))), 1)
                    conflict = await client.post(
                        "/v1/runs", headers={**headers, "Idempotency-Key": "admission-3"},
                        json={"session_id": "uncertain-session"},
                    )
                    self.assertEqual(conflict.status_code, 409)

        with patch.object(
            start_hermes.httpx, "AsyncClient",
            side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(gateway), **kwargs),
        ), patch.dict(os.environ, {"API_SERVER_KEY": "native-test"}):
            asyncio.run(exercise())
        self.assertEqual(len(calls), 3)
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
        plugin = self.home / "plugins" / "telemetry-under-test"
        plugin.mkdir(parents=True)
        (plugin / "plugin.yaml").write_text("name: telemetry-under-test\nversion: '1.0.0'\n", encoding="utf-8")
        (plugin / "__init__.py").write_text("from bridge.hermes_telemetry import register\n", encoding="utf-8")
        (self.home / "config.yaml").write_text(
            "plugins:\n  enabled:\n    - telemetry-under-test\n", encoding="utf-8",
        )
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
