import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from aiohttp import web
from aiohttp.test_utils import TestClient as AiohttpTestClient, TestServer
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider

from bridge import hermes_telemetry, telemetry


RUNTIME_DIR = Path(__file__).resolve().parents[1] / "runtimes" / "hermes"
sys.path.insert(0, str(RUNTIME_DIR))

import start_hermes  # noqa: E402
from blueprint import (  # noqa: E402
    RoleReleaseSettings,
    install_or_refresh_role_release,
    role_release_settings_from_environment,
)


class HermesRuntimeTests(unittest.TestCase):
    def test_gateway_trace_lease_cleans_success_and_blocks_ambiguous_retries(self):
        client_class = httpx.AsyncClient
        provider = TracerProvider()
        self.addCleanup(provider.shutdown)
        with patch.object(
            telemetry.trace, "get_tracer", return_value=provider.get_tracer("test-runtime")
        ):
            for outcome in ("success", "server-error", "timeout"):
                with (
                    self.subTest(outcome=outcome),
                    tempfile.TemporaryDirectory() as directory,
                    patch.dict(os.environ, {"API_SERVER_KEY": "test-key"}),
                ):
                    home = Path(directory)
                    handoff_directory = hermes_telemetry.install_gateway_telemetry(home)
                    calls = []

                    def handle(request):
                        if request.url.path.endswith("/messages"):
                            return httpx.Response(200, json={"session_id": "native-session"})
                        calls.append(request)
                        handoff = hermes_telemetry._handoff("native-session", "")
                        if request.method == "POST":
                            self.assertEqual(handoff["traceparent"], request.headers["traceparent"])
                        self.assertEqual(len(list(handoff_directory.glob("*.lease"))), 1)
                        if outcome == "timeout":
                            raise httpx.ReadTimeout("Native executor may still run.", request=request)
                        return httpx.Response(
                            (202 if request.method == "POST" else 200) if outcome == "success" else 503,
                            json={"run_id": "native-run", "status": "queued" if request.method == "POST" else "completed"}
                            if outcome == "success" else {"error": "uncertain"},
                        )

                    with patch.object(
                        start_hermes.httpx,
                        "AsyncClient",
                        side_effect=lambda **kwargs: client_class(
                            transport=httpx.MockTransport(handle), **kwargs
                        ),
                    ):
                        client = TestClient(
                            start_hermes.create_health_app(home, home, None, None),
                            raise_server_exceptions=False,
                        )
                        response = client.post(
                            "/v1/runs", json={"input": "hello", "session_id": "native-session"},
                            headers={"Authorization": "Bearer test-key", "Idempotency-Key": "first"},
                        )
                        if outcome == "success":
                            self.assertEqual(len(list(handoff_directory.glob("*.lease"))), 1)
                            client.get("/v1/runs/native-run")
                        repeated = client.post(
                            "/v1/runs", json={"input": "hello", "session_id": "native-session"},
                            headers={"Authorization": "Bearer test-key", "Idempotency-Key": "second"},
                        )
                        if outcome == "success":
                            client.get("/v1/runs/native-run")

                    if outcome == "success":
                        self.assertEqual(response.status_code, 202)
                        self.assertEqual(repeated.status_code, 202)
                        self.assertEqual(len(calls), 4)
                        self.assertEqual(list(handoff_directory.iterdir()), [])
                    else:
                        self.assertEqual(response.status_code, 503 if outcome == "server-error" else 500)
                        if outcome == "server-error":
                            self.assertEqual(response.json(), {"error": "uncertain"})
                        self.assertEqual(repeated.status_code, 409)
                        self.assertIn("different admission", repeated.json()["detail"])
                        self.assertEqual(len(calls), 1)
                        self.assertEqual(len(list(handoff_directory.glob("*.lease"))), 1)

    def test_gateway_trace_handoff_spans_only_the_awaited_native_session_turn(self):
        events = []
        active = False

        @contextmanager
        def handoff(session_id, *, profile_home, admission_id, ttl_seconds):
            nonlocal active
            self.assertEqual(profile_home, home)
            self.assertEqual(session_id, "teams:shared:thread")
            active = True
            events.append("enter")
            try:
                yield
            finally:
                active = False
                events.append("exit")

        async def handle(request):
            if request.url.path.endswith("/messages"):
                return httpx.Response(200, json={"session_id": "teams:shared:thread"})
            self.assertEqual(active, request.url.path.startswith("/v1/runs"))
            events.append(request.method)
            return httpx.Response(
                202 if request.method == "POST" else 200,
                json={"run_id": "native-run", "status": "completed"},
            )

        client_class = httpx.AsyncClient
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"API_SERVER_KEY": "test-key"}),
            patch.object(start_hermes, "gateway_trace_context", side_effect=handoff) as context,
            patch.object(
                start_hermes.httpx,
                "AsyncClient",
                side_effect=lambda **kwargs: client_class(
                    transport=httpx.MockTransport(handle), **kwargs
                ),
            ),
        ):
            home = Path(directory)
            client = TestClient(start_hermes.create_health_app(home, home, None, None))
            self.assertEqual(client.get("/api/sessions/teams%3Ashared%3Athread").status_code, 200)
            response = client.post(
                "/v1/runs", headers={"Authorization": "Bearer test-key", "Idempotency-Key": "native-admission"},
                json={"input": "hello", "instructions": "role", "session_id": "teams:shared:thread"},
            )
            client.get("/v1/runs/native-run")

        self.assertEqual(response.status_code, 202)
        context.assert_called_once_with(
            "teams:shared:thread", profile_home=home, admission_id="native-admission", ttl_seconds=3600,
        )
        self.assertEqual(events, ["GET", "enter", "POST", "GET", "exit"])

    def test_gateway_proxy_uses_current_runtime_trace_context(self):
        requests = []
        fresh_headers = {
            "traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01",
            "x-autopilot-otel-session": "3" * 32,
        }

        def handle(request):
            requests.append(request)
            return httpx.Response(200, json={"data": []})

        client_class = httpx.AsyncClient
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(start_hermes, "runtime_trace_headers", return_value=fresh_headers),
            patch.object(
                start_hermes.httpx,
                "AsyncClient",
                side_effect=lambda **kwargs: client_class(
                    transport=httpx.MockTransport(handle), **kwargs
                ),
            ),
        ):
            home = Path(directory)
            client = TestClient(start_hermes.create_health_app(home, home, None, None))
            response = client.get(
                "/v1/models",
                headers={
                    "Authorization": "Bearer api-key",
                    "traceparent": "00-" + "4" * 32 + "-" + "5" * 16 + "-01",
                    "tracestate": "stale=value",
                    "baggage": "private=must-not-forward",
                    "x-autopilot-otel-operation": "6" * 32,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(requests), 1)
        outgoing = requests[0]
        self.assertEqual(outgoing.headers["Authorization"], "Bearer api-key")
        for name, value in fresh_headers.items():
            self.assertEqual(outgoing.headers[name], value)
        for removed in ("tracestate", "baggage", "x-autopilot-otel-operation"):
            self.assertNotIn(removed, outgoing.headers)

    def test_gateway_starts_through_official_shim_as_unprivileged_account(self):
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(start_hermes.subprocess, "Popen") as popen,
        ):
            profile = Path(temp_dir)
            start_hermes.start_gateway(profile, user=10000, group=10000, extra_groups=[])

        popen.assert_called_once()
        self.assertEqual(
            popen.call_args.args[0],
            ["/opt/hermes/bin/hermes", "gateway", "run", "--replace", "--accept-hooks"],
        )
        self.assertEqual(popen.call_args.kwargs["user"], 10000)
        self.assertEqual(popen.call_args.kwargs["extra_groups"], [])
        self.assertEqual(popen.call_args.kwargs["env"]["HERMES_HOME"], str(profile))
        self.assertEqual(popen.call_args.kwargs["env"]["HOME"], str(profile / "home"))

    def test_native_api_routes_keep_cron_contract_and_argument_mapping(self):
        import runtime_routes

        calls = {}

        def recorder(name, result):
            def call(profile_home, **kwargs):
                calls[name] = kwargs
                return result
            return call

        async def exercise(home: Path) -> list[tuple[int, dict]]:
            app = web.Application()
            runtime_routes.register_routes(app, home)
            async with AiohttpTestClient(TestServer(app)) as client:
                headers = {"X-Autopilot-Key": "operator-key"}
                requests = [
                    ("/internal/cron/fire", {"jobId": "job-1", "revision": "revision-1", "deliveryActivityId": "x"}),
                    ("/internal/cron/ack-delivery", {"jobId": "job-1", "revision": "revision-1", "deliveryActivityId": "activity-1"}),
                    ("/internal/cron/system/claim", {"jobId": "dream-job", "revision": "dream-revision", "occurrenceId": "manual-1"}),
                    ("/internal/cron/system/complete", {"jobId": "dream-job", "revision": "dream-revision", "occurrenceId": "manual-1",
                                                        "success": True, "summary": {"dream": {"recordCount": 1}}, "ownerToken": "claim-owner"}),
                    ("/internal/cron/system/checkpoint", {"jobId": "dream-job", "revision": "r", "occurrenceId": "o", "payload": {}}),
                ]
                responses = []
                for path, body in requests:
                    response = await client.post(path, headers=headers, json=body)
                    responses.append((response.status, await response.json()))
                unauthorized = await client.post("/internal/cron/fire", json={})
                responses.append((unauthorized.status, await unauthorized.json()))
                bad = await client.post("/internal/cron/bind-local", headers=headers, json={"jobIds": "not-a-list"})
                responses.append((bad.status, await bad.json()))
                return responses

        def conflicting_checkpoint(profile_home, **kwargs):
            raise RuntimeError("stale owner")

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.dict(os.environ, {"API_SERVER_KEY": "operator-key"}),
            patch.object(runtime_routes, "fire_cron_job", recorder("fire", {"status": "completed"})),
            patch.object(runtime_routes, "acknowledge_cron_delivery", recorder("ack", {"status": "delivered"})),
            patch.object(runtime_routes, "claim_system_schedule", recorder("claim", {"status": "claimed"})),
            patch.object(runtime_routes, "complete_system_schedule", recorder("complete", {"status": "completed"})),
            patch.object(runtime_routes, "checkpoint_system_schedule", conflicting_checkpoint),
        ):
            import asyncio
            responses = asyncio.run(exercise(Path(temp_dir)))

        self.assertEqual([status for status, _ in responses], [200, 200, 200, 200, 409, 401, 400])
        self.assertEqual(responses[4][1], {"detail": "stale owner"})
        self.assertEqual(calls["fire"], {"job_id": "job-1", "revision": "revision-1"})
        self.assertEqual(calls["ack"], {"job_id": "job-1", "revision": "revision-1", "delivery_activity_id": "activity-1"})
        self.assertEqual(calls["claim"], {"job_id": "dream-job", "revision": "dream-revision", "occurrence_id": "manual-1"})
        self.assertEqual(calls["complete"], {
            "job_id": "dream-job", "revision": "dream-revision", "occurrence_id": "manual-1", "success": True,
            "error": "", "summary": {"dream": {"recordCount": 1}}, "owner_token": "claim-owner",
        })

    def _git(self, repo: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            env=os.environ.copy(),
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def _commit_role_release(
        self,
        repo: Path,
        role_release: str,
        marker: str,
    ) -> str:
        distribution = repo / "blueprints" / "junior-project-manager"
        role_skill = distribution / "skills" / "role" / "junior-project-manager"
        dream_skill = distribution / "skills" / "role" / "dream-reflection"
        if distribution.exists():
            shutil.rmtree(distribution)
        role_skill.mkdir(parents=True)
        dream_skill.mkdir(parents=True)
        (distribution / "distribution.yaml").write_text(
            "\n".join(
                [
                    "role_blueprint: junior-project-manager",
                    f"role_release: {role_release}",
                    "distribution_owned:",
                    "  - SOUL.md",
                    "  - config.yaml",
                    "  - skills/role",
                    "  - distribution.yaml",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (distribution / "SOUL.md").write_text(f"# {marker}\n", encoding="utf-8")
        (distribution / "config.yaml").write_text("custom:\n  roleDefault: true\n", encoding="utf-8")
        (role_skill / "SKILL.md").write_text(
            f"---\nname: junior-project-manager\ndescription: {marker} role behavior.\n---\n\n# {marker}\n",
            encoding="utf-8",
        )
        (dream_skill / "SKILL.md").write_text(
            "---\nname: dream-reflection\ndescription: Reflect over Work History.\n---\n",
            encoding="utf-8",
        )
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-m", f"Role Release {role_release}")
        return self._git(repo, "rev-parse", "HEAD")

    @staticmethod
    def _settings(repo: Path, role_release: str, commit: str) -> RoleReleaseSettings:
        return RoleReleaseSettings(
            role_blueprint="junior-project-manager",
            source=str(repo),
            repository_path="blueprints/junior-project-manager",
            role_release=role_release,
            commit=commit,
            worker_id="worker-1",
            assignment_scope="team-alpha",
        )

    def _installed_profile(self, root: Path) -> tuple[Path, Path, RoleReleaseSettings]:
        repo = root / "source"
        home = root / "hermes"
        repo.mkdir()
        self._git(repo, "init")
        self._git(repo, "config", "user.email", "test@example.com")
        self._git(repo, "config", "user.name", "Hermes Test")
        commit = self._commit_role_release(repo, "3.0.0", "release-3")
        settings = self._settings(repo, "3.0.0", commit)
        installed = install_or_refresh_role_release(home, settings)
        return repo, installed.profile_home, settings

    def test_named_profile_worker_is_adopted_into_hermes_home_resumably(self):
        from blueprint import LEGACY_DEFAULT_PROFILE, adopt_worker_home_layout

        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            named = home / "profiles" / "junior-project-manager"
            (named / "local").mkdir(parents=True)
            (named / "local" / "worker.json").write_text("{}", encoding="utf-8")
            (named / "state.db").write_text("worker-state", encoding="utf-8")
            (named / "memories").mkdir()
            (home / "memories").mkdir()
            (home / "memories" / "MEMORY.md").write_text("default leftovers", encoding="utf-8")
            (home / "active_profile").write_text("junior-project-manager\n", encoding="utf-8")
            original_move = shutil.move
            moves = []

            def interrupted(source, destination):
                moves.append(source)
                if Path(source).parent == named and len([m for m in moves if Path(m).parent == named]) == 2:
                    raise OSError("interrupted")
                return original_move(source, destination)

            with patch("blueprint.shutil.move", side_effect=interrupted), self.assertRaises(OSError):
                adopt_worker_home_layout(home, "junior-project-manager")
            self.assertTrue(adopt_worker_home_layout(home, "junior-project-manager"))
            self.assertFalse(adopt_worker_home_layout(home, "junior-project-manager"))

            self.assertEqual((home / "state.db").read_text(encoding="utf-8"), "worker-state")
            self.assertTrue((home / "local" / "worker.json").is_file())
            self.assertFalse((home / "profiles").exists())
            legacy = home / LEGACY_DEFAULT_PROFILE
            self.assertEqual((legacy / "memories" / "MEMORY.md").read_text(encoding="utf-8"), "default leftovers")
            self.assertTrue((legacy / "active_profile").is_file())
            self.assertFalse((home / "memories" / "MEMORY.md").exists())

    def test_role_release_install_writes_worker_manifest_and_role_baseline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, profile, settings = self._installed_profile(Path(temp_dir))
            manifest = json.loads((profile / "local" / "worker.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["roleBlueprint"], "junior-project-manager")
        self.assertEqual(manifest["roleRelease"], "3.0.0")
        self.assertEqual(manifest["roleReleaseCommit"], settings.commit)
        self.assertEqual(manifest["workerId"], "worker-1")
        self.assertIn(
            "skills/role/junior-project-manager/SKILL.md",
            manifest["roleSkillBaseline"],
        )
        self.assertNotIn("skills/private", manifest["distributionOwned"])

    def test_same_role_release_is_reused(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo, profile, settings = self._installed_profile(root)
            (profile / "memories" / "MEMORY.md").write_text("private memory\n", encoding="utf-8")
            reused = install_or_refresh_role_release(root / "hermes", settings)
            memory_after = (profile / "memories" / "MEMORY.md").read_text(encoding="utf-8")

        self.assertFalse(reused.changed)
        self.assertEqual(memory_after, "private memory\n")

    def _local_bundle(self, root: Path, relative: str = "blueprints/junior-project-manager") -> tuple[Path, RoleReleaseSettings]:
        package = root / "application"
        distribution = package / relative
        shutil.copytree(RUNTIME_DIR.parents[1] / "blueprints" / "junior-project-manager", distribution)
        settings = replace(self._settings(package, "4.0.0", "a" * 40), source="local", repository_path=relative)
        payload = {
            "schemaVersion": 1,
            "roleBlueprint": settings.role_blueprint,
            "roleBlueprintPath": relative,
            "roleRelease": settings.role_release,
            "roleReleaseCommit": settings.commit,
            "files": {
                path.relative_to(distribution).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(distribution.rglob("*")) if path.is_file()
            },
        }
        manifest = package / "role-release.json"
        manifest.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        return package, replace(settings, bundle_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest())

    def test_local_role_installs_and_reuses_without_git_and_preserves_private_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package, settings = self._local_bundle(root)
            environment = {
                "AUTOPILOTS_APPLICATION_ROOT": str(package),
                "HERMES_ROLE_BLUEPRINT_SOURCE": "local",
                "HERMES_ROLE_BLUEPRINT": settings.role_blueprint,
                "HERMES_ROLE_BLUEPRINT_PATH": settings.repository_path,
                "HERMES_ROLE_RELEASE": settings.role_release,
                "HERMES_ROLE_RELEASE_COMMIT": settings.commit,
                "HERMES_ROLE_BUNDLE_SHA256": settings.bundle_sha256,
                "WORKER_ID": settings.worker_id,
                "WORKER_ASSIGNMENT_SCOPE": settings.assignment_scope,
            }
            with (
                patch.dict(os.environ, environment, clear=True),
                patch("blueprint.subprocess.run", side_effect=AssertionError("Offline roles must not invoke Git")),
            ):
                parsed = role_release_settings_from_environment()
                self.assertEqual(parsed, settings)
                installed = install_or_refresh_role_release(root / "home", parsed)
                memory = installed.profile_home / "memories" / "MEMORY.md"
                memory.write_text("private", encoding="utf-8")
                reused = install_or_refresh_role_release(root / "home", parsed)
            self.assertTrue(installed.changed)
            self.assertFalse(reused.changed)
            self.assertEqual(reused.manifest["roleBundleSha256"], settings.bundle_sha256)
            self.assertEqual(reused.manifest["roleReleaseCommit"], settings.commit)
            self.assertEqual(memory.read_text(encoding="utf-8"), "private")
            self.assertFalse((package / ".git").exists())

    def test_local_bundle_supports_custom_application_relative_role_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package, settings = self._local_bundle(root, relative="customer/roles/project-manager")
            with patch.dict(os.environ, {"AUTOPILOTS_APPLICATION_ROOT": str(package)}):
                installed = install_or_refresh_role_release(root / "home", settings)
            self.assertEqual(installed.manifest["roleBlueprintPath"], "customer/roles/project-manager")

    def test_local_role_revalidates_exact_content_inventory_even_on_reused_release(self):
        for mutation in ("changed", "extra", "missing", "manifest"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                package, settings = self._local_bundle(root)
                with patch.dict(os.environ, {"AUTOPILOTS_APPLICATION_ROOT": str(package)}):
                    install_or_refresh_role_release(root / "home", settings)
                    original = (root / "home" / "SOUL.md").read_bytes()
                    source = package / settings.repository_path / "SOUL.md"
                    if mutation == "changed":
                        source.write_text("tampered", encoding="utf-8")
                    elif mutation == "extra":
                        (source.parent / "unexpected.txt").write_text("extra", encoding="utf-8")
                    elif mutation == "missing":
                        source.unlink()
                    else:
                        with (package / "role-release.json").open("ab") as manifest:
                            manifest.write(b"\n")
                    with self.assertRaisesRegex(ValueError, "SHA-256|inventory"):
                        install_or_refresh_role_release(root / "home", settings)
                self.assertEqual((root / "home" / "SOUL.md").read_bytes(), original)

    def test_local_bundle_metadata_must_match_configured_release_and_commit(self):
        for field, value in (
            ("roleRelease", "99.0.0"), ("roleReleaseCommit", "f" * 40),
            ("roleBlueprintPath", "blueprints/other"), ("roleBlueprint", "other"),
            ("schemaVersion", 2),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                package, settings = self._local_bundle(root)
                manifest = package / "role-release.json"
                payload = json.loads(manifest.read_bytes())
                payload[field] = value
                manifest.write_text(json.dumps(payload), encoding="utf-8")
                settings = replace(settings, bundle_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest())
                with patch.dict(os.environ, {"AUTOPILOTS_APPLICATION_ROOT": str(package)}):
                    with self.assertRaisesRegex(ValueError, "configured role"):
                        install_or_refresh_role_release(root / "home", settings)
                self.assertFalse((root / "home").exists())

    def test_local_role_requires_expected_hash_and_rejects_nonrelative_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package, settings = self._local_bundle(root)
            with patch.dict(os.environ, {"AUTOPILOTS_APPLICATION_ROOT": str(package)}):
                for relative in ("../outside", "/absolute", "C:\\outside", ""):
                    with self.subTest(path=relative), self.assertRaisesRegex(ValueError, "relative"):
                        install_or_refresh_role_release(root / "home", replace(settings, repository_path=relative))
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    install_or_refresh_role_release(root / "home", replace(settings, bundle_sha256=""))
            with patch.dict(os.environ, {
                "HERMES_ROLE_BLUEPRINT_SOURCE": "local",
                "HERMES_ROLE_RELEASE": settings.role_release,
                "HERMES_ROLE_RELEASE_COMMIT": settings.commit,
            }, clear=True):
                with self.assertRaisesRegex(ValueError, "HERMES_ROLE_BUNDLE_SHA256"):
                    role_release_settings_from_environment()

    def test_private_playbook_and_work_history_survive_worker_refresh(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo, profile, settings = self._installed_profile(root)
            private_skill = profile / "skills" / "private" / "cedar-delivery" / "SKILL.md"
            private_skill.parent.mkdir(parents=True)
            private_skill.write_text(
                "---\nname: cedar-delivery\ndescription: Private Cedar delivery playbook.\n---\n",
                encoding="utf-8",
            )
            (profile / "memories" / "USER.md").write_text("prefers bullets\n", encoding="utf-8")
            (profile / "state.db").write_bytes(b"private work history")
            commit_v4 = self._commit_role_release(repo, "4.0.0", "release-4")

            refreshed = install_or_refresh_role_release(
                root / "hermes",
                self._settings(repo, "4.0.0", commit_v4),
            )
            private_exists = private_skill.exists()
            user_after = (profile / "memories" / "USER.md").read_text(encoding="utf-8")
            history_after = (profile / "state.db").read_bytes()
            soul_after = (profile / "SOUL.md").read_text(encoding="utf-8")

        self.assertTrue(refreshed.changed)
        self.assertTrue(private_exists)
        self.assertEqual(user_after, "prefers bullets\n")
        self.assertEqual(history_after, b"private work history")
        self.assertIn("# release-4", soul_after)
        self.assertNotEqual(settings.commit, commit_v4)

    def test_worker_refresh_rejects_role_release_rollback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo, profile, _ = self._installed_profile(root)
            older_commit = self._commit_role_release(repo, "2.9.0", "older")

            with self.assertRaisesRegex(ValueError, "newer Role Release"):
                install_or_refresh_role_release(
                    root / "hermes",
                    self._settings(repo, "2.9.0", older_commit),
                )
            soul_after = (profile / "SOUL.md").read_text(encoding="utf-8")

        self.assertIn("# release-3", soul_after)

    def test_failed_worker_refresh_restores_previous_role_release(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo, profile, settings = self._installed_profile(root)
            original_soul = (profile / "SOUL.md").read_text(encoding="utf-8")
            original_manifest = (profile / "local" / "worker.json").read_text(encoding="utf-8")
            commit_v4 = self._commit_role_release(repo, "4.0.0", "release-4")

            def fail_after_partial_copy(distribution_root, profile_home, owned_paths):
                (profile_home / "SOUL.md").write_text("# partial release\n", encoding="utf-8")
                raise OSError("simulated copy failure")

            with patch("blueprint._copy_owned_paths", side_effect=fail_after_partial_copy):
                with self.assertRaisesRegex(OSError, "simulated copy failure"):
                    install_or_refresh_role_release(
                        root / "hermes",
                        self._settings(repo, "4.0.0", commit_v4),
                    )

            restored_soul = (profile / "SOUL.md").read_text(encoding="utf-8")
            restored_manifest = (profile / "local" / "worker.json").read_text(encoding="utf-8")
            transaction_exists = (profile / "learning" / "refresh-transaction").exists()

        self.assertEqual(restored_soul, original_soul)
        self.assertEqual(restored_manifest, original_manifest)
        self.assertFalse(transaction_exists)
        self.assertNotEqual(settings.commit, commit_v4)

    def test_proxy_reports_native_api_startup_as_unavailable(self):
        client_class = httpx.AsyncClient

        def refuse(request):
            raise httpx.ConnectError("Connection refused", request=request)

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(
                start_hermes.httpx, "AsyncClient",
                side_effect=lambda **kwargs: client_class(transport=httpx.MockTransport(refuse), **kwargs),
            ),
        ):
            client = TestClient(start_hermes.create_health_app(Path(temp_dir), Path(temp_dir), None, None))
            response = client.get("/v1/runs/run-1")
        self.assertEqual(response.status_code, 503)
        self.assertIn("not accepting requests yet", response.json()["detail"])

    def test_native_api_is_loopback_only_behind_the_public_wrapper(self):
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.dict(os.environ, {"API_SERVER_PORT": "8642", "HERMES_GATEWAY_PORT": "9119", "API_SERVER_KEY": "k"}),
        ):
            config = start_hermes.hermes_config(Path(temp_dir))
            start_hermes.write_env_file(Path(temp_dir))
            env = (Path(temp_dir) / ".env").read_text(encoding="utf-8")

        self.assertEqual(config["gateway"]["platforms"]["api_server"]["port"], 9119)
        self.assertEqual(config["gateway"]["platforms"]["api_server"]["host"], "127.0.0.1")
        self.assertIn("API_SERVER_PORT=9119", env)
        self.assertIn("API_SERVER_HOST=127.0.0.1", env)

    def test_role_release_settings_require_new_environment_names(self):
        names = [
            "HERMES_ROLE_BLUEPRINT",
            "HERMES_ROLE_BLUEPRINT_SOURCE",
            "HERMES_ROLE_BLUEPRINT_PATH",
            "HERMES_ROLE_RELEASE",
            "HERMES_ROLE_RELEASE_COMMIT",
            "WORKER_ID",
            "WORKER_ASSIGNMENT_SCOPE",
        ]
        previous = {name: os.environ.get(name) for name in names}
        values = {
            "HERMES_ROLE_BLUEPRINT": "junior-project-manager",
            "HERMES_ROLE_BLUEPRINT_SOURCE": "https://example.com/roles.git",
            "HERMES_ROLE_BLUEPRINT_PATH": "roles/junior-project-manager",
            "HERMES_ROLE_RELEASE": "3.0.0",
            "HERMES_ROLE_RELEASE_COMMIT": "a" * 40,
            "WORKER_ID": "hermes1",
            "WORKER_ASSIGNMENT_SCOPE": "team-alpha",
        }
        os.environ.update(values)
        try:
            settings = role_release_settings_from_environment()
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        self.assertIsNotNone(settings)
        self.assertEqual(settings.role_release, "3.0.0")
        self.assertEqual(settings.worker_id, "hermes1")


if __name__ == "__main__":
    unittest.main()
