import asyncio
import inspect
import os
import unittest
from types import SimpleNamespace

import bridge.app as bridge_app
import bridge.runtime.factory as runtime_factory
import bridge.runtime.openclaw as openclaw_runtime
import scripts.sandbox_runtime as sandbox_runtime
from bridge.gateway_client import OpenClawGatewayError
from bridge.runtime.base import AgentRequest
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

    def test_runtime_factory_rejects_unsupported_runtime_until_later_milestones(self):
        previous = os.environ.get("AGENT_RUNTIME")
        os.environ["AGENT_RUNTIME"] = "hermes"
        try:
            with self.assertRaisesRegex(ValueError, "Hermes support starts in later milestones"):
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

    def test_openclaw_sandbox_config_preserves_gateway_defaults(self):
        config = openclaw_sandbox_config(
            image_name="registry.example/openclaw-runtime@sha256:test",
            gateway_token="token-1",
            foundry_openai_base_url="https://foundry.example/openai/v1",
            model_deployment="gpt-test",
            private_incidents_mcp_url="https://mcp.example/mcp",
        )

        self.assertEqual(config.runtime_kind, "openclaw")
        self.assertEqual(config.port, 18789)
        self.assertEqual(config.command, ("python3",))
        self.assertEqual(config.args, ("-m", "openclaw_gateway.start_gateway"))
        self.assertEqual(config.data_mount_path, "/data")
        self.assertEqual(config.environment["OPENCLAW_GATEWAY_TOKEN"], "token-1")
        self.assertEqual(config.environment["PRIVATE_INCIDENTS_MCP_URL"], "https://mcp.example/mcp")
        self.assertNotIn("runtime", runtime_labels(config))
        self.assertNotIn("autopilot", runtime_labels(config))
        self.assertEqual(runtime_labels(config)["kind"], "openclaw")

    def test_hermes_sandbox_config_can_be_built_without_starting_runtime(self):
        config = hermes_sandbox_config(
            image_name="registry.example/hermes-runtime@sha256:test",
            api_server_key="api-key-1",
            private_incidents_mcp_url="https://mcp.example/mcp",
        )

        self.assertEqual(config.runtime_kind, "hermes")
        self.assertEqual(config.port, 8642)
        self.assertEqual(config.health_path, "/health")
        self.assertEqual(config.command, ("python3",))
        self.assertEqual(config.args, ("start_hermes.py",))
        self.assertEqual(config.environment["API_SERVER_ENABLED"], "true")
        self.assertEqual(config.environment["API_SERVER_HOST"], "0.0.0.0")
        self.assertEqual(config.environment["API_SERVER_PORT"], "8642")
        self.assertEqual(config.environment["API_SERVER_KEY"], "api-key-1")
        self.assertEqual(config.environment["HERMES_HOME"], "/data/hermes")
        self.assertEqual(config.data_volume_name, "hermes-data")
        self.assertEqual(runtime_labels(config)["kind"], "hermes")

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
                        "labels": {"app": "autopilots-on-azure", "kind": "openclaw"},
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

if __name__ == "__main__":
    unittest.main()
