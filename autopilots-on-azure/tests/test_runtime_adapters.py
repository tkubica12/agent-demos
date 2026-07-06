import asyncio
import inspect
import os
import unittest
from types import SimpleNamespace

import bridge.app as bridge_app
import bridge.runtime.factory as runtime_factory
import bridge.runtime.openclaw as openclaw_runtime
from bridge.runtime.base import AgentRequest
from bridge.runtime.openclaw import OpenClawRuntimeAdapter
from scripts.sandbox_runtime import GatewaySandboxConfig


def sandbox_config() -> GatewaySandboxConfig:
    return GatewaySandboxConfig(
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


if __name__ == "__main__":
    unittest.main()
