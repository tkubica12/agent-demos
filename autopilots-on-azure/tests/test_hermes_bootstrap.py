import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtimes" / "hermes"))

import start_hermes


class HermesBootstrapTests(unittest.TestCase):
    def test_native_telemetry_plugin_cannot_be_silently_disabled(self):
        with self.assertRaisesRegex(ValueError, "telemetry plugin must not be disabled"):
            start_hermes.hermes_config(
                Path("unused-runtime-home"),
                {"plugins": {"disabled": ["autopilots-telemetry"]}},
            )

    def test_native_telemetry_plugin_is_enabled_without_replacing_role_plugins(self):
        base = {"plugins": {"enabled": ["azure"], "disabled": ["unrelated"]}}
        with tempfile.TemporaryDirectory() as directory:
            config = start_hermes.hermes_config(Path(directory), base)
            repeated = start_hermes.hermes_config(Path(directory), config)
        self.assertEqual(config["plugins"]["enabled"], ["azure", "autopilots-telemetry"])
        self.assertEqual(repeated["plugins"], config["plugins"])
        self.assertEqual(base["plugins"]["enabled"], ["azure"])
        self.assertEqual(config["plugins"]["disabled"], ["unrelated"])

    def test_health_never_reports_ready_without_a_running_gateway(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            for gateway in (None, Mock(poll=Mock(return_value=1))):
                with self.subTest(gateway=gateway):
                    client = TestClient(start_hermes.create_health_app(home, home, None, gateway))
                    response = client.get("/health")
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(response.json()["status"], "gateway-unavailable")

    def test_native_foundry_auth_replaces_persisted_proxy_configuration(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "FOUNDRY_OPENAI_BASE_URL": "https://example.services.ai.azure.com/openai/v1/",
                "HERMES_MODEL": "gpt-5-6-terra",
                "OPENAI_BASE_URL": "http://127.0.0.1:18080/v1",
                "OPENAI_API_KEY": "obsolete-placeholder",
                "AZURE_FOUNDRY_API_KEY": "obsolete-placeholder",
            },
            clear=True,
        ):
            home = Path(directory)
            (home / ".env").write_text(
                "OPENAI_API_KEY=obsolete-placeholder\nAZURE_FOUNDRY_API_KEY=obsolete-placeholder\n",
                encoding="utf-8",
            )
            config = start_hermes.hermes_config(
                home,
                {
                    "model": {"name": "old", "auth_mode": "api_key"},
                    "memory": {"nudge_interval": 10},
                    "skills": {"creation_nudge_interval": 10},
                    "curator": {"enabled": True},
                },
            )
            start_hermes.write_env_file(home)
            model = config["model"]
            self.assertEqual(model["auth_mode"], "entra_id")
            self.assertEqual(model["default"], "gpt-5-6-terra")
            self.assertNotIn("name", model)
            self.assertEqual(model["base_url"], "https://example.services.ai.azure.com/openai/v1")
            self.assertEqual(model["entra"]["scope"], "https://cognitiveservices.azure.com/.default")
            self.assertNotIn("OPENAI_API_KEY", os.environ)
            self.assertNotIn("AZURE_FOUNDRY_API_KEY", os.environ)
            self.assertNotIn("obsolete-placeholder", (home / ".env").read_text(encoding="utf-8"))
            self.assertEqual(config["memory"]["nudge_interval"], 0)
            self.assertEqual(config["skills"]["creation_nudge_interval"], 0)
            self.assertFalse(config["curator"]["enabled"])


if __name__ == "__main__":
    unittest.main()
