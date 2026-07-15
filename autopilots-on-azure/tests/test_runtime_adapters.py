import asyncio
import inspect
import os
import unittest
from types import SimpleNamespace

import httpx

import bridge.app as bridge_app
import bridge.runtime.factory as runtime_factory
import bridge.runtime.openclaw as openclaw_runtime
import scripts.sandbox_runtime as sandbox_runtime
from bridge.gateway_client import OpenClawGatewayError
from bridge.runtime.base import AgentRequest, DreamRequest
from bridge.runtime.hermes import HermesRuntimeAdapter, extract_dream_candidates
from bridge.runtime.openclaw import OpenClawRuntimeAdapter
from scripts.sandbox_runtime import (
    AgentSandboxConfig,
    config_from_environment,
    ensure_agent_sandbox,
    hermes_sandbox_config,
    openclaw_sandbox_config,
    runtime_labels,
)


def sandbox_config() -> AgentSandboxConfig:
    return AgentSandboxConfig(
        subscription_id="sub-1",
        resource_group="rg-1",
        sandbox_group="sandbox-group-1",
        region="swedencentral",
        foundry_openai_base_url="https://foundry.example/openai/v1",
        model_deployment="gpt-test",
        image_name="registry.example/openclaw-runtime@sha256:test",
        gateway_token="gateway-token",
    )


