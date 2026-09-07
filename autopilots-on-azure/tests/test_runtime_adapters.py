import asyncio
import base64
import hashlib
import io
import inspect
import json
import os
import unittest
import urllib.error
from urllib.parse import quote
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

import bridge.app as bridge_app
import bridge.runtime.hermes as hermes_runtime
import scripts.sandbox_runtime as sandbox_runtime
from bridge.runtime.base import AgentRequest, AgentResponse, DreamRequest
from bridge.runtime.hermes import (
    HermesRuntimeAdapter,
    bridge_instructions,
    dream_prompt,
    explicit_learning_instructions,
    explicit_learning_prompt,
    parse_provenance_block,
    quarantine_recovery_instructions,
    session_reset_command,
)
from scripts.sandbox_runtime import (
    AgentSandboxConfig,
    config_from_environment,
    ensure_agent_sandbox,
    hermes_sandbox_config,
    require_worker_refresh_ready,
    runtime_labels,
)


SCENARIOS_JSON = json.dumps(json.loads(hermes_runtime.PROVENANCE_SHAPE_EXAMPLE)["agentProposedScenarios"])


def sandbox_config() -> AgentSandboxConfig:
    return AgentSandboxConfig(
        subscription_id="sub-1",
        resource_group="rg-1",
        sandbox_group="sandbox-group-1",
        region="swedencentral",
        foundry_openai_base_url="https://foundry.example/openai/v1",
        model_deployment="gpt-test",
        image_name="registry.example/hermes-runtime@sha256:test",
    )


