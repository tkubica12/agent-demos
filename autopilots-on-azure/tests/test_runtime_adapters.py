import asyncio
import base64
import hashlib
import io
import inspect
import json
import os
import tempfile
import time
import unittest
import urllib.error
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

import bridge.app as bridge_app
import bridge.runtime.hermes as hermes_runtime
import scripts.sandbox_runtime as sandbox_runtime
from bridge.durable_transport import Journal
from bridge.runtime.base import AgentRequest, AgentResponse, DreamRequest
from bridge.generated_files import (
    GeneratedAttachment, GeneratedFileError, INPUT_SCOPE_HEADER, SCOPE_HEADER, RUN_HEADER,
    conversation_file_scope, input_file_scope,
)
from bridge.runtime.hermes import (
    OWNER_DIGEST_REFERENCE_KEY,
    PRIVATE_SESSION_PREFIX,
    HermesRuntimeAdapter,
    bridge_instructions,
    dream_prompt,
    runtime_error_detail,
    session_reset_command,
)
from scripts.sandbox_runtime import (
    RUNTIME_BACKUP_VOLUME,
    config_from_environment,
    deploy_agent_sandbox,
    hermes_sandbox_config,
    require_worker_refresh_ready,
    runtime_labels,
)



class RuntimeAdapterTests(unittest.TestCase):
    def test_original_input_bytes_are_uploaded_privately_before_one_native_admission(self):
        original = b"private original file bytes"
        request = AgentRequest(
            "Edit the attached file", "conversation-1", "user-1", "teams_personal", True,
            metadata={"attachmentsPrivate": True},
            input_files=(GeneratedAttachment("original.txt", "text/plain", original),),
        )
        calls = []

        def handle(outgoing):
            calls.append(outgoing)
            if outgoing.method == "PUT":
                self.assertEqual(outgoing.content, original)
                self.assertEqual(outgoing.headers["X-Autopilot-Key"], "runtime-key")
                self.assertEqual(outgoing.headers["X-Hermes-Session-Id"], "private:session-1")
                self.assertEqual(
                    outgoing.headers[INPUT_SCOPE_HEADER],
                    input_file_scope("worker-1", "teams_personal", "conversation-1", "user-1"),
                )
                return httpx.Response(201, json={
                    "turnId": "a" * 32, "index": 1, "size": len(original),
                    "sha256": hashlib.sha256(original).hexdigest(),
                })
            if outgoing.method == "POST":
                body = json.loads(outgoing.content)
                self.assertEqual(body["inputTurnId"], "a" * 32)
                self.assertEqual(body["inputFileCount"], 1)
                self.assertEqual(outgoing.headers["Idempotency-Key"], calls[0].url.path.split("/")[-2])
                self.assertNotIn(original.decode(), outgoing.content.decode())
                return httpx.Response(202, json={"run_id": "input-run"})
            return httpx.Response(200, json={"run_id": "input-run", "status": "completed", "output": "Edited"})

        adapter = HermesRuntimeAdapter(client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handle), **kwargs,
        ))
        with (
            patch.dict(os.environ, {"GENERATED_CHAT_FILES_ENABLED": "true", "WORKER_ID": "worker-1"}),
            patch.object(adapter, "_resolve_transcript_id", AsyncMock(return_value="session-1")),
            patch.object(adapter, "_ensure_live_session", AsyncMock(return_value="private:session-1")),
        ):
            result = asyncio.run(adapter._native_run("https://runtime.example", "runtime-key", request))
        self.assertEqual(result["output"], "Edited")
        self.assertEqual([call.method for call in calls], ["PUT", "POST", "GET"])
        self.assertNotIn(original.decode(), repr(request))

    def test_generated_file_download_is_authenticated_scoped_and_preserves_bytes(self):
        content = "Native generated report\n".encode("utf-8")
        turn_id, file_id = "a" * 32, "b" * 32
        request = AgentRequest("Generate a report", "conversation-1", "user-1", "teams_personal", True)
        payload = {
            "run_id": "native-run-1", "output": "",
            "generatedAttachments": {"protocolVersion": 1, "files": [{
                "turnId": turn_id, "fileId": file_id, "name": "report.txt", "contentType": "text/plain",
                "size": len(content), "sha256": hashlib.sha256(content).hexdigest(),
            }]},
        }
        calls = []

        def handle(outgoing):
            calls.append(outgoing)
            self.assertEqual(outgoing.url.path, f"/internal/generated-files/{turn_id}/{file_id}")
            self.assertEqual(outgoing.headers["X-Autopilot-Key"], "runtime-key")
            self.assertEqual(outgoing.headers[RUN_HEADER], "native-run-1")
            self.assertEqual(
                outgoing.headers[SCOPE_HEADER], conversation_file_scope("worker-1", "teams_personal", "conversation-1"),
            )
            return httpx.Response(200, content=content)

        adapter = HermesRuntimeAdapter(client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handle), **kwargs,
        ))
        with (
            patch.dict(os.environ, {"GENERATED_CHAT_FILES_ENABLED": "true", "WORKER_ID": "worker-1"}),
            patch.object(adapter, "_ready_runtime", AsyncMock(return_value=("https://runtime.example", "runtime-key"))),
            patch.object(adapter, "_invoke_hermes", AsyncMock(return_value=("runs", payload))),
        ):
            response = asyncio.run(adapter._invoke_turn(request))
            self.assertEqual(adapter._headers("runtime-key", request)[SCOPE_HEADER], calls[0].headers[SCOPE_HEADER])
        self.assertEqual(response.text, "")
        self.assertEqual(response.files[0].name, "report.txt")
        self.assertEqual(response.files[0].data, content)
        self.assertNotIn("Native generated report", repr(response))

    def test_generated_file_corruption_and_runtime_refusal_are_explicit_errors(self):
        request = AgentRequest("Generate a report", "conversation-1", "user-1", "teams_personal", True)
        payload = {
            "run_id": "native-run-1", "generatedAttachments": {"protocolVersion": 1, "files": [{
                "turnId": "a" * 32, "fileId": "b" * 32, "name": "report.txt", "contentType": "text/plain",
                "size": 5, "sha256": hashlib.sha256(b"hello").hexdigest(),
            }]},
        }
        with patch.dict(os.environ, {"GENERATED_CHAT_FILES_ENABLED": "true", "WORKER_ID": "worker-1"}):
            for status, content, expected in (
                (200, b"other", GeneratedFileError),
                (200, b"oversized", GeneratedFileError),
                (403, b"Another conversation", httpx.HTTPStatusError),
                (302, b"", httpx.HTTPStatusError),
            ):
                adapter = HermesRuntimeAdapter(client_factory=lambda **kwargs: httpx.AsyncClient(
                    transport=httpx.MockTransport(lambda outgoing: httpx.Response(status, content=content)), **kwargs,
                ))
                with self.subTest(status=status, content=content), self.assertRaises(expected):
                    asyncio.run(adapter._generated_attachments("https://runtime.example", "runtime-key", request, payload))
            adapter = HermesRuntimeAdapter()
            with self.assertRaisesRegex(GeneratedFileError, "enable"):
                asyncio.run(adapter._generated_attachments("https://runtime.example", "runtime-key", request, {}))
        with patch.dict(os.environ, {"GENERATED_CHAT_FILES_ENABLED": "false"}):
            self.assertEqual(
                asyncio.run(adapter._generated_attachments("https://runtime.example", "runtime-key", request, payload)), (),
            )

    def test_hermes_runtime_requests_propagate_trace_and_opaque_correlations(self):
        requests = []
        trace_headers = {
            "traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01",
            "x-autopilot-otel-session": "3" * 32,
            "x-autopilot-otel-operation": "4" * 32,
        }

        def handle(request):
            requests.append(request)
            if request.method == "POST" and request.url.path == "/v1/runs":
                return httpx.Response(202, json={"run_id": "run-trace"})
            if request.url.path == "/v1/runs/run-trace":
                return httpx.Response(200, json={
                    "run_id": "run-trace", "status": "completed", "output": "done",
                })
            return httpx.Response(200, json={"token": "turn-1"})

        adapter = HermesRuntimeAdapter(
            runtime_url_factory=lambda: "https://hermes.example",
            client_factory=lambda **kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(handle), **kwargs
            ),
        )
        request = AgentRequest(
            prompt="hello",
            conversation_id="conversation-1",
            user_id="user-1",
            source="invoke",
            must_answer=True,
        )

        async def run():
            await adapter._wait_for_health("https://hermes.example", "api-key")
            await adapter._native_run("https://hermes.example", "api-key", request)
            await adapter._cron_request(
                "https://hermes.example", "api-key", "GET", "/internal/cron/jobs"
            )
            await adapter._collective_learning_request(
                "GET", "/internal/collective-learning/pending"
            )
            await adapter.learning_digest()
            await adapter.undo_learning(entry_id="abcdef123456", decided_by="owner-1")

        with (
            patch.dict(os.environ, {"API_SERVER_KEY": "api-key"}),
            patch.object(hermes_runtime, "runtime_trace_headers", return_value=trace_headers),
        ):
            asyncio.run(run())

        self.assertGreaterEqual(len(requests), 12)
        for outgoing in requests:
            with self.subTest(path=outgoing.url.path):
                for name, value in trace_headers.items():
                    self.assertEqual(outgoing.headers[name], value)
                self.assertNotIn("baggage", outgoing.headers)
                if outgoing.url.path.startswith("/internal/"):
                    self.assertEqual(outgoing.headers["X-Autopilot-Key"], "api-key")

    def test_session_reset_command_accepts_only_exact_aliases(self):
        self.assertTrue(session_reset_command("/new"))
        self.assertTrue(session_reset_command("  /RESET  "))
        self.assertFalse(session_reset_command("/new continue"))
        self.assertFalse(session_reset_command("new"))

    def test_personal_new_command_resets_without_model_turn(self):
        adapter = HermesRuntimeAdapter()
        reset = AsyncMock(
            return_value=AgentResponse(
                text="Started a new topic.",
                raw={"sessionReset": "completed"},
            )
        )
        request = AgentRequest(
            prompt="/new",
            conversation_id="teams:personal:conversation-1",
            user_id="user-1",
            source="teams_personal",
            must_answer=True,
        )

        async def run():
            with patch.object(
                adapter,
                "_reset_transcript",
                reset,
            ):
                return await adapter.invoke(request)

        response = asyncio.run(run())

        self.assertEqual(response.raw["sessionReset"], "completed")
        reset.assert_awaited_once_with(request)

    def test_group_new_command_does_not_reset_shared_context(self):
        adapter = HermesRuntimeAdapter()
        reset = AsyncMock()
        request = AgentRequest(
            prompt="/new",
            conversation_id="teams:group:conversation-1",
            user_id="user-1",
            source="teams_group",
            must_answer=True,
        )

        async def run():
            with patch.object(
                adapter,
                "_reset_transcript",
                reset,
            ):
                return await adapter.invoke(request)

        response = asyncio.run(run())

        self.assertEqual(
            response.raw["sessionReset"],
            "unsupported_scope",
        )
        reset.assert_not_awaited()

    def test_gateway_reaches_runtime_only_through_its_native_url(self):
        for module in (bridge_app, hermes_runtime):
            source = inspect.getsource(module)
            with self.subTest(module=module.__name__):
                self.assertNotIn("sandbox_runtime", source)
                self.assertNotIn("deploy_agent_sandbox", source)
                self.assertNotIn("SandboxGroupClient", source)
        with patch.dict(os.environ, {"AGENT_RUNTIME_URL": "https://runtime.example/"}, clear=True):
            self.assertEqual(hermes_runtime.runtime_url_from_env(), "https://runtime.example")
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(RuntimeError, "AGENT_RUNTIME_URL"):
            hermes_runtime.runtime_url_from_env()

    def test_bridge_caches_hermes_adapter(self):
        bridge_app.runtime_adapter.cache_clear()
        try:
            adapter = bridge_app.runtime_adapter()
            self.assertIsInstance(adapter, HermesRuntimeAdapter)
            self.assertIs(bridge_app.runtime_adapter(), adapter)
        finally:
            bridge_app.runtime_adapter.cache_clear()

    def test_hermes_adapter_uses_durable_native_runs_and_stateful_sessions(self):
        calls: list[dict] = []
        post_responses = [(200, {"output": " stateful OK "})]


        previous_env = {key: os.environ.get(key) for key in ["API_SERVER_KEY", "HERMES_BRIDGE_ENDPOINT_MODE", "AUTOPILOT_NAME"]}
        os.environ["API_SERVER_KEY"] = "api-key-1"
        os.environ.pop("HERMES_BRIDGE_ENDPOINT_MODE", None)
        os.environ["AUTOPILOT_NAME"] = "hermes-worker"
        try:
            adapter = HermesRuntimeAdapter(
                runtime_url_factory=lambda: "https://hermes.example",
                client_factory=lambda **kwargs: FakeHermesClient(calls, post_responses, **kwargs),
            )
            response = asyncio.run(
                adapter.invoke(
                    AgentRequest(
                        prompt="hello",
                        conversation_id="teams:thread:1",
                        user_id="user-1",
                        source="teams_personal",
                        must_answer=True,
                    )
                )
            )
        finally:
            restore_env(previous_env)

        post = next(
            call
            for call in calls
            if call["method"] == "POST"
            and call["url"].endswith("/v1/runs")
        )
        readiness = next(call for call in calls if call["url"].endswith("/v1/models"))
        self.assertEqual(response.text, "stateful OK")
        self.assertEqual(response.raw["hermesEndpoint"], "runs")
        self.assertEqual(readiness["headers"]["Authorization"], "Bearer api-key-1")
        self.assertLess(calls.index(readiness), calls.index(post))
        transcript_id = post["headers"]["X-Hermes-Session-Id"]
        self.assertRegex(
            transcript_id,
            r"^teams_personal:[0-9a-f]{24}:\d{8}T\d{2}$",
        )
        self.assertEqual(
            post["url"],
            "https://hermes.example/v1/runs",
        )
        self.assertEqual(post["json"]["session_id"], transcript_id)
        self.assertTrue(post["headers"]["Idempotency-Key"].startswith("autopilots-"))
        self.assertEqual(post["headers"]["Authorization"], "Bearer api-key-1")
        session_key = post["headers"]["X-Hermes-Session-Key"]
        self.assertEqual(
            session_key,
            (
                "hermes-worker:teams_personal:user-1:"
                + hashlib.sha256(
                    b"teams:thread:1"
                ).hexdigest()[:16]
            ),
        )
        self.assertEqual(post["json"]["input"], "hello")

    def test_hermes_adapter_fails_closed_when_runs_are_unavailable(self):
        calls: list[dict] = []
        post_responses = [(404, {"error": "not found"}), (200, {"output_text": "responses OK"})]


        previous_env = {key: os.environ.get(key) for key in ["API_SERVER_KEY", "HERMES_BRIDGE_ENDPOINT_MODE", "AUTOPILOT_NAME"]}
        os.environ["API_SERVER_KEY"] = "api-key-1"
        os.environ["HERMES_BRIDGE_ENDPOINT_MODE"] = "runs"
        os.environ["AUTOPILOT_NAME"] = "hermes-worker"
        try:
            adapter = HermesRuntimeAdapter(
                runtime_url_factory=lambda: "https://hermes.example",
                client_factory=lambda **kwargs: FakeHermesClient(calls, post_responses, **kwargs),
            )
            with self.assertRaises(httpx.HTTPStatusError):
                asyncio.run(
                    adapter.invoke(
                        AgentRequest(
                            prompt="hello",
                            conversation_id="teams:thread:1",
                            user_id="user-1",
                            source="teams_personal",
                            must_answer=True,
                        )
                    )
                )
        finally:
            restore_env(previous_env)

        post_urls = [
            call["url"]
            for call in calls
            if call["method"] == "POST"
            and (
                call["url"].endswith("/v1/runs")
                or call["url"].endswith("/v1/responses")
            )
        ]
        self.assertEqual(len(post_urls), 1)
        self.assertTrue(post_urls[0].endswith("/v1/runs"))

    def test_hermes_adapter_creates_missing_native_session(self):
        calls = []

        class MissingSessionClient:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, url, *, headers):
                calls.append(
                    {
                        "method": "GET",
                        "url": url,
                        "headers": headers,
                    }
                )
                return httpx.Response(
                    404,
                    json={"error": "missing"},
                    request=httpx.Request("GET", url),
                )

            async def post(self, url, *, headers, json):
                calls.append(
                    {
                        "method": "POST",
                        "url": url,
                        "headers": headers,
                        "json": json,
                    }
                )
                return httpx.Response(
                    201,
                    json={"object": "hermes.session"},
                    request=httpx.Request("POST", url),
                )

        request = AgentRequest(
            prompt="hello",
            conversation_id="teams:personal:conversation-1",
            user_id="user-1",
            source="teams_personal",
            must_answer=True,
        )
        adapter = HermesRuntimeAdapter(
            client_factory=MissingSessionClient,
        )
        transcript_id = hermes_runtime._hermes_transcript_id(
            request
        )

        asyncio.run(
            adapter._ensure_session(
                "https://hermes.example",
                "api-key",
                request,
                transcript_id,
            )
        )

        self.assertEqual(calls[0]["method"], "GET")
        self.assertEqual(calls[1]["method"], "POST")
        self.assertEqual(calls[1]["url"], "https://hermes.example/api/sessions")
        self.assertEqual(calls[1]["json"]["id"], transcript_id)

    def test_native_run_moves_past_deleted_session_ids_to_next_generation(self):
        base_id = "teams_personal:abc:20261114T10"
        adapter, requests = self.runtime_adapter(
            self.deleted_session_handler({base_id, f"{base_id}:new:g1"}),
        )
        request = AgentRequest("hello", "conversation-1", "user-1", "teams_personal", True)
        with patch.object(hermes_runtime, "_hermes_transcript_id", return_value=base_id):
            result = asyncio.run(adapter._native_run("https://hermes.example", "api-key", request))

        live_id = f"{base_id}:new:g2"
        self.assertEqual(result["output"], "Answer.")
        self.assertEqual(self.created_sessions(requests), [base_id, f"{base_id}:new:g1", live_id])
        [admission] = self.admitted_runs(requests)
        self.assertEqual(json.loads(admission.content)["session_id"], live_id)

    def test_existing_session_conflict_keeps_the_derived_session_id(self):
        base_id = "teams_personal:abc:20261114T10"

        def handle(outgoing):
            if outgoing.method == "POST" and outgoing.url.path == "/api/sessions":
                return httpx.Response(409, json={"error": {"code": "session_exists", "message": "exists"}})
            return self.native_session_handler(outgoing)

        adapter, requests = self.runtime_adapter(handle)
        request = AgentRequest("hello", "conversation-1", "user-1", "teams_personal", True)
        live_id = asyncio.run(adapter._ensure_live_session("https://hermes.example", "api-key", request, base_id))

        self.assertEqual(live_id, base_id)
        self.assertEqual(self.created_sessions(requests), [base_id])

    def test_durable_admission_records_new_generation_before_admitting(self):
        base_id = "teams_personal:abc:20261114T10"
        adapter, requests = self.runtime_adapter(self.deleted_session_handler({base_id}))
        request = AgentRequest("hello", "conversation-1", "user-1", "teams_personal", True)
        with self.durable_delivery(base_id) as (journal, checkpoints):
            asyncio.run(adapter._native_run("https://hermes.example", "api-key", request))

        live_id = f"{base_id}:new:g1"
        self.assertEqual(checkpoints[0], (live_id, ""))
        self.assertEqual(checkpoints[1], (live_id, hashlib.sha256(b"api-key").hexdigest()))
        self.assertEqual(journal.get("worker", "delivery-1")["native_run_id"], "run-test")
        [admission] = self.admitted_runs(requests)
        self.assertEqual(json.loads(admission.content)["session_id"], live_id)
        self.assertEqual(admission.headers["Idempotency-Key"], "autopilots-delivery-1")

    def test_recorded_admission_never_moves_to_another_session_generation(self):
        base_id = "teams_personal:abc:20261114T10"
        request = AgentRequest("hello", "conversation-1", "user-1", "teams_personal", True)
        scope = hashlib.sha256(b"api-key").hexdigest()
        for deleted, run_id in (({base_id}, ""), ({base_id}, "run-test"), (set(), "")):
            with self.subTest(deleted=bool(deleted), run_id=run_id):
                adapter, requests = self.runtime_adapter(self.deleted_session_handler(deleted))
                with self.durable_delivery(
                    base_id, native_scope=scope, admission_at=time.time(), native_run_id=run_id,
                ) as (journal, _checkpoints):
                    if deleted and not run_id:
                        with self.assertRaisesRegex(
                            hermes_runtime.NativeRunInterventionError, "uncertain native admission",
                        ):
                            asyncio.run(adapter._native_run("https://hermes.example", "api-key", request))
                    else:
                        result = asyncio.run(adapter._native_run("https://hermes.example", "api-key", request))
                        self.assertEqual(result["output"], "Answer.")
                    self.assertEqual(journal.load_state("native-transcript:delivery-1"), base_id)
                self.assertEqual(self.created_sessions(requests), [base_id])
                admissions = self.admitted_runs(requests)
                if deleted:
                    self.assertEqual(admissions, [])
                else:
                    [admission] = admissions
                    self.assertEqual(json.loads(admission.content)["session_id"], base_id)
                    self.assertEqual(admission.headers["Idempotency-Key"], "autopilots-delivery-1")
                    self.assertEqual(journal.get("worker", "delivery-1")["native_run_id"], "run-test")

    def deleted_session_handler(self, deleted: set[str]):
        def handle(outgoing):
            if outgoing.method == "POST" and outgoing.url.path == "/api/sessions":
                if json.loads(outgoing.content)["id"] in deleted:
                    return httpx.Response(409, json={"error": {
                        "code": "session_deleted", "message": "Session was deleted; its id cannot be reused",
                    }})
            return self.native_session_handler(outgoing)

        return handle

    @staticmethod
    def created_sessions(requests: list[httpx.Request]) -> list[str]:
        return [
            json.loads(call.content)["id"]
            for call in requests
            if call.method == "POST" and call.url.path == "/api/sessions"
        ]

    @staticmethod
    def admitted_runs(requests: list[httpx.Request]) -> list[httpx.Request]:
        return [call for call in requests if call.method == "POST" and call.url.path == "/v1/runs"]

    @contextmanager
    def durable_delivery(self, transcript_id: str, **receipt):
        from bridge import durable_transport, event_worker

        directory = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(directory.cleanup)
        journal = Journal(Path(directory.name) / "journal.db")
        journal.admit(
            "worker", {"MessageId": "delivery-1", "LockToken": "lease", "Properties": {"DeliveryCount": 1}},
            {"type": "test"}, "connection",
        )
        journal.save_state("native-transcript:delivery-1", transcript_id)
        if receipt:
            journal.update("worker", "delivery-1", **receipt)
        checkpoints: list[tuple[str, str]] = []

        async def record_checkpoint():
            checkpoints.append((
                journal.load_state("native-transcript:delivery-1"),
                journal.get("worker", "delivery-1")["native_scope"],
            ))

        identity = durable_transport.delivery_identity.set("delivery-1")
        queue = durable_transport.delivery_queue.set("worker")
        try:
            with (
                patch.object(durable_transport, "enabled", return_value=True),
                patch.object(durable_transport, "journal", return_value=journal),
                patch.object(event_worker, "checkpoint", record_checkpoint),
            ):
                yield journal, checkpoints
        finally:
            durable_transport.delivery_queue.reset(queue)
            durable_transport.delivery_identity.reset(identity)

    def test_hermes_selects_latest_reset_generation(self):
        base_id = "teams_personal:abc:20260727T12"

        class SessionListClient:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, url, *, headers):
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "id": base_id,
                                "started_at": 1,
                            },
                            {
                                "id": f"{base_id}:new:latest",
                                "started_at": 3,
                            },
                            {
                                "id": f"{base_id}:new:older",
                                "started_at": 2,
                            },
                        ]
                    },
                    request=httpx.Request("GET", url),
                )

        request = AgentRequest(
            prompt="continue",
            conversation_id="conversation-1",
            user_id="user-1",
            source="teams_personal",
            must_answer=True,
        )
        adapter = HermesRuntimeAdapter(
            client_factory=SessionListClient,
        )
        with patch.object(
            hermes_runtime,
            "_hermes_transcript_id",
            return_value=base_id,
        ):
            result = asyncio.run(
                adapter._resolve_transcript_id(
                    "https://hermes.example",
                    "api-key",
                    request,
                )
            )

        self.assertEqual(result, f"{base_id}:new:latest")

    def test_native_poll_failure_does_not_guess_from_transcripts_or_submit_another_run(self):
        requests = []
        def handle(request):
            requests.append(request)
            if request.url.path == "/v1/runs" and request.method == "POST":
                return httpx.Response(202, json={"run_id": "run-uncertain"})
            if request.url.path == "/v1/runs/run-uncertain":
                return httpx.Response(500, json={"error": "status unavailable"})
            if request.url.path == "/api/sessions":
                return httpx.Response(200, json={"data": []})
            return httpx.Response(200, json={"session": {"message_count": 2}})
        request = AgentRequest(
            prompt="edit the document",
            conversation_id="teams:personal:conversation-1",
            user_id="user-1",
            source="teams_personal",
            must_answer=True,
        )
        adapter = HermesRuntimeAdapter(
            client_factory=lambda **kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(handle), **kwargs,
            ),
        )
        with self.assertRaises(httpx.HTTPStatusError):
            asyncio.run(
                adapter._native_run(
                    "https://hermes.example",
                    "api-key",
                    request,
                )
            )

        self.assertEqual(sum(r.method == "POST" and r.url.path == "/v1/runs" for r in requests), 1)
        self.assertFalse(any(r.url.path.endswith("/messages") for r in requests))

    def test_native_run_detects_completion_within_sub_second_polls(self):
        statuses = iter(["running", "running", "completed"])

        def handle(request):
            if request.url.path == "/v1/runs" and request.method == "POST":
                return httpx.Response(202, json={"run_id": "run-fast"})
            if request.url.path == "/v1/runs/run-fast":
                return httpx.Response(200, json={
                    "run_id": "run-fast", "status": next(statuses), "output": "done",
                })
            if request.url.path == "/api/sessions":
                return httpx.Response(200, json={"data": []})
            return httpx.Response(200, json={"session": {"message_count": 2}})

        request = AgentRequest(
            prompt="hello",
            conversation_id="teams:personal:conversation-1",
            user_id="user-1",
            source="teams_personal",
            must_answer=True,
        )
        adapter = HermesRuntimeAdapter(
            client_factory=lambda **kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(handle), **kwargs,
            ),
        )
        sleeps = []

        async def record_sleep(seconds):
            sleeps.append(seconds)

        with patch.object(hermes_runtime.asyncio, "sleep", record_sleep):
            result = asyncio.run(
                adapter._native_run("https://hermes.example", "api-key", request)
            )

        self.assertEqual(result["output"], "done")
        self.assertEqual(len(sleeps), 2)
        self.assertLess(sum(sleeps), 2)

    def test_native_control_wait_returns_same_runs_completed_widget(self):
        widget = "<HTML_WIDGET_REQUEST>" + json.dumps({
            "title": "Note", "html": '<input id="note">',
            "actions": [{"id": "send", "label": "Send"}],
        }) + "</HTML_WIDGET_REQUEST>"
        request = AgentRequest("Create a widget", "conversation-1", "user-1", "teams_personal", True)
        for waiting in ("waiting_for_approval", "waiting_for_clarification"):
            with self.subTest(waiting=waiting):
                statuses = iter((waiting, "completed"))

                def handle(outgoing):
                    if outgoing.method == "GET" and outgoing.url.path == "/v1/runs/run-test":
                        return httpx.Response(200, json={
                            "run_id": "run-test", "status": next(statuses), "output": widget,
                        })
                    return self.native_session_handler(outgoing)

                adapter, requests = self.runtime_adapter(handle)
                with patch.object(hermes_runtime.asyncio, "sleep", AsyncMock()):
                    result = asyncio.run(adapter._native_run("https://hermes.example", "api-key", request))
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["output"], widget)
                admissions = [call for call in requests if call.method == "POST" and call.url.path == "/v1/runs"]
                self.assertEqual(len(admissions), 1)
                self.assertEqual(
                    [call.url.path for call in requests if call.url.path.startswith("/v1/runs/")],
                    ["/v1/runs/run-test", "/v1/runs/run-test"],
                )

    def test_native_control_wait_deadline_does_not_admit_another_run(self):
        from bridge.event_worker import InvocationPendingError

        request = AgentRequest("Create a widget", "conversation-1", "user-1", "teams_personal", True)
        for waiting in ("waiting_for_approval", "waiting_for_clarification"):
            with self.subTest(waiting=waiting):
                ticks = iter((0.0, 0.0, 0.5, 1.0))

                def handle(outgoing):
                    if outgoing.method == "GET" and outgoing.url.path == "/v1/runs/run-test":
                        return httpx.Response(200, json={"run_id": "run-test", "status": waiting})
                    return self.native_session_handler(outgoing)

                adapter, requests = self.runtime_adapter(handle)
                with (
                    patch.dict(os.environ, {"HERMES_BRIDGE_TIMEOUT_SECONDS": "1"}),
                    patch.object(hermes_runtime, "time", SimpleNamespace(monotonic=lambda: next(ticks))),
                    patch.object(hermes_runtime.asyncio, "sleep", AsyncMock()),
                    self.assertRaisesRegex(InvocationPendingError, "run-test remains unfinished"),
                ):
                    asyncio.run(adapter._native_run("https://hermes.example", "api-key", request))
                self.assertEqual(
                    sum(call.method == "POST" and call.url.path == "/v1/runs" for call in requests), 1,
                )
                self.assertEqual(
                    [call.url.path for call in requests if call.url.path.startswith("/v1/runs/")],
                    ["/v1/runs/run-test", "/v1/runs/run-test"],
                )

    def runtime_adapter(self, handler):
        requests: list[httpx.Request] = []

        def record(request):
            requests.append(request)
            return handler(request)

        adapter = HermesRuntimeAdapter(
            runtime_url_factory=lambda: "https://hermes.example",
            client_factory=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(record), **kwargs),
        )
        adapter._wait_for_health = AsyncMock()
        return adapter, requests

    @staticmethod
    def native_session_handler(request):
        if request.method == "GET" and request.url.path == "/api/sessions":
            return httpx.Response(200, json={"data": []})
        if request.method == "GET" and request.url.path == "/v1/runs/run-test":
            return httpx.Response(200, json={
                "run_id": "run-test", "status": "completed", "output": "Answer.",
            })
        if request.method == "GET":
            return httpx.Response(404, json={"error": "missing"})
        if request.url.path == "/api/sessions":
            return httpx.Response(201, json={"session": {"id": json.loads(request.content)["id"]}})
        if request.url.path == "/v1/runs":
            return httpx.Response(202, json={"run_id": "run-test"})
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "Answer."}})

    def test_private_turns_use_a_marked_native_session_and_ordinary_turns_do_not(self):
        adapter, requests = self.runtime_adapter(self.native_session_handler)

        async def run():
            for metadata in ({}, {"attachmentsPrivate": True}, {"persistenceDisabled": True}):
                response = await adapter.invoke(AgentRequest(
                    prompt="Summarize", conversation_id="conversation-1", user_id="user-1",
                    source="teams_personal", must_answer=True, metadata=metadata,
                ))
                self.assertEqual(response.text, "Answer.")
                self.assertEqual(response.raw["privateSession"], bool(metadata))

        with patch.dict(os.environ, {"API_SERVER_KEY": "api-key"}):
            asyncio.run(run())
        chats = [request for request in requests if request.method == "POST" and request.url.path == "/v1/runs"]
        session_ids = [request.headers["X-Hermes-Session-Id"] for request in chats]
        self.assertFalse(session_ids[0].startswith(PRIVATE_SESSION_PREFIX))
        self.assertEqual(session_ids[1], PRIVATE_SESSION_PREFIX + session_ids[0])
        self.assertEqual(session_ids[2], session_ids[1])
        self.assertEqual(
            {request.headers["X-Hermes-Session-Key"] for request in chats}, {chats[0].headers["X-Hermes-Session-Key"]},
        )
        self.assertNotIn("<LEARNING_PROVENANCE", json.loads(chats[0].content)["instructions"])

    def test_learn_command_is_an_ordinary_native_turn(self):
        adapter, requests = self.runtime_adapter(self.native_session_handler)
        with patch.dict(os.environ, {"API_SERVER_KEY": "api-key"}):
            asyncio.run(adapter.invoke(AgentRequest(
                prompt="/learn Always confirm the timezone.", conversation_id="conversation-1", user_id="user-1",
                source="teams_personal", must_answer=True,
            )))
        chat = next(request for request in requests if request.method == "POST" and request.url.path == "/v1/runs")
        body = json.loads(chat.content)
        self.assertEqual(body["input"], "/learn Always confirm the timezone.")
        self.assertEqual(
            body["instructions"],
            bridge_instructions(AgentRequest(
                prompt="", conversation_id="conversation-1", user_id="user-1",
                source="teams_personal", must_answer=True,
            )),
        )
        self.assertFalse(any("/internal/learning" in request.url.path for request in requests))

    def test_dream_is_a_native_turn_and_returns_the_owner_digest(self):
        def handler(request):
            if request.url.path == "/internal/learning/digest":
                return httpx.Response(200, json={"changes": [{"entryId": "e1"}], "pending": [], "governed": []})
            return self.native_session_handler(request)

        adapter, requests = self.runtime_adapter(handler)
        with patch.dict(os.environ, {"API_SERVER_KEY": "api-key"}):
            result = asyncio.run(adapter.dream(DreamRequest(session_id="dream-1", focus="recent work", max_records=2)))
        chat = next(request for request in requests if request.method == "POST" and request.url.path == "/v1/runs")
        prompt = json.loads(chat.content)["input"]
        self.assertIn("at most 2 skills", prompt)
        self.assertIn("skill_manage", prompt)
        self.assertNotIn("provenance", prompt.lower())
        self.assertEqual(result.learning_digest["changes"][0]["entryId"], "e1")
        digest = next(request for request in requests if request.url.path == "/internal/learning/digest")
        self.assertEqual(digest.url.params["markPresented"], "false")

    def test_learning_decisions_call_the_secured_runtime_routes(self):
        def handler(request):
            if request.url.path == "/internal/learning/share":
                return httpx.Response(409, json={"detail": "skills/role/x changed after the digest was shown."})
            if request.url.path == "/internal/cron/delivery-reference" and request.method == "GET":
                return httpx.Response(404, json={"detail": "Delivery reference was not found."})
            return httpx.Response(200, json={"receiptId": "r", "path": request.url.path})

        adapter, requests = self.runtime_adapter(handler)

        async def run():
            await adapter.learning_digest(mark_presented=True, limit=5)
            await adapter.undo_learning(entry_id="abcdef123456", decided_by="owner-1")
            await adapter.decide_learning_pending(pending_id="1234abcd", approve=False, decided_by="owner-1")
            with self.assertRaises(httpx.HTTPStatusError) as conflict:
                await adapter.share_learning(
                    skill="skills/role/x", choice="share", decided_by="owner-1", artifact_hash="a" * 64,
                )
            self.assertEqual(runtime_error_detail(conflict.exception), "skills/role/x changed after the digest was shown.")
            await adapter.remember_owner_delivery({"referenceKey": "teams-x", "boundary": "one_to_one",
                                                   "conversation": {"conversation_reference": {}}})
            self.assertIsNone(await adapter.owner_delivery_reference())

        with patch.dict(os.environ, {"API_SERVER_KEY": "api-key"}):
            asyncio.run(run())
        self.assertTrue(all(request.headers["X-Autopilot-Key"] == "api-key" for request in requests))
        self.assertEqual(requests[0].url.params["markPresented"], "true")
        self.assertEqual(json.loads(requests[1].content), {"entryId": "abcdef123456", "decidedBy": "owner-1"})
        self.assertEqual(json.loads(requests[2].content)["approve"], False)
        self.assertEqual(json.loads(requests[3].content)["artifactHash"], "a" * 64)
        self.assertEqual(json.loads(requests[4].content)["referenceKey"], OWNER_DIGEST_REFERENCE_KEY)
        self.assertEqual(requests[5].url.params["referenceKey"], OWNER_DIGEST_REFERENCE_KEY)

    def test_hermes_instructions_defer_classification_to_active_soul(self):
        ordinary = bridge_instructions(
            AgentRequest(
                prompt="Remember this.",
                conversation_id="session-1",
                user_id="user-1",
                source="teams_personal",
                must_answer=True,
            )
        )
        dream = bridge_instructions(
            AgentRequest(
                prompt="Dream.",
                conversation_id="dream-1",
                user_id="operator",
                source="dream",
                must_answer=True,
            )
        )
        for instructions in (
            ordinary,
            dream,
            dream_prompt(DreamRequest(session_id="dream-1", focus="recent work", max_records=2)),
        ):
            self.assertIn("active Role Blueprint SOUL.md", instructions)
            self.assertIn("sole authority", instructions)
        for phrase in (
            "compact critical facts use Hermes USER.md or MEMORY.md",
            "rich assignment-specific knowledge or procedure",
            "generalized correction to existing role behavior",
            "new generalized reusable procedure",
        ):
            self.assertNotIn(phrase, ordinary)

    def test_learning_instructions_point_approvals_to_the_owner_digest(self):
        instructions = bridge_instructions(
            AgentRequest(
                prompt="Do the work.",
                conversation_id="session-1",
                user_id="user-1",
                source="teams_personal",
                must_answer=True,
            )
        )
        self.assertIn("learning digest with Undo", instructions)
        self.assertIn("never mention CLI commands", instructions)
        for removed in ("provenance", "records.jsonl", "agentProposedScenarios"):
            self.assertNotIn(removed, instructions)
            self.assertNotIn(removed, dream_prompt(DreamRequest(session_id="dream")))

    def test_group_history_is_shared_but_memory_keys_remain_per_user(self):
        first = AgentRequest(
            prompt="Project update", conversation_id="group-conversation", user_id="user-a",
            source="teams_group", must_answer=True,
        )
        second = AgentRequest(
            prompt="A follow-up", conversation_id="group-conversation", user_id="user-b",
            source="teams_group", must_answer=True,
        )
        self.assertEqual(hermes_runtime._hermes_transcript_id(first), hermes_runtime._hermes_transcript_id(second))
        self.assertNotEqual(hermes_runtime._hermes_session_key(first), hermes_runtime._hermes_session_key(second))

    def test_legacy_endpoint_modes_are_rejected_before_inference(self):
        adapter = HermesRuntimeAdapter()
        request = AgentRequest(
            prompt="hello", conversation_id="conversation", user_id="user", source="invoke", must_answer=True,
        )
        for mode in ("auto", "sessions", "responses", "chat_completions"):
            with patch.dict(os.environ, {"HERMES_BRIDGE_ENDPOINT_MODE": mode}):
                with patch.object(adapter, "_native_run", new_callable=AsyncMock) as invoke:
                    with self.assertRaisesRegex(ValueError, "only 'runs'"):
                        asyncio.run(adapter._invoke_hermes("http://runtime.test", "key", request))
                    invoke.assert_not_awaited()

    def test_office_lock_guidance_does_not_require_teams_mcp(self):
        request = AgentRequest(
            prompt="Edit this file.",
            conversation_id="conversation-1",
            user_id="user-1",
            source="invoke",
            must_answer=True,
        )

        with patch.dict(
            os.environ,
            {
                "M365_COLLABORATION_MCP_URL": (
                    "http://127.0.0.1:18082/mcp"
                ),
                "WORKIQ_TEAMS_MCP_URL": "",
            },
        ):
            instructions = bridge_instructions(request)

        self.assertIn("load the existing office-collaboration skill", instructions)
        self.assertIn("operationScope", instructions)
        self.assertNotIn("retry_pending_office_publish", instructions)
        self.assertNotIn("find_pending_office_publishes", instructions)
        self.assertNotIn("share_office_file_with_user", instructions)
        self.assertNotIn(
            "Agent User Teams collaboration is enabled",
            instructions,
        )

    def test_hermes_collective_learning_calls_secured_runtime_operations(self):
        calls: list[dict] = []


        previous = {
            "API_SERVER_KEY": os.environ.get("API_SERVER_KEY"),
            "COLLECTIVE_LEARNING_APPROVAL_PRIVATE_KEY": os.environ.get(
                "COLLECTIVE_LEARNING_APPROVAL_PRIVATE_KEY"
            ),
        }
        os.environ["API_SERVER_KEY"] = "api-key-1"
        os.environ["COLLECTIVE_LEARNING_APPROVAL_PRIVATE_KEY"] = base64.b64encode(
            b"\x01" * 32
        ).decode("ascii")
        try:
            adapter = HermesRuntimeAdapter(
                runtime_url_factory=lambda: "https://hermes.example",
                client_factory=lambda **kwargs: FakeHermesClient(calls, [], **kwargs),
            )
            prepared = asyncio.run(adapter.prepare_collective_learning())
            approved = asyncio.run(
                adapter.approve_collective_learning(
                    packet_digest="a" * 64,
                    approved_by="operator",
                )
            )
            exported = asyncio.run(adapter.export_collective_learning())
        finally:
            restore_env(previous)

        operation_calls = [call for call in calls if "/internal/collective-learning/" in call["url"]]
        self.assertEqual(
            [call["method"] for call in operation_calls],
            ["POST", "GET", "POST", "GET"],
        )
        self.assertEqual(
            operation_calls[2]["json"]["receipt"]["packetDigest"],
            "a" * 64,
        )
        self.assertEqual(operation_calls[2]["json"]["receipt"]["approvedBy"], "operator")
        self.assertEqual(len(operation_calls[2]["json"]["receipt"]["signature"]), 88)
        self.assertNotIn("sandboxId", prepared)
        self.assertTrue(approved["approved"])
        self.assertEqual(exported["packet"]["packetVersion"], "3.0")

    def test_pending_packet_read_does_not_wait_for_model_work_but_decisions_remain_guarded(self):
        async def run():
            lock = asyncio.Lock()
            adapter = HermesRuntimeAdapter(runtime_lock=lock)
            adapter._collective_learning_request = AsyncMock(return_value={"packetDigest": "a" * 64})
            async with lock:
                pending = await asyncio.wait_for(adapter.pending_collective_learning(), timeout=1)
                self.assertEqual(pending["packetDigest"], "a" * 64)
                guarded = [
                    asyncio.create_task(adapter.prepare_collective_learning()),
                    asyncio.create_task(adapter.prepare_refresh_rejection()),
                    asyncio.create_task(adapter.approve_collective_learning(
                        packet_digest="a" * 64, approved_by="authorized-owner",
                    )),
                    asyncio.create_task(adapter.export_collective_learning()),
                    asyncio.create_task(adapter.reject_and_refresh(
                        disposition_digest="b" * 64, rejected_by="authorized-owner", reason="Keep local",
                    )),
                ]
                try:
                    await asyncio.sleep(0)
                    self.assertTrue(all(not task.done() for task in guarded))
                    adapter._collective_learning_request.assert_awaited_once_with(
                        "GET", "/internal/collective-learning/pending",
                    )
                finally:
                    for task in guarded:
                        task.cancel()
                    await asyncio.gather(*guarded, return_exceptions=True)
        asyncio.run(run())

    def test_hermes_serializes_runtime_turns(self):
        calls: list[dict] = []
        active = 0
        maximum_active = 0


        previous = os.environ.get("API_SERVER_KEY")
        os.environ["API_SERVER_KEY"] = "api-key-1"
        try:
            adapter = HermesRuntimeAdapter(
                runtime_url_factory=lambda: "https://hermes.example",
                client_factory=lambda **kwargs: FakeHermesClient(calls, [], **kwargs),
            )

            async def invoke_hermes(base_url, api_key, request):
                nonlocal active, maximum_active
                active += 1
                maximum_active = max(maximum_active, active)
                await asyncio.sleep(0.02)
                active -= 1
                return "sessions", {"output": "Done."}

            adapter._invoke_hermes = invoke_hermes

            async def run_both():
                await asyncio.gather(
                    adapter.invoke(
                        AgentRequest(
                            prompt="First ordinary task.",
                            conversation_id="session-1",
                            user_id="user-1",
                            source="teams_personal",
                            must_answer=True,
                        )
                    ),
                    adapter.invoke(
                        AgentRequest(
                            prompt="Second ordinary task.",
                            conversation_id="session-2",
                            user_id="user-2",
                            source="teams_personal",
                            must_answer=True,
                        )
                    ),
                )

            asyncio.run(run_both())
        finally:
            if previous is None:
                os.environ.pop("API_SERVER_KEY", None)
            else:
                os.environ["API_SERVER_KEY"] = previous

        self.assertEqual(maximum_active, 1)

    def test_hermes_sandbox_config_can_be_built_without_starting_runtime(self):
        config = hermes_sandbox_config(
            image_name="registry.example/hermes-runtime@sha256:test",
            disk_image_id="ready-hermes-disk",
            api_server_key="api-key-1",
            private_incidents_mcp_url="https://mcp.example/mcp",
            private_incidents_mcp_scope="api://private/.default",
            agent365_tenant_id="tenant-1",
            agent365_blueprint_client_id="blueprint-1",
            agent365_agent_identity_client_id="agent-1",
            foundry_openai_base_url="https://foundry.example/openai/v1",
            model_deployment="gpt-test",
            role_blueprint="junior-project-manager",
            role_blueprint_source="https://github.com/example/blueprints.git",
            role_blueprint_path="blueprints/junior-project-manager",
            role_release="3.0.0",
            role_release_commit="a" * 40,
            worker_id="worker-1",
            assignment_scope="team-alpha",
            collective_learning_approval_public_key="approval-public-key",
        )

        self.assertEqual(config.runtime_kind, "hermes")
        self.assertEqual(config.port, 8642)
        self.assertEqual(config.health_path, "/health")
        self.assertEqual(config.command, ("/app/.venv/bin/python",))
        self.assertEqual(config.disk_image_id, "ready-hermes-disk")
        self.assertEqual(config.args, ("/app/start_hermes.py",))
        self.assertEqual(config.environment["PATH"].split(":")[0], "/app/.venv/bin")
        self.assertEqual(config.environment["PYTHONPATH"], "/app")
        self.assertEqual(config.environment["NODE_PATH"], "/app/node_modules")
        self.assertEqual(config.environment["API_SERVER_ENABLED"], "true")
        self.assertEqual(config.environment["API_SERVER_HOST"], "0.0.0.0")
        self.assertEqual(config.environment["API_SERVER_PORT"], "8642")
        self.assertEqual(config.environment["API_SERVER_KEY"], "api-key-1")
        self.assertEqual(config.environment["HERMES_HOME"], "/data/hermes")
        self.assertEqual(config.environment["FOUNDRY_OPENAI_BASE_URL"], "https://foundry.example/openai/v1")
        self.assertEqual(config.environment["HERMES_MODEL"], "gpt-test")
        self.assertEqual(config.environment["HERMES_ROLE_BLUEPRINT"], "junior-project-manager")
        self.assertEqual(config.environment["HERMES_ROLE_RELEASE"], "3.0.0")
        self.assertEqual(config.environment["HERMES_ROLE_RELEASE_COMMIT"], "a" * 40)
        self.assertEqual(config.environment["WORKER_ID"], "worker-1")
        self.assertEqual(
            config.environment["COLLECTIVE_LEARNING_APPROVAL_PUBLIC_KEY"],
            "approval-public-key",
        )
        self.assertEqual(config.environment["RUNTIME_BACKUP_DIR"], "/backup")
        self.assertEqual(runtime_labels(config)["kind"], "hermes")
        self.assertEqual(runtime_labels(config)["roleBlueprint"], "junior-project-manager")
        self.assertEqual(runtime_labels(config)["roleRelease"], "3.0.0")
        self.assertEqual(runtime_labels(config)["roleReleaseCommit"], "a" * 40)
        self.assertEqual(runtime_labels(config)["worker"], "worker-1")
        self.assertEqual(runtime_labels(config)["runtimeImage"], "hermes-api-server-image")

    def test_deployment_settings_use_predeployed_disk_and_runtime_identity(self):
        image = "registry.example/runtime@sha256:" + "a" * 64
        settings = {
            "AGENT_RUNTIME_DISK_IMAGE_ID": "ready-disk-1",
            "AGENT_RUNTIME_MANAGED_IDENTITY_CLIENT_ID": "runtime-client",
            "AGENT_RUNTIME_IMAGE": image,
            "AUTOPILOT_BRIDGE_URL": "https://gateway.example",
            "DISK_SOURCE_IMAGE": "obsolete-conversion-source",
        }
        with patch.dict(os.environ, {"AGENT_RUNTIME_DISK_IMAGE_ID": "operator-shell-disk"}, clear=True):
            config = config_from_environment(
                settings,
                subscription_id="sub-1",
                resource_group="rg-1",
                sandbox_group="sandbox-group-1",
                region="swedencentral",
                foundry_openai_base_url="https://foundry.example/openai/v1",
                api_server_key="api-key-1",
            )
        self.assertEqual(config.disk_image_id, "ready-disk-1")
        self.assertEqual(config.managed_identity_client_id, "runtime-client")
        self.assertEqual(config.image_name, image)
        self.assertEqual(config.runtime_image_reference, image)
        self.assertEqual(config.environment["AUTOPILOT_BRIDGE_URL"], "https://gateway.example")
        self.assertRegex(config.runtime_config_revision, r"^[0-9a-f]{16}$")
        changed = config_from_environment(
            {**settings, "AUTOPILOT_BRIDGE_URL": "https://replacement-gateway.example"},
            subscription_id="sub-1", resource_group="rg-1", sandbox_group="sandbox-group-1",
            region="swedencentral", api_server_key="api-key-1",
            foundry_openai_base_url="https://foundry.example/openai/v1",
        )
        self.assertNotEqual(changed.runtime_config_revision, config.runtime_config_revision)

    def test_missing_predeployed_disk_fails_before_any_sandbox_lifecycle_action(self):
        config = hermes_sandbox_config(
            subscription_id="sub-1",
            resource_group="rg-1",
            sandbox_group="sandbox-group-1",
            region="swedencentral",
            image_name="registry.example/runtime@sha256:" + "a" * 64,
        )
        with patch.object(sandbox_runtime, "create_sandbox_group_client") as create_client:
            with self.assertRaisesRegex(ValueError, "AGENT_RUNTIME_DISK_IMAGE_ID is required"):
                deploy_agent_sandbox(config)
        create_client.assert_not_called()

    def replacement_scenario(self, exec_result):
        config = hermes_sandbox_config(
            subscription_id="sub-1",
            resource_group="rg-1",
            sandbox_group="sandbox-group-1",
            region="swedencentral",
            image_name="",
            disk_image_id="ready-disk-1",
            role_release_commit="a" * 40,
        )
        events = []
        backup_volume = [{"volumeName": RUNTIME_BACKUP_VOLUME}]

        class SandboxClient:
            def __init__(self, sandbox_id):
                self.sandbox_id = sandbox_id

            def ensure_running(self, timeout):
                events.append(("wake", self.sandbox_id))

            def read_file(self, path):
                return json.dumps({"workingDirectory": "/app"})

            def exec(self, command):
                events.append(("exec", self.sandbox_id, command))
                return exec_result

        class Poller:
            def result(self):
                return None

        class Client:
            _group_path = "/groups/test"

            def _dp_get(self, path):
                return [
                    {"id": "old-runtime", "state": "Stopped", "volumes": backup_volume,
                     "labels": {**runtime_labels(config), "runtimeConfigRevision": "old"}},
                    {"id": "failed-runtime", "state": "Failed", "volumes": backup_volume,
                     "labels": {**runtime_labels(config), "runtimeConfigRevision": "older"}},
                    {"id": "sandbox-1", "state": "Stopped", "volumes": backup_volume,
                     "labels": runtime_labels(config),
                     "ports": [{"port": 8642, "url": "https://runtime.example/"}]},
                ]

            def get_sandbox_client(self, sandbox_id):
                return SandboxClient(sandbox_id)

            def begin_delete_sandbox(self, sandbox_id, polling_timeout):
                events.append(("delete", sandbox_id))
                return Poller()

        return config, Client(), events

    def test_replaced_runtime_publishes_final_backup_before_deletion(self):
        published = SimpleNamespace(exit_code=0, stderr="", stdout='{"archive": "hermes-backup-1.zip", "seconds": 4.2}\n')
        config, client, events = self.replacement_scenario(published)

        with patch.object(sandbox_runtime, "create_sandbox_group_client", return_value=client):
            result = deploy_agent_sandbox(config)

        command = "/app/.venv/bin/python /app/runtime_backup.py --home /data/hermes --backup-dir /backup"
        self.assertEqual(
            events,
            [("wake", "old-runtime"), ("exec", "old-runtime", command), ("delete", "old-runtime"),
             ("delete", "failed-runtime")],
        )
        self.assertEqual(result.sandbox_id, "sandbox-1")
        self.assertEqual(result.backup_volume, RUNTIME_BACKUP_VOLUME)

    def test_failed_final_backup_keeps_the_runtime_being_replaced(self):
        failed = SimpleNamespace(exit_code=1, stdout="", stderr="hermes backup failed (1)")
        config, client, events = self.replacement_scenario(failed)

        with patch.object(sandbox_runtime, "create_sandbox_group_client", return_value=client):
            with self.assertRaisesRegex(RuntimeError, "Final Worker backup on runtime Sandbox old-runtime failed"):
                deploy_agent_sandbox(config)

        self.assertNotIn(("delete", "old-runtime"), events)

    def test_deployment_reuses_matching_runtime_without_waking_or_exec(self):
        config = hermes_sandbox_config(
            subscription_id="sub-1",
            resource_group="rg-1",
            sandbox_group="sandbox-group-1",
            region="swedencentral",
            image_name="",
            disk_image_id="ready-disk-1",
        )

        class Client:
            _group_path = "/groups/test"

            def _dp_get(self, path):
                return [
                    {
                        "id": "other-sandbox",
                        "labels": {"app": "other-project"},
                        "volumes": [{"volumeName": RUNTIME_BACKUP_VOLUME}],
                    },
                    {
                        "id": "sandbox-1",
                        "state": "Stopped",
                        "labels": runtime_labels(config),
                        "volumes": [{"volumeName": RUNTIME_BACKUP_VOLUME}],
                        "ports": [{"port": 8642, "url": "https://runtime.example/", "activationMode": "OnDemand"}],
                    },
                ]

            def get_sandbox_client(self, sandbox_id):
                raise AssertionError("Reusing runtime compute must not resume it or execute commands.")

            def list_disk_images(self):
                raise AssertionError("Existing sandbox reuse must not inspect disk images.")

            def list_volumes(self):
                raise AssertionError("Existing sandbox reuse must not inspect volumes.")

        with patch.object(sandbox_runtime, "create_sandbox_group_client", return_value=Client()):
            result = deploy_agent_sandbox(config)

        self.assertEqual(result.sandbox_id, "sandbox-1")
        self.assertEqual(result.endpoint_url, "https://runtime.example")
        self.assertTrue(result.reused_existing_sandbox)

    def test_worker_refresh_preflight_retries_transient_gateway_failure(self):
        config = hermes_sandbox_config(
            subscription_id="sub-1",
            resource_group="rg-1",
            sandbox_group="sandbox-group-1",
            region="swedencentral",
            image_name="registry.example/hermes-runtime@sha256:test",
            api_server_key="api-key-1",
            role_blueprint="junior-project-manager",
            role_blueprint_source="https://example.com/roles.git",
            role_blueprint_path="roles/junior-project-manager",
            role_release="3.2.0",
            role_release_commit="b" * 40,
            worker_id="worker-1",
        )

        class SandboxClient:
            def ensure_running(self, timeout):
                self.timeout = timeout

            def get(self):
                return SimpleNamespace(
                    ports=[SimpleNamespace(port=8642, url="https://hermes.example")]
                )

        class Client:
            def get_sandbox_client(self, sandbox_id):
                self.sandbox_id = sandbox_id
                return SandboxClient()

        class JsonResponse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        transient = urllib.error.HTTPError(
            "https://hermes.example/internal/collective-learning/refresh-ready",
            502,
            "Bad Gateway",
            {},
            io.BytesIO(b'{"error":"Failed to forward request"}'),
        )
        ready = JsonResponse(b'{"ready":true}')

        with (
            patch.object(
                sandbox_runtime.urllib.request,
                "urlopen",
                side_effect=[transient, ready],
            ) as urlopen,
            patch.object(sandbox_runtime.time, "sleep"),
        ):
            require_worker_refresh_ready(
                Client(),
                config,
                {
                    "id": "sandbox-1",
                    "labels": {"roleReleaseCommit": "a" * 40},
                },
            )

        self.assertEqual(urlopen.call_count, 2)

    def test_hermes_response_text_parses_chat_completions(self):
        text = HermesRuntimeAdapter._response_text({"choices": [{"message": {"content": " hello from Hermes "}}]})

        self.assertEqual(text, "hello from Hermes")

    def test_hermes_response_text_parses_native_session_chat(self):
        text = HermesRuntimeAdapter._response_text(
            {
                "object": "hermes.session.chat.completion",
                "message": {
                    "role": "assistant",
                    "content": " native session reply ",
                },
            }
        )

        self.assertEqual(text, "native session reply")


