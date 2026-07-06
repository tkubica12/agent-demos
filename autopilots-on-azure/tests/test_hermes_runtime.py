import os
import sys
import tempfile
import unittest
from pathlib import Path


RUNTIME_DIR = Path(__file__).resolve().parents[1] / "runtimes" / "hermes"
sys.path.insert(0, str(RUNTIME_DIR))

import start_hermes  # noqa: E402


class HermesRuntimeTests(unittest.TestCase):
    def test_config_enables_api_server_and_private_mcp(self):
        previous = {key: os.environ.get(key) for key in ["API_SERVER_KEY", "PRIVATE_INCIDENTS_MCP_URL", "PRIVATE_INCIDENTS_MCP_STATIC_KEY"]}
        os.environ["API_SERVER_KEY"] = "api-key-1"
        os.environ["PRIVATE_INCIDENTS_MCP_URL"] = "http://mcp.example/mcp"
        os.environ["PRIVATE_INCIDENTS_MCP_STATIC_KEY"] = "mcp-key-1"
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                home = Path(temp_dir)
                config = start_hermes.hermes_config(home)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertTrue(config["gateway"]["platforms"]["api_server"]["enabled"])
        self.assertEqual(config["gateway"]["platforms"]["api_server"]["host"], "0.0.0.0")
        self.assertEqual(config["gateway"]["platforms"]["api_server"]["port"], 8642)
        self.assertEqual(config["gateway"]["platforms"]["api_server"]["api_key"], "api-key-1")
        self.assertEqual(config["mcp_servers"]["private-incidents"]["url"], "http://mcp.example/mcp")
        self.assertEqual(config["mcp_servers"]["private-incidents"]["headers"]["Authorization"], "Bearer mcp-key-1")


if __name__ == "__main__":
    unittest.main()
