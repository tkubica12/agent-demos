from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from autopilots_identity import mcp_proxy
from autopilots_identity.mcp_proxy import (
    McpServerConfig,
    forwarded_request_headers,
    load_server_configs,
    upstream_url,
)


class IdentityMcpProxyTests(unittest.TestCase):
    def test_loads_agent_and_agent_user_servers(self) -> None:
        servers = load_server_configs(
            """
            {
              "private": {
                "upstreamUrl": "https://private.example/mcp",
                "scope": "api://private/.default",
                "identityMode": "agent"
              },
              "mail": {
                "upstreamUrl": "https://mail.example/mcp",
                "scope": "mail-resource/Tools.ListInvoke.All",
                "identityMode": "agent_user"
              }
            }
            """
        )

        self.assertEqual(servers["private"].identity_mode, "agent")
        self.assertEqual(servers["mail"].identity_mode, "agent_user")

    def test_rejects_non_https_upstream(self) -> None:
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            load_server_configs(
                '{"private":{"upstreamUrl":"http://private.example/mcp","scope":"api://private/.default"}}'
            )

    def test_builds_upstream_url_and_replaces_authorization(self) -> None:
        config = McpServerConfig(
            upstream_url="https://private.example/mcp",
            scope="api://private/.default",
            identity_mode="agent",
        )
        request = SimpleNamespace(
            headers={
                "authorization": "old",
                "content-type": "application/json",
                "mcp-session-id": "session-1",
                "host": "localhost",
            }
        )

        headers = forwarded_request_headers(request, "agent-token")

        self.assertEqual(upstream_url(config, "events", "page=2"), "https://private.example/mcp/events?page=2")
        self.assertEqual(headers["Authorization"], "Bearer agent-token")
        self.assertEqual(headers["mcp-session-id"], "session-1")
        self.assertNotIn("host", headers)

    def test_startup_prefetches_each_distinct_token_without_failing_the_proxy(self) -> None:
        servers = load_server_configs(
            """
            {
              "word": {"upstreamUrl": "https://word.example/mcp", "scope": "m365/.default", "identityMode": "agent_user"},
              "mail": {"upstreamUrl": "https://mail.example/mcp", "scope": "m365/.default", "identityMode": "agent_user"},
              "private": {"upstreamUrl": "https://private.example/mcp", "scope": "api://private/.default"}
            }
            """
        )
        fetch = AsyncMock(side_effect=["user-token", RuntimeError("entra unavailable")])
        with (
            patch.object(mcp_proxy, "server_configs", return_value=servers),
            patch.object(mcp_proxy, "access_token", fetch),
            self.assertLogs(mcp_proxy.logger, level="WARNING") as logs,
        ):
            asyncio.run(mcp_proxy.prefetch_tokens())
        self.assertEqual([call.args[0].scope for call in fetch.await_args_list], ["m365/.default", "api://private/.default"])
        self.assertIn("entra unavailable", logs.output[0])


if __name__ == "__main__":
    unittest.main()