class FakeHermesClient:
    def __init__(
        self,
        calls: list[dict],
        post_responses: list[tuple[int, dict]],
        **kwargs,
    ):
        self.calls = calls
        self.post_responses = post_responses
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, *, headers: dict | None = None):
        self.calls.append({"method": "GET", "url": url, "headers": headers or {}})
        if "/v1/runs/" in url:
            return httpx.Response(
                200, json={"run_id": "run-test", "status": "completed", "output": self.native_output},
                request=httpx.Request("GET", url),
            )
        return httpx.Response(200, json={"status": "ok"}, request=httpx.Request("GET", url))

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict,
        json: dict | None = None,
    ):
        self.calls.append({"method": method, "url": url, "headers": headers, "json": json})
        if url.endswith("/internal/collective-learning/prepare"):
            payload = {"packetDigest": "a" * 64, "improvements": [], "approvalRequired": True}
        elif url.endswith("/internal/collective-learning/pending"):
            payload = {
                "packetDigest": "a" * 64,
                "packet": {
                    "worker": {"workerId": "worker-1"},
                    "roleRelease": {"commit": "b" * 40},
                    "governedStateHash": "c" * 64,
                },
            }
        elif url.endswith("/internal/collective-learning/attest"):
            payload = {"approved": True, "packetDigest": "b" * 64}
        elif url.endswith("/internal/collective-learning/export"):
            payload = {
                "packet": {"packetVersion": "3.0", "improvements": []},
                "receipt": {"approved": True},
            }
        else:
            raise AssertionError(f"Unexpected request URL: {url}")
        return httpx.Response(200, json=payload, request=httpx.Request(method, url))

    async def post(self, url: str, *, headers: dict, json: dict):
        self.calls.append({"method": "POST", "url": url, "headers": headers, "json": json})
        status_code, payload = self.post_responses.pop(0)
        if url.endswith("/v1/runs") and status_code == 200:
            self.native_output = str(payload.get("output") or payload.get("output_text") or "")
            return httpx.Response(202, json={"run_id": "run-test"}, request=httpx.Request("POST", url))
        return httpx.Response(status_code, json=payload, request=httpx.Request("POST", url))


def restore_env(previous: dict[str, str | None]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