class RuntimeAdapterTests(unittest.TestCase):
    def test_bridge_app_does_not_import_openclaw_protocol_or_sandbox_lifecycle(self):
        source = inspect.getsource(bridge_app)

        self.assertNotIn("bridge.gateway_client", source)
        self.assertNotIn("OpenClawGatewayClient", source)
        self.assertNotIn("ensure_gateway_sandbox", source)
        self.assertNotIn("GatewaySandboxConfig", source)

    def test_runtime_factory_defaults_to_openclaw(self):
        previous = os.environ.pop("AGENT_RUNTIME", None)
        try:
            adapter = runtime_factory.create_runtime_adapter()
        finally:
            if previous is not None:
                os.environ["AGENT_RUNTIME"] = previous

        self.assertEqual(adapter.runtime_kind, "openclaw")

    def test_runtime_factory_creates_hermes_adapter(self):
        previous = os.environ.get("AGENT_RUNTIME")
        os.environ["AGENT_RUNTIME"] = "hermes"
        try:
            adapter = runtime_factory.create_runtime_adapter()
        finally:
            if previous is None:
                os.environ.pop("AGENT_RUNTIME", None)
            else:
                os.environ["AGENT_RUNTIME"] = previous

        self.assertEqual(adapter.runtime_kind, "hermes")

    def test_runtime_factory_rejects_unknown_runtime(self):
        previous = os.environ.get("AGENT_RUNTIME")
        os.environ["AGENT_RUNTIME"] = "bogus"
        try:
            with self.assertRaisesRegex(ValueError, "Unsupported AGENT_RUNTIME 'bogus'"):
                runtime_factory.create_runtime_adapter()
        finally:
            if previous is None:
                os.environ.pop("AGENT_RUNTIME", None)
            else:
                os.environ["AGENT_RUNTIME"] = previous

    def test_openclaw_adapter_invokes_gateway_with_runtime_request(self):
        calls = {}

        def ensure_sandbox(config, *, credential):
            calls["config"] = config
            calls["credential"] = credential
            return SimpleNamespace(
                sandbox_id="sandbox-1",
                gateway_url="https://gateway.example",
                reused_existing_sandbox=True,
                data_volume="openclaw-data",
            )

        class Gateway:
            def __init__(self, **kwargs):
                calls["gateway_kwargs"] = kwargs

            async def invoke_agent(self, **kwargs):
                calls["invoke_kwargs"] = kwargs
                return "hello from OpenClaw"

        previous_gateway = openclaw_runtime.OpenClawGatewayClient
        openclaw_runtime.OpenClawGatewayClient = Gateway
        try:
            adapter = OpenClawRuntimeAdapter(
                credential_factory=lambda: "credential-1",
                sandbox_config_factory=sandbox_config,
                ensure_sandbox=ensure_sandbox,
            )
            response = asyncio.run(
                adapter.invoke(
                    AgentRequest(
                        prompt="hello",
                        conversation_id="session-1",
                        user_id="user-1",
                        source="invoke",
                        must_answer=True,
                    )
                )
            )
        finally:
            openclaw_runtime.OpenClawGatewayClient = previous_gateway

        self.assertEqual(response.text, "hello from OpenClaw")
        self.assertEqual(response.raw["sandboxId"], "sandbox-1")
        self.assertEqual(calls["credential"], "credential-1")
        self.assertEqual(calls["gateway_kwargs"]["url"], "wss://gateway.example/")
        self.assertEqual(calls["gateway_kwargs"]["token"], "gateway-token")
        self.assertEqual(calls["invoke_kwargs"]["message"], "hello")
        self.assertEqual(calls["invoke_kwargs"]["session_key"], "session-1")

    def test_openclaw_adapter_attaches_sandbox_details_to_pairing_errors(self):
        class Gateway:
            def __init__(self, **kwargs):
                pass

            async def invoke_agent(self, **kwargs):
                raise OpenClawGatewayError("pairing required: device is not approved yet")

        def ensure_sandbox(config, *, credential):
            return SimpleNamespace(
                sandbox_id="sandbox-1",
                gateway_url="https://gateway.example",
                reused_existing_sandbox=True,
                data_volume="openclaw-data",
            )

        previous_gateway = openclaw_runtime.OpenClawGatewayClient
        openclaw_runtime.OpenClawGatewayClient = Gateway
        try:
            adapter = OpenClawRuntimeAdapter(
                credential_factory=lambda: "credential-1",
                sandbox_config_factory=sandbox_config,
                ensure_sandbox=ensure_sandbox,
            )
            with self.assertRaises(OpenClawGatewayError) as raised:
                asyncio.run(
                    adapter.invoke(
                        AgentRequest(
                            prompt="hello",
                            conversation_id="session-1",
                            user_id="user-1",
                            source="invoke",
                            must_answer=True,
                        )
                    )
                )
        finally:
            openclaw_runtime.OpenClawGatewayClient = previous_gateway

        self.assertEqual(raised.exception.sandbox_id, "sandbox-1")
        self.assertEqual(raised.exception.gateway_url, "https://gateway.example")

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

        post = next(call for call in calls if call["method"] == "POST")
        self.assertEqual(response.text, "stateful OK")
        self.assertEqual(response.raw["hermesEndpoint"], "sessions")
        self.assertEqual(post["url"], "https://hermes.example/api/sessions/teams%3Athread%3A1/chat")
        self.assertEqual(post["headers"]["Authorization"], "Bearer api-key-1")
        self.assertEqual(post["headers"]["X-Hermes-Session-Id"], "teams:thread:1")
        self.assertEqual(post["headers"]["X-Hermes-Session-Key"], "hermes-worker:teams_personal:user-1")
        self.assertEqual(post["json"]["input"], "hello")

    def test_hermes_adapter_falls_back_to_responses_api_when_session_chat_is_unavailable(self):
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
        os.environ["HERMES_BRIDGE_ENDPOINT_MODE"] = "auto"
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

        post_urls = [call["url"] for call in calls if call["method"] == "POST"]
        self.assertEqual(response.text, "responses OK")
        self.assertEqual(response.raw["hermesEndpoint"], "responses")
        self.assertEqual(post_urls, ["https://hermes.example/api/sessions/teams%3Athread%3A1/chat", "https://hermes.example/v1/responses"])

    def test_hermes_normal_turn_submits_transferable_hot_learning(self):
        calls: list[dict] = []
        post_responses = [
            (
                200,
                {
                    "output": (
                        "I will apply that procedure."
                        "<TRANSFERABLE_LEARNING_RECORDS>"
                        '[{"classification":"transferable_procedural","title":"Owner required",'
                        '"generalizedLearning":"Require an accountable owner.","rationale":"Ownership prevents ambiguity.",'
                        '"evidence":[{"sourceType":"private_session","summary":"A generalized correction established the rule."}],'
                        '"confidence":0.9,"proposedTarget":{"kind":"skill","path":"skills/action-ownership"}}]'
                        "</TRANSFERABLE_LEARNING_RECORDS>"
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

        previous = os.environ.get("API_SERVER_KEY")
        os.environ["API_SERVER_KEY"] = "api-key-1"
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
                        prompt="Remember this reusable procedure.",
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

        candidate_post = next(call for call in calls if call["url"].endswith("/internal/learning/candidates"))
        gateway_post = next(call for call in calls if "/api/sessions/" in call["url"])
        self.assertEqual(result.text, "I will apply that procedure.")
        self.assertEqual(candidate_post["json"]["candidates"][0]["title"], "Owner required")
        self.assertEqual(result.raw["learningSubmission"]["accepted"][0]["recordId"], "lr-test")
        self.assertIn("local/private-cache.md", gateway_post["json"]["instructions"])
        self.assertIn("hot-learning skill", gateway_post["json"]["instructions"])

    def test_hermes_dream_uses_isolated_session_and_returns_learning_packet(self):
        calls: list[dict] = []
        post_responses = [
            (
                200,
                {
                    "output": (
                        "Dream complete\n"
                        "<TRANSFERABLE_LEARNING_RECORDS>"
                        '[{"classification":"transferable_procedural","title":"Ownership",'
                        '"generalizedLearning":"Require an owner.","rationale":"Actions need accountability.",'
                        '"evidence":[{"sourceType":"private_session","summary":"Generalized action gaps recurred."}],'
                        '"confidence":0.9,"proposedTarget":{"kind":"skill","path":"skills/action-ownership"}}]'
                        "</TRANSFERABLE_LEARNING_RECORDS>"
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

        post = next(call for call in calls if call["method"] == "POST")
        packet_get = next(call for call in calls if call["url"].endswith("/internal/learning/packet"))
        candidate_post = next(call for call in calls if call["url"].endswith("/internal/learning/candidates"))
        self.assertEqual(post["url"], "https://hermes.example/api/sessions/dream%3Ahermes-worker/chat")
        self.assertIn("delivery follow-up", post["json"]["input"])
        self.assertIn("at most 3", post["json"]["input"])
        self.assertEqual(candidate_post["json"]["candidates"][0]["title"], "Ownership")
        self.assertEqual(packet_get["headers"]["X-Autopilot-Key"], "api-key-1")
        self.assertEqual(result.learning_packet["packetVersion"], "1.0")
        self.assertEqual(result.agent.text, "Dream complete")

    def test_extract_dream_candidates_requires_json_array(self):
        with self.assertRaisesRegex(ValueError, "JSON array"):
            extract_dream_candidates(
                "summary<TRANSFERABLE_LEARNING_RECORDS>{}</TRANSFERABLE_LEARNING_RECORDS>"
            )

    def test_invalid_hot_learning_block_preserves_user_answer_and_surfaces_failure(self):
        calls: list[dict] = []
        post_responses = [
            (
                200,
                {
                    "output": (
                        "The user-visible answer."
                        "<TRANSFERABLE_LEARNING_RECORDS>{invalid json}</TRANSFERABLE_LEARNING_RECORDS>"
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

        previous = os.environ.get("API_SERVER_KEY")
        os.environ["API_SERVER_KEY"] = "api-key-1"
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
                        prompt="hello",
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

        self.assertIn("The user-visible answer.", result.text)
        self.assertIn("Local hot learning was not saved", result.text)
        self.assertIn("Invalid transferable-learning block", result.raw["learningCaptureError"])
        self.assertFalse(any(call["url"].endswith("/internal/learning/candidates") for call in calls))

    def test_openclaw_sandbox_config_preserves_gateway_defaults(self):
        config = openclaw_sandbox_config(
            image_name="registry.example/openclaw-runtime@sha256:test",
            gateway_token="token-1",
            foundry_openai_base_url="https://foundry.example/openai/v1",
            model_deployment="gpt-test",
            private_incidents_mcp_url="https://mcp.example/mcp",
            private_incidents_mcp_scope="api://private/.default",
            agent365_tenant_id="tenant-1",
            agent365_blueprint_client_id="blueprint-1",
            agent365_agent_identity_client_id="agent-1",
        )

        self.assertEqual(config.runtime_kind, "openclaw")
        self.assertEqual(config.port, 18789)
        self.assertEqual(config.command, ("python3",))
        self.assertEqual(config.args, ("-m", "openclaw_gateway.start_gateway"))
        self.assertEqual(config.data_mount_path, "/data")
        self.assertEqual(config.environment["OPENCLAW_GATEWAY_TOKEN"], "token-1")
        self.assertEqual(config.environment["PRIVATE_INCIDENTS_MCP_URL"], "http://127.0.0.1:18081/servers/private-incidents")
        self.assertIn("https://mcp.example/mcp", config.environment["AGENT_MCP_SERVERS_JSON"])
        self.assertNotIn("runtime", runtime_labels(config))
        self.assertNotIn("autopilot", runtime_labels(config))
        self.assertEqual(runtime_labels(config)["kind"], "openclaw")

    def test_hermes_sandbox_config_can_be_built_without_starting_runtime(self):
        config = hermes_sandbox_config(
            image_name="registry.example/hermes-runtime@sha256:test",
            api_server_key="api-key-1",
            private_incidents_mcp_url="https://mcp.example/mcp",
            private_incidents_mcp_scope="api://private/.default",
            agent365_tenant_id="tenant-1",
            agent365_blueprint_client_id="blueprint-1",
            agent365_agent_identity_client_id="agent-1",
            foundry_openai_base_url="https://foundry.example/openai/v1",
            model_deployment="gpt-test",
            blueprint_name="junior-project-manager",
            blueprint_source="https://github.com/example/blueprints.git",
            blueprint_path="blueprints/junior-project-manager",
            blueprint_version="1.0.0",
            blueprint_commit="a" * 40,
            instance_id="worker-1",
            assignee_scope="team-alpha",
        )

        self.assertEqual(config.runtime_kind, "hermes")
        self.assertEqual(config.port, 8642)
        self.assertEqual(config.health_path, "/health")
        self.assertEqual(config.command, ("python3",))
        self.assertEqual(config.args, ("/app/start_hermes.py",))
        self.assertEqual(config.environment["API_SERVER_ENABLED"], "true")
        self.assertEqual(config.environment["API_SERVER_HOST"], "0.0.0.0")
        self.assertEqual(config.environment["API_SERVER_PORT"], "8642")
        self.assertEqual(config.environment["API_SERVER_KEY"], "api-key-1")
        self.assertEqual(config.environment["HERMES_HOME"], "/data/hermes")
        self.assertEqual(config.environment["FOUNDRY_OPENAI_BASE_URL"], "https://foundry.example/openai/v1")
        self.assertEqual(config.environment["HERMES_MODEL"], "gpt-test")
        self.assertEqual(config.environment["OPENCLAW_MODEL_ID"], "gpt-test")
        self.assertEqual(config.environment["HERMES_BLUEPRINT_NAME"], "junior-project-manager")
        self.assertEqual(config.environment["HERMES_BLUEPRINT_COMMIT"], "a" * 40)
        self.assertEqual(config.environment["AUTOPILOT_INSTANCE_ID"], "worker-1")
        self.assertEqual(config.data_volume_name, "hermes-data")
        self.assertEqual(runtime_labels(config)["kind"], "hermes")
        self.assertEqual(runtime_labels(config)["blueprint"], "junior-project-manager")
        self.assertEqual(runtime_labels(config)["blueprintCommit"], "a" * 40)
        self.assertEqual(runtime_labels(config)["instance"], "worker-1")

    def test_environment_config_uses_bridge_registry_credentials(self):
        previous = {key: os.environ.get(key) for key in ["AGENT_RUNTIME_REGISTRY_USERNAME", "AGENT_RUNTIME_REGISTRY_PASSWORD"]}
        os.environ["AGENT_RUNTIME_REGISTRY_USERNAME"] = "registry-user"
        os.environ["AGENT_RUNTIME_REGISTRY_PASSWORD"] = "registry-pass"
        try:
            config = config_from_environment(
                runtime_kind="openclaw",
                subscription_id="sub-1",
                resource_group="rg-1",
                sandbox_group="sandbox-group-1",
                region="swedencentral",
                image_name="registry.example/openclaw-runtime@sha256:test",
                foundry_openai_base_url="https://foundry.example/openai/v1",
                gateway_token="token-1",
            )
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertEqual(config.registry_username, "registry-user")
        self.assertEqual(config.registry_password, "registry-pass")

    def test_hermes_environment_config_uses_runtime_volume_env(self):
        previous = os.environ.get("AGENT_RUNTIME_DATA_VOLUME_NAME")
        os.environ["AGENT_RUNTIME_DATA_VOLUME_NAME"] = "hermes-env-data"
        try:
            config = config_from_environment(
                runtime_kind="hermes",
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

    def test_existing_sandbox_reuse_does_not_require_registry_credentials(self):
        config = openclaw_sandbox_config(
            subscription_id="sub-1",
            resource_group="rg-1",
            sandbox_group="sandbox-group-1",
            region="swedencentral",
            image_name="",
            data_volume_name="openclaw-data",
            gateway_token="token-1",
        )

        class SandboxClient:
            def ensure_running(self, timeout):
                self.timeout = timeout

            def get(self):
                return SimpleNamespace(ports=[SimpleNamespace(port=18789, url="https://gateway.example")])

            def exec(self, command):
                return SimpleNamespace(exit_code=0, stdout="", stderr="")

        class Client:
            _group_path = "/groups/test"

            def _dp_get(self, path):
                return [
                    {
                        "id": "legacy-sandbox",
                        "labels": {"app": "openclaw-on-azure"},
                        "volumes": [{"volumeName": "openclaw-data"}],
                    },
                    {
                        "id": "sandbox-1",
                        "labels": {
                            "app": "autopilots-on-azure",
                            "kind": "openclaw",
                            "identityArchitecture": "agent-federation-v1",
                        },
                        "volumes": [{"volumeName": "openclaw-data"}],
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
        self.assertEqual(result.gateway_url, "https://gateway.example")
        self.assertTrue(result.reused_existing_sandbox)

    def test_hermes_response_text_parses_chat_completions(self):
        text = HermesRuntimeAdapter._response_text({"choices": [{"message": {"content": " hello from Hermes "}}]})

        self.assertEqual(text, "hello from Hermes")


class FakeHermesClient:
    def __init__(self, calls: list[dict], post_responses: list[tuple[int, dict]], **kwargs):
        self.calls = calls
        self.post_responses = post_responses
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, *, headers: dict | None = None):
        self.calls.append({"method": "GET", "url": url, "headers": headers or {}})
        payload = {"packetVersion": "1.0", "records": []} if url.endswith("/internal/learning/packet") else {"status": "ok"}
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    async def post(self, url: str, *, headers: dict, json: dict):
        self.calls.append({"method": "POST", "url": url, "headers": headers, "json": json})
        if url.endswith("/internal/learning/candidates"):
            return httpx.Response(
                200,
                json={"accepted": [{"recordId": "lr-test"}], "rejected": []},
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
