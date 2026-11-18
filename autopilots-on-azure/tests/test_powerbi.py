import json
import unittest

from scripts.sandbox_runtime import hermes_sandbox_config


def runtime(**overrides):
    return hermes_sandbox_config(**{
        "subscription_id": "sub", "resource_group": "rg", "sandbox_group": "group",
        "region": "swedencentral", "image_name": "runtime", "agent365_tenant_id": "tenant",
        "agent365_blueprint_client_id": "blueprint", "agent365_agent_identity_client_id": "agent",
        **overrides,
    })


class PowerBIRuntimeTests(unittest.TestCase):
    def test_fabric_iq_goes_through_identity_proxy_as_agent_user(self):
        self.assertFalse(any(key.startswith("POWERBI_") for key in runtime().environment))
        fabric_iq = {"powerbi": {"upstreamUrl": "https://fabriciq.example/mcp", "scope": "powerbi/.default"}}
        environment = runtime(additional_agent_user_mcp_servers=fabric_iq, agent365_agent_user_id="user").environment
        self.assertEqual(environment["POWERBI_MCP_URL"], "http://127.0.0.1:18081/servers/powerbi")
        self.assertEqual(json.loads(environment["AGENT_MCP_SERVERS_JSON"])["powerbi"]["identityMode"], "agent_user")
        with self.assertRaises(ValueError):
            runtime(additional_agent_user_mcp_servers=fabric_iq)


if __name__ == "__main__":
    unittest.main()