class RuntimeAdapterTests(unittest.TestCase):
    def test_hermes_runtime_requests_propagate_trace_and_opaque_correlations(self):
        requests = []
        trace_headers = {
            "traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01",
            "x-autopilot-otel-session": "3" * 32,
            "x-autopilot-otel-operation": "4" * 32,
        }

        def handle(request):
            requests.append(request)
            return httpx.Response(200, json={"token": "turn-1"})

        adapter = HermesRuntimeAdapter(
            credential_factory=lambda: "credential-1",
            sandbox_config_factory=sandbox_config,
            ensure_sandbox=lambda *args, **kwargs: SimpleNamespace(
                sandbox_id="sandbox-1",
                endpoint_url="https://hermes.example",
                reused_existing_sandbox=True,
            ),
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
            await adapter._session_chat("https://hermes.example", "api-key", request)
            await adapter._cron_request(
                "https://hermes.example", "api-key", "GET", "/internal/cron/jobs"
            )
            await adapter._collective_learning_request(
                "GET", "/internal/collective-learning/pending"
            )
            await adapter._begin_learning_turn("https://hermes.example", "api-key")
            await adapter._reconcile_learning_turn(
                "https://hermes.example", "api-key", "turn-1", []
            )
            await adapter._abort_learning_turn(
                "https://hermes.example", "api-key", "turn-1"
            )

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

    def test_hermes_cancellation_aborts_learning_transaction(self):
        adapter = HermesRuntimeAdapter()
        request = AgentRequest(
            prompt="Update the document",
            conversation_id="conversation-1",
            user_id="user-1",
            source="teams_personal",
            must_answer=True,
        )
        invoke = AsyncMock(side_effect=asyncio.CancelledError())
        abort = AsyncMock()

        async def run() -> None:
            with (
                patch.object(adapter, "_invoke_hermes", invoke),
                patch.object(adapter, "_abort_learning_turn", abort),
            ):
                with self.assertRaises(asyncio.CancelledError):
                    await adapter._invoke_hermes_with_abort(
                        "https://sandbox.example",
                        "key",
                        request,
                        "snapshot-1",
                    )

        asyncio.run(run())
        abort.assert_awaited_once_with(
            "https://sandbox.example",
            "key",
            "snapshot-1",
        )

    def test_bridge_app_does_not_import_sandbox_lifecycle(self):
        source = inspect.getsource(bridge_app)

        self.assertNotIn("ensure_agent_sandbox", source)
        self.assertNotIn("AgentSandboxConfig", source)

    def test_bridge_caches_hermes_adapter(self):
        bridge_app.runtime_adapter.cache_clear()
        try:
            adapter = bridge_app.runtime_adapter()
            self.assertIsInstance(adapter, HermesRuntimeAdapter)
            self.assertIs(bridge_app.runtime_adapter(), adapter)
        finally:
            bridge_app.runtime_adapter.cache_clear()

    def test_hermes_adapter_prefers_stateful_session_chat(self):
        calls: list[dict] = []
        post_responses = [(200, {"output": " stateful OK "})]

        def ensure_sandbox(config, *, credential):
            return SimpleNamespace(
                sandbox_id="sandbox-1",
                endpoint_url="https://hermes.example",
                reused_existing_sandbox=True,
                data_volume="hermes-data",
            )

        previous_env = {key: os.environ.get(key) for key in ["API_SERVER_KEY", "HERMES_BRIDGE_ENDPOINT_MODE", "AUTOPILOT_NAME"]}
        os.environ["API_SERVER_KEY"] = "api-key-1"
        os.environ.pop("HERMES_BRIDGE_ENDPOINT_MODE", None)
        os.environ["AUTOPILOT_NAME"] = "hermes-worker"
        try:
            adapter = HermesRuntimeAdapter(
                credential_factory=lambda: "credential-1",
                sandbox_config_factory=sandbox_config,
                ensure_sandbox=ensure_sandbox,
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
            and call["url"].endswith("/chat")
        )
        readiness = next(call for call in calls if call["url"].endswith("/v1/models"))
        self.assertEqual(response.text, "stateful OK")
        self.assertEqual(response.raw["hermesEndpoint"], "sessions")
        self.assertEqual(readiness["headers"]["Authorization"], "Bearer api-key-1")
        self.assertLess(calls.index(readiness), calls.index(post))
        transcript_id = post["headers"]["X-Hermes-Session-Id"]
        self.assertRegex(
            transcript_id,
            r"^teams_personal:[0-9a-f]{24}:\d{8}T\d{2}$",
        )
        self.assertEqual(
            post["url"],
            (
                "https://hermes.example/api/sessions/"
                f"{quote(transcript_id, safe='')}/chat"
            ),
        )
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

    def test_hermes_adapter_fails_closed_when_session_chat_is_unavailable(self):
        calls: list[dict] = []
        post_responses = [(404, {"error": "not found"}), (200, {"output_text": "responses OK"})]

        def ensure_sandbox(config, *, credential):
            return SimpleNamespace(
                sandbox_id="sandbox-1",
                endpoint_url="https://hermes.example",
                reused_existing_sandbox=True,
                data_volume="hermes-data",
            )

        previous_env = {key: os.environ.get(key) for key in ["API_SERVER_KEY", "HERMES_BRIDGE_ENDPOINT_MODE", "AUTOPILOT_NAME"]}
        os.environ["API_SERVER_KEY"] = "api-key-1"
        os.environ["HERMES_BRIDGE_ENDPOINT_MODE"] = "sessions"
        os.environ["AUTOPILOT_NAME"] = "hermes-worker"
        try:
            adapter = HermesRuntimeAdapter(
                credential_factory=lambda: "credential-1",
                sandbox_config_factory=sandbox_config,
                ensure_sandbox=ensure_sandbox,
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
                call["url"].endswith("/chat")
                or call["url"].endswith("/v1/responses")
            )
        ]
        self.assertEqual(len(post_urls), 1)
        self.assertTrue(post_urls[0].endswith("/chat"))
        self.assertTrue(any(call["url"].endswith("/internal/learning/abort") for call in calls))

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

    def test_hermes_adapter_recovers_completed_turn_after_session_500(self):
        class RecoveringSessionClient:
            def __init__(self, **_kwargs):
                self.message_reads = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, url, *, headers):
                if url.endswith("/messages"):
                    self.message_reads += 1
                    payload = {
                        "data": [
                            {"role": "user", "content": "old"},
                            {"role": "assistant", "content": "old answer"},
                            *(
                                [
                                    {
                                        "role": "tool",
                                        "content": "tool result",
                                    },
                                    {
                                        "role": "assistant",
                                        "content": (
                                            "completed despite HTTP 500"
                                        ),
                                    },
                                ]
                                if self.message_reads >= 20
                                else []
                            ),
                        ]
                    }
                else:
                    payload = {
                        "session": {
                            "id": "session-1",
                            "message_count": 2,
                        }
                    }
                return httpx.Response(
                    200,
                    json=payload,
                    request=httpx.Request("GET", url),
                )

            async def post(self, url, *, headers, json):
                return httpx.Response(
                    500,
                    json={"error": "serialization failed"},
                    request=httpx.Request("POST", url),
                )

        request = AgentRequest(
            prompt="edit the document",
            conversation_id="teams:personal:conversation-1",
            user_id="user-1",
            source="teams_personal",
            must_answer=True,
        )
        adapter = HermesRuntimeAdapter(
            client_factory=RecoveringSessionClient,
        )

        sleep = AsyncMock()
        with patch.object(hermes_runtime.asyncio, "sleep", sleep):
            payload = asyncio.run(
                adapter._session_chat(
                    "https://hermes.example",
                    "api-key",
                    request,
                )
            )

        self.assertEqual(
            payload["message"]["content"],
            "completed despite HTTP 500",
        )
        self.assertEqual(payload["recovered_after_status"], 500)
        self.assertEqual(sleep.await_count, 19)

    def test_hermes_normal_turn_reconciles_native_skill_provenance(self):
        calls: list[dict] = []
        post_responses = [
            (
                200,
                {
                    "output": (
                        "I created the reusable procedure."
                        "<LEARNING_PROVENANCE_RECORDS>"
                        '[{"classification":"candidate_improvement",'
                        '"artifactPath":"skills/candidates/action-ownership","action":"create",'
                        '"title":"Require action ownership","generalizedLearning":"Require an accountable owner.",'
                        '"rationale":"Ownership prevents ambiguity.",'
                        '"evidence":[{"sourceType":"private_session","summary":"A generalized correction established the rule."}],'
                        f'"agentProposedScenarios":{SCENARIOS_JSON},'
                        '"confidence":0.9,"sourceStage":"foreground"}]'
                        "</LEARNING_PROVENANCE_RECORDS>"
                    )
                },
            )
        ]

        def ensure_sandbox(config, *, credential):
            return SimpleNamespace(
                sandbox_id="sandbox-1",
                endpoint_url="https://hermes.example",
                reused_existing_sandbox=True,
                data_volume="hermes-data",
            )

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
                credential_factory=lambda: "credential-1",
                sandbox_config_factory=sandbox_config,
                ensure_sandbox=ensure_sandbox,
                client_factory=lambda **kwargs: FakeHermesClient(calls, post_responses, **kwargs),
            )
            result = asyncio.run(
                adapter.invoke(
                    AgentRequest(
                        prompt="Learn this reusable procedure.",
                        conversation_id="session-1",
                        user_id="user-1",
                        source="teams_personal",
                        must_answer=True,
                    )
                )
            )
        finally:
            restore_env(previous)

        begin_post = next(call for call in calls if call["url"].endswith("/internal/learning/turns"))
        reconcile_post = next(call for call in calls if call["url"].endswith("/internal/learning/reconcile"))
        gateway_post = next(
            call
            for call in calls
            if call["method"] == "POST"
            and call["url"].endswith("/chat")
        )
        self.assertEqual(begin_post["json"], {})
        self.assertEqual(result.text, "I created the reusable procedure.")
        self.assertEqual(
            reconcile_post["json"]["provenance"][0]["artifactPath"],
            "skills/candidates/action-ownership",
        )
        self.assertEqual(result.raw["learningReconciliation"]["accepted"][0]["recordId"], "lr-test")
        self.assertIn("active Role Blueprint SOUL.md", gateway_post["json"]["instructions"])
        self.assertIn("<LEARNING_PROVENANCE_RECORDS>", gateway_post["json"]["instructions"])



    def test_learn_command_runs_one_constrained_transactional_turn(self):
        calls: list[dict] = []
        post_responses = [
            (
                200,
                {
                    "output": (
                        "I saved the reusable procedure."
                        "<LEARNING_PROVENANCE_RECORDS>"
                        '[{"classification":"candidate_improvement",'
                        '"artifactPath":"skills/candidates/action-ownership","action":"create",'
                        '"title":"Require action ownership","generalizedLearning":"Require an accountable owner.",'
                        '"rationale":"Ownership prevents ambiguity.",'
                        '"evidence":[{"sourceType":"private_session","summary":"A generalized correction established the rule."}],'
                        f'"agentProposedScenarios":{SCENARIOS_JSON},'
                        '"confidence":0.9,"sourceStage":"foreground"}]'
                        "</LEARNING_PROVENANCE_RECORDS>"
                    )
                },
            ),
        ]
        reconciliation_responses = [
            {
                "accepted": [{"recordId": "lr-learning"}],
                "rejected": [],
                "privatePlaybooksChanged": [],
                "governedArtifactsChanged": ["skills/candidates/action-ownership"],
                "rolledBack": False,
            },
        ]

        def ensure_sandbox(config, *, credential):
            return SimpleNamespace(
                sandbox_id="sandbox-1",
                endpoint_url="https://hermes.example",
                reused_existing_sandbox=True,
                data_volume="hermes-data",
            )

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
                credential_factory=lambda: "credential-1",
                sandbox_config_factory=sandbox_config,
                ensure_sandbox=ensure_sandbox,
                client_factory=lambda **kwargs: FakeHermesClient(
                    calls,
                    post_responses,
                    reconciliation_response=reconciliation_responses,
                    **kwargs,
                ),
            )
            result = asyncio.run(
                adapter.invoke(
                    AgentRequest(
                        prompt="/learn Require an accountable owner for every reusable action.",
                        conversation_id="session-1",
                        user_id="user-1",
                        source="teams_personal",
                        must_answer=True,
                    )
                )
            )
        finally:
            restore_env(previous)

        session_posts = [
            call
            for call in calls
            if call["method"] == "POST"
            and call["url"].endswith("/chat")
        ]
        turn_posts = [call for call in calls if call["url"].endswith("/internal/learning/turns")]
        reconcile_posts = [call for call in calls if call["url"].endswith("/internal/learning/reconcile")]
        self.assertEqual(result.text, "I saved the reusable procedure.")
        self.assertEqual(len(turn_posts), 1)
        self.assertEqual(len(session_posts), 1)
        self.assertEqual(
            session_posts[0]["json"]["input"],
            "Require an accountable owner for every reusable action.",
        )
        self.assertIn("explicit /learn request", session_posts[0]["json"]["instructions"])
        self.assertEqual(reconcile_posts[0]["json"]["provenance"][0]["sourceStage"], "foreground")
        self.assertEqual(result.raw["learningReconciliation"]["accepted"][0]["recordId"], "lr-learning")

    def test_ordinary_remember_request_does_not_start_fallback_turn(self):
        calls: list[dict] = []

        def ensure_sandbox(config, *, credential):
            return SimpleNamespace(
                sandbox_id="sandbox-1",
                endpoint_url="https://hermes.example",
                reused_existing_sandbox=True,
                data_volume="hermes-data",
            )

        previous = os.environ.get("API_SERVER_KEY")
        os.environ["API_SERVER_KEY"] = "api-key-1"
        try:
            adapter = HermesRuntimeAdapter(
                credential_factory=lambda: "credential-1",
                sandbox_config_factory=sandbox_config,
                ensure_sandbox=ensure_sandbox,
                client_factory=lambda **kwargs: FakeHermesClient(
                    calls,
                    [(200, {"output": "I understand."})],
                    **kwargs,
                ),
            )
            result = asyncio.run(
                adapter.invoke(
                    AgentRequest(
                        prompt="Remember this preference for later.",
                        conversation_id="session-1",
                        user_id="user-1",
                        source="teams_personal",
                        must_answer=True,
                    )
                )
            )
        finally:
            if previous is None:
                os.environ.pop("API_SERVER_KEY", None)
            else:
                os.environ["API_SERVER_KEY"] = previous

        session_posts = [
            call
            for call in calls
            if call["method"] == "POST"
            and call["url"].endswith("/chat")
        ]
        turn_posts = [call for call in calls if call["url"].endswith("/internal/learning/turns")]
        self.assertEqual(result.text, "I understand.")
        self.assertEqual(len(session_posts), 1)
        self.assertEqual(len(turn_posts), 1)

    def test_malformed_provenance_block_preserves_visible_answer_and_reports_failure(self):
        calls: list[dict] = []
        post_responses = [
            (
                200,
                {
                    "output": (
                        "The user-visible answer."
                        "<LEARNING_PROVENANCE_RECORDS>{}</LEARNING_PROVENANCE_RECORDS>"
                    )
                },
            )
        ]

        def ensure_sandbox(config, *, credential):
            return SimpleNamespace(
                sandbox_id="sandbox-1",
                endpoint_url="https://hermes.example",
                reused_existing_sandbox=True,
                data_volume="hermes-data",
            )

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
                credential_factory=lambda: "credential-1",
                sandbox_config_factory=sandbox_config,
                ensure_sandbox=ensure_sandbox,
                client_factory=lambda **kwargs: FakeHermesClient(calls, post_responses, **kwargs),
            )
            result = asyncio.run(
                adapter.invoke(
                    AgentRequest(
                        prompt="Answer this question.",
                        conversation_id="session-1",
                        user_id="user-1",
                        source="teams_personal",
                        must_answer=True,
                    )
                )
            )
        finally:
            restore_env(previous)

        self.assertIn("The user-visible answer.", result.text)
        self.assertIn("Local learning was not saved.", result.text)
        self.assertIn("JSON array", result.raw["learningCaptureError"])
        self.assertFalse(
            any(
                "/learning%3A" in call["url"]
                for call in calls
                if call["method"] == "POST"
                and call["url"].endswith("/chat")
            )
        )

    def test_hermes_dream_uses_isolated_session_reconciles_dream_provenance_and_returns_status(self):
        calls: list[dict] = []
        post_responses = [
            (
                200,
                {
                    "output": (
                        "Dream complete"
                        "<LEARNING_PROVENANCE_RECORDS>"
                        '[{"classification":"candidate_improvement",'
                        '"artifactPath":"skills/candidates/action-ownership","action":"create",'
                        '"title":"Require action ownership","generalizedLearning":"Require an accountable owner.",'
                        '"rationale":"Ownership prevents ambiguity.",'
                        '"evidence":[{"sourceType":"private_session","summary":"Generalized action gaps recurred."}],'
                        f'"agentProposedScenarios":{SCENARIOS_JSON},'
                        '"confidence":0.9,"sourceStage":"dream"}]'
                        "</LEARNING_PROVENANCE_RECORDS>"
                    )
                },
            )
        ]

        def ensure_sandbox(config, *, credential):
            return SimpleNamespace(
                sandbox_id="sandbox-1",
                endpoint_url="https://hermes.example",
                reused_existing_sandbox=True,
                data_volume="hermes-data",
            )

        previous_env = {key: os.environ.get(key) for key in ["API_SERVER_KEY", "AUTOPILOT_NAME"]}
        os.environ["API_SERVER_KEY"] = "api-key-1"
        os.environ["AUTOPILOT_NAME"] = "hermes-worker"
        try:
            adapter = HermesRuntimeAdapter(
                credential_factory=lambda: "credential-1",
                sandbox_config_factory=sandbox_config,
                ensure_sandbox=ensure_sandbox,
                client_factory=lambda **kwargs: FakeHermesClient(calls, post_responses, **kwargs),
            )
            result = asyncio.run(
                adapter.dream(
                    DreamRequest(
                        session_id="dream:hermes-worker",
                        focus="delivery follow-up",
                        max_records=3,
                    )
                )
            )
        finally:
            restore_env(previous_env)

        dream_post = next(
            call
            for call in calls
            if call["method"] == "POST"
            and call["url"].endswith("/chat")
        )
        reconcile_post = next(call for call in calls if call["url"].endswith("/internal/learning/reconcile"))
        status_get = next(call for call in calls if call["url"].endswith("/internal/learning/status"))
        self.assertRegex(
            dream_post["url"],
            (
                r"^https://hermes\.example/api/sessions/"
                r"dream%3A[0-9a-f]{24}%3A\d{8}T\d{2}/chat$"
            ),
        )
        self.assertIn("delivery follow-up", dream_post["json"]["input"])
        self.assertEqual(reconcile_post["json"]["provenance"][0]["sourceStage"], "dream")
        self.assertEqual(status_get["headers"]["X-Autopilot-Key"], "api-key-1")
        self.assertEqual(result.agent.text, "Dream complete")
        self.assertEqual(result.learning_status["status"], "ok")

    def test_parse_provenance_block_rejects_non_arrays_and_partial_markers(self):
        with self.assertRaisesRegex(ValueError, "JSON array"):
            parse_provenance_block("answer<LEARNING_PROVENANCE_RECORDS>{}</LEARNING_PROVENANCE_RECORDS>")
        with self.assertRaisesRegex(ValueError, "one complete learning provenance block"):
            parse_provenance_block("answer<LEARNING_PROVENANCE_RECORDS>[]")
        with self.assertRaisesRegex(ValueError, "one complete learning provenance block"):
            parse_provenance_block("answer</LEARNING_PROVENANCE_RECORDS>")

    def test_explicit_learning_prompt_accepts_only_learn_command(self):
        self.assertEqual(explicit_learning_prompt("/learn retain this rule"), "retain this rule")
        self.assertEqual(explicit_learning_prompt("  /LEARN\tretain this rule  "), "retain this rule")
        self.assertEqual(explicit_learning_prompt("/learn"), "")
        self.assertIsNone(explicit_learning_prompt("Remember this rule"))
        self.assertIsNone(explicit_learning_prompt("/learner retain this rule"))

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
        explicit = bridge_instructions(
            AgentRequest(
                prompt="Retain this rule.",
                conversation_id="learn-1",
                user_id="user-1",
                source="teams_personal",
                must_answer=True,
                metadata={"learningIntent": "explicit"},
            )
        )

        for instructions in (
            ordinary,
            dream,
            explicit,
            quarantine_recovery_instructions(),
            dream_prompt(DreamRequest(session_id="dream-1", focus="recent work", max_records=2)),
        ):
            self.assertIn("active Role Blueprint SOUL.md", instructions)
            self.assertIn("sole authority", instructions)

        duplicated_taxonomy = (
            "compact critical facts use Hermes USER.md or MEMORY.md",
            "rich assignment-specific knowledge or procedure",
            "generalized correction to existing role behavior",
            "new generalized reusable procedure",
        )
        for phrase in duplicated_taxonomy:
            self.assertNotIn(phrase, ordinary)
            self.assertNotIn(phrase, explicit)
            self.assertNotIn(phrase, quarantine_recovery_instructions())
        self.assertEqual(explicit, explicit_learning_instructions())

    def test_bridge_keeps_governed_learning_protocol_outside_soul(self):
        instructions = bridge_instructions(
            AgentRequest(
                prompt="Do the work.",
                conversation_id="session-1",
                user_id="user-1",
                source="teams_personal",
                must_answer=True,
            )
        )

        self.assertIn("<LEARNING_PROVENANCE_RECORDS>", instructions)
        self.assertIn("at most 3 provenance objects", instructions)
        self.assertIn("Private Playbook changes have no provenance object", instructions)
        self.assertIn("Never edit learning/records.jsonl directly", instructions)

    def test_learning_prompts_require_declarative_scenarios(self):
        from runtimes.hermes.learning import validate_agent_proposed_scenarios

        example = json.loads(hermes_runtime.PROVENANCE_SHAPE_EXAMPLE)
        validate_agent_proposed_scenarios(example["agentProposedScenarios"])
        request = AgentRequest(
            prompt="Improve the role.", conversation_id="conversation", user_id="user",
            source="teams_personal", must_answer=True,
        )
        for instructions in (
            bridge_instructions(request), explicit_learning_instructions(),
            quarantine_recovery_instructions(), dream_prompt(DreamRequest(session_id="dream")),
        ):
            self.assertIn("agentProposedScenarios", instructions)
            self.assertIn("response.text", instructions)
            self.assertIn("not_contains", instructions)
            self.assertIn("independent regression/holdout", instructions)
            self.assertNotIn("create, patch, or delete", instructions)

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
        for mode in ("auto", "responses", "chat_completions"):
            with patch.dict(os.environ, {"HERMES_BRIDGE_ENDPOINT_MODE": mode}):
                with patch.object(adapter, "_session_chat", new_callable=AsyncMock) as invoke:
                    with self.assertRaisesRegex(ValueError, "only 'sessions'"):
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

        def ensure_sandbox(config, *, credential):
            return SimpleNamespace(
                sandbox_id="sandbox-1",
                endpoint_url="https://hermes.example",
                reused_existing_sandbox=True,
                data_volume="hermes-data",
            )

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
                credential_factory=lambda: "credential-1",
                sandbox_config_factory=sandbox_config,
                ensure_sandbox=ensure_sandbox,
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
        self.assertEqual(prepared["sandboxId"], "sandbox-1")
        self.assertTrue(approved["approved"])
        self.assertEqual(exported["packet"]["packetVersion"], "2.0")

    def test_hermes_serializes_complete_learning_transactions(self):
        calls: list[dict] = []
        active = 0
        maximum_active = 0

        def ensure_sandbox(config, *, credential):
            return SimpleNamespace(
                sandbox_id="sandbox-1",
                endpoint_url="https://hermes.example",
                reused_existing_sandbox=True,
                data_volume="hermes-data",
            )

        previous = os.environ.get("API_SERVER_KEY")
        os.environ["API_SERVER_KEY"] = "api-key-1"
        try:
            adapter = HermesRuntimeAdapter(
                credential_factory=lambda: "credential-1",
                sandbox_config_factory=sandbox_config,
                ensure_sandbox=ensure_sandbox,
                client_factory=lambda **kwargs: FakeHermesClient(
                    calls,
                    [],
                    reconciliation_response={
                        "accepted": [],
                        "rejected": [],
                        "privatePlaybooksChanged": [],
                        "governedArtifactsChanged": [],
                        "rolledBack": False,
                    },
                    **kwargs,
                ),
            )

            async def invoke_hermes(base_url, api_key, request):
                nonlocal active, maximum_active
                active += 1
                maximum_active = max(maximum_active, active)
                await asyncio.sleep(0.02)
                active -= 1
                return "sessions", {"output": "Done.<LEARNING_PROVENANCE_RECORDS>[]</LEARNING_PROVENANCE_RECORDS>"}

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

    def test_next_bridge_turn_reconciles_quarantined_cli_candidate(self):
        calls: list[dict] = []
        provenance = (
            "<LEARNING_PROVENANCE_RECORDS>"
            '[{"classification":"candidate_improvement",'
            '"artifactPath":"skills/candidates/meeting-decision-record","action":"create",'
            '"title":"Meeting decision record",'
            '"generalizedLearning":"Record the decision, owner, effective date, and affected commitments.",'
            '"rationale":"Complete decision records improve follow-through.",'
            '"evidence":[{"sourceType":"tool_result","summary":"A direct CLI skill write was quarantined for review."}],'
            f'"agentProposedScenarios":{SCENARIOS_JSON},'
            '"confidence":0.9,"sourceStage":"operator"}]'
            "</LEARNING_PROVENANCE_RECORDS>"
        )
        post_responses = [
            (200, {"output": f"Recovered.{provenance}"}),
            (
                200,
                {
                    "output": (
                        "Normal answer."
                        "<LEARNING_PROVENANCE_RECORDS>[]</LEARNING_PROVENANCE_RECORDS>"
                    )
                },
            ),
        ]
        reconciliation_responses = [
            {
                "accepted": [{"recordId": "lr-recovered"}],
                "rejected": [],
                "privatePlaybooksChanged": [],
                "governedArtifactsChanged": ["skills/candidates/meeting-decision-record"],
                "rolledBack": False,
            },
            {
                "accepted": [],
                "rejected": [],
                "privatePlaybooksChanged": [],
                "governedArtifactsChanged": [],
                "rolledBack": False,
            },
        ]

        def ensure_sandbox(config, *, credential):
            return SimpleNamespace(
                sandbox_id="sandbox-1",
                endpoint_url="https://hermes.example",
                reused_existing_sandbox=True,
                data_volume="hermes-data",
            )

        previous = os.environ.get("API_SERVER_KEY")
        os.environ["API_SERVER_KEY"] = "api-key-1"
        try:
            adapter = HermesRuntimeAdapter(
                credential_factory=lambda: "credential-1",
                sandbox_config_factory=sandbox_config,
                ensure_sandbox=ensure_sandbox,
                client_factory=lambda **kwargs: FakeHermesClient(
                    calls,
                    post_responses,
                    reconciliation_response=reconciliation_responses,
                    recovered_unprovenanced=[
                        "skills/candidates/meeting-decision-record/SKILL.md"
                    ],
                    **kwargs,
                ),
            )
            result = asyncio.run(
                adapter.invoke(
                    AgentRequest(
                        prompt="Continue with an ordinary task.",
                        conversation_id="session-1",
                        user_id="user-1",
                        source="teams_personal",
                        must_answer=True,
                    )
                )
            )
        finally:
            if previous is None:
                os.environ.pop("API_SERVER_KEY", None)
            else:
                os.environ["API_SERVER_KEY"] = previous

        turn_posts = [call for call in calls if call["url"].endswith("/internal/learning/turns")]
        session_posts = [
            call
            for call in calls
            if call["method"] == "POST"
            and call["url"].endswith("/chat")
        ]
        self.assertEqual(len(turn_posts), 2)
        self.assertEqual(len(session_posts), 2)
        self.assertIn("learning/quarantine", session_posts[0]["json"]["instructions"])
        self.assertEqual(
            result.raw["quarantineRecovery"]["accepted"][0]["recordId"],
            "lr-recovered",
        )
        self.assertEqual(result.text, "Normal answer.")

    def test_hermes_aborts_learning_transaction_when_model_invocation_fails(self):
        calls: list[dict] = []

        def ensure_sandbox(config, *, credential):
            return SimpleNamespace(
                sandbox_id="sandbox-1",
                endpoint_url="https://hermes.example",
                reused_existing_sandbox=True,
                data_volume="hermes-data",
            )

        previous = os.environ.get("API_SERVER_KEY")
        os.environ["API_SERVER_KEY"] = "api-key-1"
        try:
            adapter = HermesRuntimeAdapter(
                credential_factory=lambda: "credential-1",
                sandbox_config_factory=sandbox_config,
                ensure_sandbox=ensure_sandbox,
                client_factory=lambda **kwargs: FakeHermesClient(
                    calls,
                    [(500, {"error": "model failed"})],
                    **kwargs,
                ),
            )
            with (
                patch.dict(os.environ, {"HERMES_SESSION_RECOVERY_TIMEOUT_SECONDS": "1"}),
                self.assertRaises(httpx.HTTPStatusError),
            ):
                asyncio.run(
                    adapter.invoke(
                        AgentRequest(
                            prompt="Ordinary task.",
                            conversation_id="session-1",
                            user_id="user-1",
                            source="teams_personal",
                            must_answer=True,
                        )
                    )
                )
        finally:
            if previous is None:
                os.environ.pop("API_SERVER_KEY", None)
            else:
                os.environ["API_SERVER_KEY"] = previous

        abort = next(call for call in calls if call["url"].endswith("/internal/learning/abort"))
        self.assertEqual(abort["json"], {"token": "lt-test-1"})

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
        self.assertEqual(config.data_volume_name, "hermes-data")
        self.assertEqual(runtime_labels(config)["kind"], "hermes")
        self.assertEqual(runtime_labels(config)["roleBlueprint"], "junior-project-manager")
        self.assertEqual(runtime_labels(config)["roleRelease"], "3.0.0")
        self.assertEqual(runtime_labels(config)["roleReleaseCommit"], "a" * 40)
        self.assertEqual(runtime_labels(config)["worker"], "worker-1")
        self.assertEqual(runtime_labels(config)["runtimeImage"], "hermes-api-server-image")

    @patch("scripts.sandbox_runtime.get_config", side_effect=lambda name, fallback="": os.getenv(name) or fallback)
    def test_environment_config_uses_predeployed_disk_and_runtime_identity(self, _get_config):
        image = "registry.example/runtime@sha256:" + "a" * 64
        with patch.dict(os.environ, {
            "AGENT_RUNTIME_DISK_IMAGE_ID": "ready-disk-1",
            "AGENT_RUNTIME_MANAGED_IDENTITY_CLIENT_ID": "runtime-client",
            "AGENT_RUNTIME_IMAGE": image,
            "DISK_SOURCE_IMAGE": "obsolete-conversion-source",
        }, clear=True):
            config = config_from_environment(
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
                ensure_agent_sandbox(config, wait_for_ready_seconds=0)
        create_client.assert_not_called()

    def test_hermes_environment_config_uses_runtime_volume_env(self):
        previous = os.environ.get("AGENT_RUNTIME_DATA_VOLUME_NAME")
        os.environ["AGENT_RUNTIME_DATA_VOLUME_NAME"] = "hermes-env-data"
        try:
            config = config_from_environment(
                subscription_id="sub-1",
                resource_group="rg-1",
                sandbox_group="sandbox-group-1",
                region="swedencentral",
                image_name="registry.example/hermes-runtime@sha256:test",
                api_server_key="api-key-1",
            )
        finally:
            if previous is None:
                os.environ.pop("AGENT_RUNTIME_DATA_VOLUME_NAME", None)
            else:
                os.environ["AGENT_RUNTIME_DATA_VOLUME_NAME"] = previous

        self.assertEqual(config.data_volume_name, "hermes-env-data")

    def test_existing_sandbox_reuse_uses_predeployed_disk_without_conversion(self):
        config = hermes_sandbox_config(
            subscription_id="sub-1",
            resource_group="rg-1",
            sandbox_group="sandbox-group-1",
            region="swedencentral",
            image_name="",
            disk_image_id="ready-disk-1",
            data_volume_name="hermes-data",
        )

        class SandboxClient:
            def ensure_running(self, timeout):
                self.timeout = timeout

            def get(self):
                return SimpleNamespace(ports=[SimpleNamespace(port=8642, url="https://gateway.example")])

            def exec(self, command):
                return SimpleNamespace(exit_code=0, stdout="", stderr="")

        class Client:
            _group_path = "/groups/test"

            def _dp_get(self, path):
                return [
                    {
                        "id": "other-sandbox",
                        "labels": {"app": "other-project"},
                        "volumes": [{"volumeName": "hermes-data"}],
                    },
                    {
                        "id": "sandbox-1",
                        "labels": {
                            "app": "autopilots-on-azure",
                            "kind": "hermes",
                            "identityArchitecture": "agent-federation-v1",
                            "runtimeImage": config.disk_image_name,
                        },
                        "volumes": [{"volumeName": "hermes-data"}],
                    },
                ]

            def get_sandbox_client(self, sandbox_id):
                return SandboxClient()

            def get_sandbox(self, sandbox_id):
                return SimpleNamespace(id=sandbox_id)

            def list_disk_images(self):
                raise AssertionError("Existing sandbox reuse must not inspect disk images.")

            def list_volumes(self):
                raise AssertionError("Existing sandbox reuse must not inspect volumes.")

        previous_factory = sandbox_runtime.create_sandbox_group_client
        sandbox_runtime.create_sandbox_group_client = lambda config, credential=None: Client()
        try:
            result = ensure_agent_sandbox(config, wait_for_ready_seconds=0)
        finally:
            sandbox_runtime.create_sandbox_group_client = previous_factory

        self.assertEqual(result.sandbox_id, "sandbox-1")
        self.assertEqual(result.endpoint_url, "https://gateway.example")
        self.assertTrue(result.reused_existing_sandbox)

    def test_worker_refresh_preflight_retries_transient_gateway_failure(self):
        config = hermes_sandbox_config(
            subscription_id="sub-1",
            resource_group="rg-1",
            sandbox_group="sandbox-group-1",
            region="swedencentral",
            image_name="registry.example/hermes-runtime@sha256:test",
            data_volume_name="hermes-data",
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
        reconciliation_response: dict | list[dict] | None = None,
        recovered_unprovenanced: list[str] | None = None,
        **kwargs,
    ):
        self.calls = calls
        self.post_responses = post_responses
        self.reconciliation_response = reconciliation_response or {
            "accepted": [{"recordId": "lr-test"}],
            "rejected": [],
            "privatePlaybooksChanged": [],
            "governedArtifactsChanged": ["skills/candidates/action-ownership"],
            "rolledBack": False,
        }
        self.recovered_unprovenanced = list(recovered_unprovenanced or [])
        self.begin_count = 0
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, *, headers: dict | None = None):
        self.calls.append({"method": "GET", "url": url, "headers": headers or {}})
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
                "packet": {"packetVersion": "2.0", "improvements": []},
                "receipt": {"approved": True},
            }
        else:
            raise AssertionError(f"Unexpected request URL: {url}")
        return httpx.Response(200, json=payload, request=httpx.Request(method, url))

    async def post(self, url: str, *, headers: dict, json: dict):
        self.calls.append({"method": "POST", "url": url, "headers": headers, "json": json})
        if url.endswith("/internal/learning/turns"):
            self.begin_count = sum(
                1
                for call in self.calls
                if call["url"].endswith("/internal/learning/turns")
            )
            recovered = self.recovered_unprovenanced if self.begin_count == 1 else []
            return httpx.Response(
                200,
                json={
                    "token": f"lt-test-{self.begin_count}",
                    "recoveredUnprovenancedFiles": recovered,
                },
                request=httpx.Request("POST", url),
            )
        if url.endswith("/internal/learning/reconcile"):
            reconciliation_response = (
                self.reconciliation_response.pop(0)
                if isinstance(self.reconciliation_response, list)
                else self.reconciliation_response
            )
            return httpx.Response(
                200,
                json=reconciliation_response,
                request=httpx.Request("POST", url),
            )
        if url.endswith("/internal/learning/abort"):
            return httpx.Response(
                200,
                json={"aborted": True, "token": json["token"]},
                request=httpx.Request("POST", url),
            )
        status_code, payload = self.post_responses.pop(0)
        return httpx.Response(status_code, json=payload, request=httpx.Request("POST", url))


def restore_env(previous: dict[str, str | None]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
