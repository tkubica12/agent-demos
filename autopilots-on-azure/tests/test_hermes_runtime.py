import os
import sys
import tempfile
import unittest
from pathlib import Path


RUNTIME_DIR = Path(__file__).resolve().parents[1] / "runtimes" / "hermes"
sys.path.insert(0, str(RUNTIME_DIR))

import start_hermes  # noqa: E402


class HermesRuntimeTests(unittest.TestCase):
    def test_requirements_include_streamable_http_mcp_sdk(self):
        requirements = (RUNTIME_DIR / "requirements.txt").read_text(encoding="utf-8")

        self.assertIn("mcp>=", requirements)

    def test_config_enables_api_server_and_private_mcp(self):
        previous = {
            key: os.environ.get(key)
            for key in [
                "API_SERVER_KEY",
                "PRIVATE_INCIDENTS_MCP_URL",
                "WORKIQ_MAIL_MCP_URL",
                "FOUNDRY_OPENAI_BASE_URL",
                "OPENAI_BASE_URL",
                "OPENAI_API_KEY",
                "HERMES_MODEL",
            ]
        }
        os.environ["API_SERVER_KEY"] = "api-key-1"
        os.environ["PRIVATE_INCIDENTS_MCP_URL"] = "http://mcp.example/mcp"
        os.environ["WORKIQ_MAIL_MCP_URL"] = "http://mail.example/mcp"
        os.environ["FOUNDRY_OPENAI_BASE_URL"] = "https://foundry.example/openai/v1"
        os.environ["HERMES_MODEL"] = "gpt-test"
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                home = Path(temp_dir)
                config = start_hermes.hermes_config(home)
                openai_base_url = os.environ["OPENAI_BASE_URL"]
                openai_api_key = os.environ["OPENAI_API_KEY"]
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
        self.assertNotIn("headers", config["mcp_servers"]["private-incidents"])
        self.assertEqual(config["mcp_servers"]["workiq-mail"]["url"], "http://mail.example/mcp")
        self.assertEqual(openai_base_url, "http://127.0.0.1:18080/v1")
        self.assertEqual(openai_api_key, "unused-managed-identity-token-proxy")
        self.assertEqual(config["model"]["provider"], "azure-foundry")


if __name__ == "__main__":
    unittest.main()
