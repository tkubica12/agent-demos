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


class RoleMcpRuntimeTests(unittest.TestCase):
    def test_role_servers_go_through_identity_proxy_as_the_agent(self):
        server = {"pm-tools": {"upstreamUrl": "https://pm.example/mcp", "scope": "api://pm/.default"}}
        environment = runtime(role_mcp_servers=server).environment
        self.assertEqual(
            json.loads(environment["ROLE_MCP_SERVERS_JSON"]), {"pm-tools": "http://127.0.0.1:18081/servers/pm-tools"},
        )
        self.assertEqual(json.loads(environment["AGENT_MCP_SERVERS_JSON"])["pm-tools"]["identityMode"], "agent")
        server["pm-tools"]["agentUserTokenHeaders"] = {"x-agent-user-ado-token": "ado/.default"}
        with self.assertRaises(ValueError):
            runtime(role_mcp_servers=server)
        runtime(role_mcp_servers=server, agent365_agent_user_id="user")


if __name__ == "__main__":
    unittest.main()
