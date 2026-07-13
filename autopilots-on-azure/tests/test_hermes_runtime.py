import os
import subprocess
import sys
import tempfile
import unittest
import json
import shutil
from pathlib import Path


RUNTIME_DIR = Path(__file__).resolve().parents[1] / "runtimes" / "hermes"
sys.path.insert(0, str(RUNTIME_DIR))

import start_hermes  # noqa: E402
from blueprint import BlueprintSettings, install_or_update_blueprint, settings_from_environment  # noqa: E402


class HermesRuntimeTests(unittest.TestCase):
    @staticmethod
    def _git(repo: Path, *args: str) -> str:
        result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
        return result.stdout.strip()

    def _commit_blueprint(
        self,
        repo: Path,
        version: str,
        marker: str,
        extra_owned: tuple[str, ...] = (),
    ) -> str:
        distribution = repo / "blueprints" / "junior-project-manager"
        skill = distribution / "skills" / "junior-project-manager"
        skill.mkdir(parents=True, exist_ok=True)
        manifest_lines = [
            "name: junior-project-manager",
            f"version: {version}",
            "distribution_owned:",
            "  - SOUL.md",
            "  - config.yaml",
            "  - skills/junior-project-manager",
            *[f"  - {path}" for path in extra_owned],
            "  - distribution.yaml",
            "",
        ]
        (distribution / "distribution.yaml").write_text("\n".join(manifest_lines), encoding="utf-8")
        (distribution / "SOUL.md").write_text(f"# {marker}\n", encoding="utf-8")
        (distribution / "config.yaml").write_text("custom:\n  blueprintDefault: true\n", encoding="utf-8")
        (skill / "SKILL.md").write_text(f"# {marker} skill\n", encoding="utf-8")
        for relative in extra_owned:
            owned_path = distribution / relative
            owned_path.mkdir(parents=True, exist_ok=True)
            (owned_path / "owned.txt").write_text(f"{marker}\n", encoding="utf-8")
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-m", version)
        return self._git(repo, "rev-parse", "HEAD")

    def test_blueprint_upgrade_preserves_instance_private_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "source"
            home = root / "hermes"
            repo.mkdir()
            self._git(repo, "init")
            self._git(repo, "config", "user.email", "test@example.com")
            self._git(repo, "config", "user.name", "Hermes Test")
            commit_v1 = self._commit_blueprint(repo, "1.0.0", "v1", ("legacy",))
            settings_v1 = BlueprintSettings(
                name="junior-project-manager",
                source=str(repo),
                repository_path="blueprints/junior-project-manager",
                version="1.0.0",
                commit=commit_v1,
                instance_id="worker-1",
                assignee_scope="team-alpha",
            )

            first = install_or_update_blueprint(home, settings_v1)
            profile = first.profile_home
            (profile / "memories" / "MEMORY.md").write_text("private memory\n", encoding="utf-8")
            (profile / "sessions" / "session.json").write_text('{"private":true}\n', encoding="utf-8")
            (profile / "state.db").write_bytes(b"private sqlite state")
            custom_skill = profile / "skills" / "instance-local" / "SKILL.md"
            custom_skill.parent.mkdir(parents=True)
            custom_skill.write_text("# local skill\n", encoding="utf-8")

            commit_v2 = self._commit_blueprint(repo, "2.0.0", "v2")
            settings_v2 = BlueprintSettings(
                name=settings_v1.name,
                source=settings_v1.source,
                repository_path=settings_v1.repository_path,
                version="2.0.0",
                commit=commit_v2,
                instance_id=settings_v1.instance_id,
                assignee_scope=settings_v1.assignee_scope,
            )
            second = install_or_update_blueprint(home, settings_v2)

            self.assertTrue(second.changed)
            self.assertEqual((profile / "SOUL.md").read_text(encoding="utf-8"), "# v2\n")
            self.assertFalse((profile / "legacy").exists())
            self.assertEqual((profile / "memories" / "MEMORY.md").read_text(encoding="utf-8"), "private memory\n")
            self.assertTrue((profile / "sessions" / "session.json").exists())
            self.assertEqual((profile / "state.db").read_bytes(), b"private sqlite state")
            self.assertEqual(custom_skill.read_text(encoding="utf-8"), "# local skill\n")
            manifest = json.loads((profile / "local" / "autopilots-instance.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["blueprintVersion"], "2.0.0")
            self.assertEqual(manifest["blueprintCommit"], commit_v2)
            self.assertEqual(manifest["instanceId"], "worker-1")

    def test_matching_pinned_blueprint_reuses_installed_profile_without_git(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "source"
            home = root / "hermes"
            repo.mkdir()
            self._git(repo, "init")
            self._git(repo, "config", "user.email", "test@example.com")
            self._git(repo, "config", "user.name", "Hermes Test")
            commit = self._commit_blueprint(repo, "1.0.0", "v1")
            settings = BlueprintSettings(
                name="junior-project-manager",
                source=str(repo),
                repository_path="blueprints/junior-project-manager",
                version="1.0.0",
                commit=commit,
                instance_id="worker-1",
                assignee_scope="team-alpha",
            )
            install_or_update_blueprint(home, settings)
            for child in repo.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()

            reused = install_or_update_blueprint(home, settings)

            self.assertFalse(reused.changed)
            self.assertEqual(reused.manifest["blueprintCommit"], commit)

    def test_runtime_env_update_preserves_instance_values(self):
        previous = {key: os.environ.get(key) for key in ["API_SERVER_KEY", "HERMES_MODEL"]}
        os.environ["API_SERVER_KEY"] = "api-key-1"
        os.environ["HERMES_MODEL"] = "gpt-test"
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                home = Path(temp_dir)
                (home / ".env").write_text("PRIVATE_PREFERENCE=keep\nAPI_SERVER_KEY=old\n", encoding="utf-8")

                start_hermes.write_env_file(home)
                contents = (home / ".env").read_text(encoding="utf-8")
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertIn("PRIVATE_PREFERENCE=keep", contents)
        self.assertIn("API_SERVER_KEY=api-key-1", contents)
        self.assertNotIn("API_SERVER_KEY=old", contents)

    def test_activate_profile_writes_hermes_sticky_profile(self):
        previous = os.environ.get("HERMES_PROFILE")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                home = Path(temp_dir)
                active_path = start_hermes.activate_profile(home, "junior-project-manager")
                contents = active_path.read_text(encoding="utf-8")
        finally:
            if previous is None:
                os.environ.pop("HERMES_PROFILE", None)
            else:
                os.environ["HERMES_PROFILE"] = previous

        self.assertEqual(contents, "junior-project-manager\n")

    def test_gateway_process_uses_active_profile_as_hermes_home(self):
        calls = {}

        class Process:
            pass

        def popen(command, env):
            calls["command"] = command
            calls["env"] = env
            return Process()

        original = start_hermes.subprocess.Popen
        start_hermes.subprocess.Popen = popen
        try:
            profile_home = Path("/data/hermes/profiles/junior-project-manager")
            start_hermes.start_gateway(profile_home)
        finally:
            start_hermes.subprocess.Popen = original

        self.assertEqual(calls["command"], ["hermes", "gateway", "run", "--accept-hooks"])
        self.assertEqual(calls["env"]["HERMES_HOME"], str(profile_home))

    def test_blueprint_source_rejects_embedded_credentials(self):
        keys = ["HERMES_BLUEPRINT_SOURCE", "HERMES_BLUEPRINT_NAME", "HERMES_BLUEPRINT_COMMIT"]
        previous = {key: os.environ.get(key) for key in keys}
        os.environ["HERMES_BLUEPRINT_SOURCE"] = "https://token@example.com/blueprint.git"
        os.environ["HERMES_BLUEPRINT_NAME"] = "junior-project-manager"
        os.environ["HERMES_BLUEPRINT_COMMIT"] = "a" * 40
        try:
            with self.assertRaisesRegex(ValueError, "embedded credentials"):
                settings_from_environment()
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

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
