import asyncio
import sys
import threading
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request
from bridge.generated_files import NativeSkillLoadEvidence

RUNTIME = Path(__file__).resolve().parents[1] / "runtimes" / "hermes"
sys.path.insert(0, str(RUNTIME))
import runtime_routes


class RuntimeActivityTests(unittest.TestCase):
    def observe(self, pending=0, inflight=0, live_task=False, workers=0, scopes=0):
        adapter = SimpleNamespace(
            _pending_agent_requests=pending, _inflight_agent_runs=inflight,
            _active_run_tasks={"run": SimpleNamespace(done=lambda: not live_task)},
        )
        native = SimpleNamespace(api_worker_live_count=lambda: workers)
        supervisor = SimpleNamespace(status=lambda: {"active": scopes})
        with (
            patch.dict(sys.modules, {"gateway.platforms.api_server_runs": native}),
            patch("runtime_keepalive.get_keepalive", return_value=supervisor),
        ):
            return runtime_routes.native_activity(adapter)

    def test_native_quiescence_covers_queued_tasks_and_live_executor_threads(self):
        result = self.observe(pending=1, inflight=2, live_task=True, workers=3, scopes=1)
        self.assertEqual(result["active"], 8)
        self.assertEqual(result["nativeWorkerThreads"], 3)
        self.assertEqual(result["liveRunTasks"], 1)

    def test_an_idle_native_adapter_attests_zero_actual_work(self):
        self.assertEqual(self.observe()["active"], 0)

    def test_invalid_native_state_is_not_a_success_shaped_idle_fallback(self):
        for value in (True, -1, "invalid"):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                self.observe(pending=value)


class RuntimeActivityRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_skill_load_route_is_authenticated_gated_and_metadata_only(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"API_SERVER_KEY": "activity-unit-key", "GENERATED_CHAT_FILES_ENABLED": "true"}),
            patch.object(runtime_routes, "native_skill_load_evidence", NativeSkillLoadEvidence()),
        ):
            app = web.Application()
            runtime_routes.register_routes(app, Path(directory))
            async with TestClient(TestServer(app)) as client:
                url = "/internal/skills/evidence/run-1"
                self.assertEqual((await client.get(url)).status, 401)
                headers = {"X-Autopilot-Key": "activity-unit-key"}
                response = await client.get(url, headers=headers)
                self.assertEqual(response.status, 200)
                self.assertEqual(await response.json(), {"runId": "run-1", "loads": []})
                response = await client.get("/internal/skills/evidence/invalid%3Aid", headers=headers)
                self.assertEqual(response.status, 400)
                with patch.dict("os.environ", {"GENERATED_CHAT_FILES_ENABLED": "false"}):
                    self.assertEqual((await client.get(url, headers=headers)).status, 503)

    async def test_cancelled_http_request_retains_its_native_worker_until_actual_completion(self):
        started = threading.Event()
        release = threading.Event()
        adapter = SimpleNamespace(_inflight_agent_runs=0, _active_run_tasks={})
        native = SimpleNamespace(api_worker_live_count=lambda: 0)

        def blocking_diagnostics(_home):
            started.set()
            if not release.wait(5):
                raise TimeoutError("The test did not release its native worker.")
            return {"jobs": []}

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"API_SERVER_KEY": "activity-unit-key"}),
            patch.dict(sys.modules, {"gateway.platforms.api_server_runs": native}),
            patch("runtime_keepalive.get_keepalive", return_value=None),
            patch.object(runtime_routes, "cron_diagnostics", side_effect=blocking_diagnostics),
        ):
            app = web.Application()
            runtime_routes.register_routes(app, Path(directory), adapter=adapter)
            handler = next(
                route.handler for route in app.router.routes()
                if route.method == "GET" and route.resource.canonical == "/internal/cron/diagnostics"
            )
            headers = {"X-Autopilot-Key": "activity-unit-key"}
            async with TestClient(TestServer(app)) as client:
                task = asyncio.create_task(handler(make_mocked_request(
                    "GET", "/internal/cron/diagnostics", headers=headers, app=app,
                )))
                try:
                    self.assertTrue(await asyncio.to_thread(started.wait, 2))
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    response = await client.get("/internal/runtime/activity", headers=headers)
                    status = await response.json()
                    self.assertEqual(status["runtimeEndpointWorkers"], 1)
                    self.assertGreater(status["active"], 0)
                finally:
                    release.set()
                for _ in range(100):
                    response = await client.get("/internal/runtime/activity", headers=headers)
                    status = await response.json()
                    if status["runtimeEndpointWorkers"] == 0:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(status["active"], 0)

    async def test_real_authenticated_route_uses_live_adapter_without_profile_argument_injection(self):
        adapter = SimpleNamespace(
            _inflight_agent_runs=0, _active_run_tasks={},
        )
        native = SimpleNamespace(api_worker_live_count=lambda: 0)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"API_SERVER_KEY": "activity-unit-key"}),
            patch.dict(sys.modules, {"gateway.platforms.api_server_runs": native}),
            patch("runtime_keepalive.get_keepalive", return_value=None),
        ):
            app = web.Application()
            runtime_routes.register_routes(app, Path(directory), adapter=adapter)
            async with TestClient(TestServer(app)) as client:
                denied = await client.get("/internal/runtime/activity")
                self.assertEqual(denied.status, 401)
                response = await client.get(
                    "/internal/runtime/activity", headers={"X-Autopilot-Key": "activity-unit-key"},
                )
                self.assertEqual(response.status, 200)
                status = await response.json()
                self.assertEqual(status["active"], 0)
                self.assertEqual(status["pendingAdmissions"], 0)
