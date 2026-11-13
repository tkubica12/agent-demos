import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtimes" / "hermes"))

import start_hermes
import runtime_keepalive


class HermesBootstrapTests(unittest.TestCase):
    def test_autopilots_plugin_cannot_be_silently_disabled(self):
        with self.assertRaisesRegex(ValueError, "autopilots plugin must not be disabled"):
            start_hermes.hermes_config(Path("unused-runtime-home"), {"plugins": {"disabled": ["autopilots"]}})

    def test_autopilots_plugin_is_enabled_without_replacing_role_plugins(self):
        base = {"plugins": {"enabled": ["role-plugin", "keepalive", "autopilots-telemetry"], "disabled": ["unrelated"]}}
        with tempfile.TemporaryDirectory() as directory:
            config = start_hermes.hermes_config(Path(directory), base)
            repeated = start_hermes.hermes_config(Path(directory), config)
        self.assertEqual(config["plugins"]["enabled"], ["role-plugin", "autopilots"])
        self.assertEqual(repeated["plugins"], config["plugins"])
        self.assertEqual(base["plugins"]["enabled"], ["role-plugin", "keepalive", "autopilots-telemetry"])
        self.assertEqual(config["plugins"]["disabled"], ["unrelated"])
        self.assertEqual(config["plugins"]["auto_update_check_hours"], 0)

    def test_native_learning_and_unattended_housekeeping_settings(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"USER_SCHEDULING_ENABLED": "true"}, clear=True):
            config = start_hermes.hermes_config(Path(directory))
        self.assertEqual(config["memory"]["nudge_interval"], 10)
        self.assertEqual(config["skills"]["creation_nudge_interval"], 10)
        self.assertTrue(config["skills"]["ledger"])
        self.assertFalse(config["skills"]["write_approval"])
        self.assertTrue(config["auxiliary"]["background_review"]["enabled"])
        self.assertFalse(config["curator"]["enabled"])
        self.assertEqual(config["cron"], {"misfire_grace_minutes": 0, "provider": "azure", "mirror_delivery": False})
        self.assertFalse(config["updates"]["check"])
        self.assertFalse(config["security"]["allow_lazy_installs"])
        self.assertEqual(config["gateway"]["platforms"]["api_server"]["host"], "127.0.0.1")

    def test_disabled_scheduling_drops_a_persisted_service_bus_provider(self):
        persisted = {"cron": {"provider": "azure", "mirror_delivery": False, "misfire_grace_minutes": 5}}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"USER_SCHEDULING_ENABLED": "false"}, clear=True):
            config = start_hermes.hermes_config(Path(directory), persisted)
        self.assertEqual(config["cron"], {"misfire_grace_minutes": 0})
        self.assertEqual(persisted["cron"]["provider"], "azure")

    def test_image_owned_plugins_replace_retired_and_stale_plugin_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "image" / "autopilots"
            source.mkdir(parents=True)
            (source / "plugin.yaml").write_text("name: autopilots\n", encoding="utf-8")
            profile = root / "profile"
            for retired in ("keepalive", "autopilots-telemetry"):
                (profile / "plugins" / retired).mkdir(parents=True)
            (profile / "plugins" / "autopilots").mkdir()
            (profile / "plugins" / "autopilots" / "stale.py").write_text("", encoding="utf-8")
            start_hermes.install_runtime_plugins(profile, root / "image")
            self.assertEqual(sorted(path.name for path in (profile / "plugins").iterdir()), ["autopilots"])
            self.assertEqual(sorted(path.name for path in (profile / "plugins" / "autopilots").iterdir()), ["plugin.yaml"])

    def test_profile_ownership_walks_only_launcher_written_paths_after_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            for name in ("plugins/autopilots", ".telemetry/generation", "workspace", "sessions"):
                (home / name).mkdir(parents=True)
            (home / "config.yaml").write_text("{}", encoding="utf-8")
            (home / "sessions" / "big.jsonl").write_text("", encoding="utf-8")
            owner = home.stat().st_uid
            chowned = []
            with patch.object(start_hermes.os, "chown", lambda path, uid, gid: chowned.append(Path(path)), create=True):
                start_hermes.own_profile(home, owner, owner)
            self.assertIn(home / ".telemetry" / "generation", chowned)
            self.assertIn(home / "plugins" / "autopilots", chowned)
            self.assertNotIn(home / "sessions" / "big.jsonl", chowned)
            chowned.clear()
            with patch.object(start_hermes.os, "chown", lambda path, uid, gid: chowned.append(Path(path)), create=True):
                start_hermes.own_profile(home, owner + 1, owner + 1)
            self.assertIn(home / "sessions" / "big.jsonl", chowned)

    def test_hermes_starts_when_loopback_adapter_accepts_connections(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        try:
            start_hermes.wait_for_loopback(Mock(poll=Mock(return_value=None)), listener.getsockname()[1], timeout=2)
        finally:
            listener.close()
        with self.assertRaisesRegex(RuntimeError, "exited with code 3"):
            start_hermes.wait_for_loopback(Mock(poll=Mock(return_value=3), returncode=3), 9, timeout=2)

    def test_health_never_reports_ready_without_a_running_gateway(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            for gateway in (None, Mock(poll=Mock(return_value=1))):
                with self.subTest(gateway=gateway):
                    client = TestClient(start_hermes.create_health_app(home, home, None, gateway))
                    response = client.get("/health")
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(response.json()["status"], "gateway-unavailable")

    def test_public_runtime_health_is_minimal_and_detailed_health_requires_operations_key(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"API_SERVER_KEY": "operations-key"}):
            home = Path(directory)
            client = TestClient(start_hermes.create_health_app(
                home, home, None, Mock(pid=123, poll=Mock(return_value=None)),
            ))
            public = client.get("/health")
            self.assertEqual(public.status_code, 200)
            self.assertEqual(set(public.json()), {"status", "release"})
            for headers in ({}, {"X-Autopilot-Key": "wrong-key"}):
                self.assertEqual(client.get("/health/detailed", headers=headers).status_code, 401)
            detailed = client.get("/health/detailed", headers={"X-Autopilot-Key": "operations-key"})
            self.assertEqual(detailed.status_code, 200)
            self.assertEqual(detailed.json()["profileHome"], str(home))
            self.assertEqual(detailed.json()["gatewayPid"], 123)
            self.assertIn("backup", detailed.json())

    def test_runtime_requests_keep_deterministic_work_alive_but_health_does_not(self):
        supervisor = runtime_keepalive.Keepalive(
            "https://sandbox.example/health", send=Mock(return_value=200),
        )
        self.addCleanup(supervisor.close)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"API_SERVER_KEY": "test-key"}),
            patch.object(start_hermes, "get_keepalive", return_value=supervisor),
            patch.object(supervisor, "_run"),
        ):
            home = Path(directory)
            app = start_hermes.create_health_app(
                home, home, None, Mock(pid=123, poll=Mock(return_value=None)),
            )
            client = TestClient(app)
            client.get("/health")
            self.assertEqual(supervisor.tick(), 0)
            supervisor._send.assert_not_called()

            def claim(*args, **kwargs):
                self.assertEqual(supervisor.tick(), 1)
                return {"claimed": True}

            with patch.object(start_hermes, "claim_interaction", side_effect=claim):
                response = client.post(
                    "/internal/interactions/claim",
                    json={"interactionId": "i-1", "choiceId": "c-1", "expiresAtUnix": 4102444800},
                    headers={"X-Autopilot-Key": "test-key"},
                )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(supervisor.tick(), 0)

    def test_native_foundry_auth_replaces_persisted_proxy_configuration(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "FOUNDRY_OPENAI_BASE_URL": "https://example.services.ai.azure.com/openai/v1/",
                "HERMES_MODEL": "gpt-6-1-sol",
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
            self.assertEqual(model["default"], "gpt-6-1-sol")
            self.assertEqual(model["api_mode"], "responses")
            self.assertNotIn("name", model)
            self.assertEqual(model["base_url"], "https://example.services.ai.azure.com/openai/v1")
            self.assertEqual(model["entra"]["scope"], "https://ai.azure.com/.default")
            self.assertNotIn("OPENAI_API_KEY", os.environ)
            self.assertNotIn("AZURE_FOUNDRY_API_KEY", os.environ)
            self.assertNotIn("obsolete-placeholder", (home / ".env").read_text(encoding="utf-8"))
            self.assertEqual(config["memory"]["nudge_interval"], 10)
            self.assertEqual(config["skills"]["creation_nudge_interval"], 10)
            self.assertFalse(config["curator"]["enabled"])


if __name__ == "__main__":
    unittest.main()
