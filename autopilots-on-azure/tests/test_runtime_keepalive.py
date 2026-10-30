import os
import sys
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtimes" / "hermes"))

import runtime_keepalive


class KeepaliveTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.send = Mock(return_value=200)
        self.supervisor = runtime_keepalive.Keepalive(
            "https://sandbox.example/health",
            clock=lambda: self.now,
            send=self.send,
        )
        self.run = patch.object(self.supervisor, "_run")
        self.run.start()
        self.addCleanup(self.run.stop)
        self.addCleanup(self.supervisor.close)

    def test_idle_does_not_send_requests(self):
        self.assertEqual(self.supervisor.tick(), 0)
        self.send.assert_not_called()

    def test_concurrent_operations_release_only_themselves(self):
        with self.supervisor.activity("model"):
            with self.supervisor.activity("tool"):
                self.assertEqual(self.supervisor.tick(), 2)
            self.assertEqual(self.supervisor.tick(), 1)
        self.assertEqual(self.supervisor.tick(), 0)

    def test_exception_releases_operation(self):
        with self.assertRaisesRegex(RuntimeError, "model failed"):
            with self.supervisor.activity("model"):
                raise RuntimeError("model failed")
        self.assertEqual(self.supervisor.tick(), 0)

    def test_silent_work_outlives_idle_timeout_and_pings_at_most_once_per_minute(self):
        with self.supervisor.activity("silent-tool"):
            for elapsed in (0, 1, 59, 60, 120, 180, 240, 300, 360):
                self.now = 1000 + elapsed
                self.assertEqual(self.supervisor.tick(), 1)
        self.assertEqual(self.send.call_count, 7)
        self.now += 60
        self.assertEqual(self.supervisor.tick(), 0)
        self.assertEqual(self.send.call_count, 7)

    def test_stuck_operation_expires_without_extending_its_deadline(self):
        with self.supervisor.activity("stuck"):
            for _ in range(29):
                self.now += 60
                self.assertEqual(self.supervisor.tick(), 1)
            self.now += 60
            with self.assertLogs(runtime_keepalive.logger, level="ERROR"):
                self.assertEqual(self.supervisor.tick(), 0)
            sends = self.send.call_count
            self.now += 600
            self.assertEqual(self.supervisor.tick(), 0)
            self.assertEqual(self.send.call_count, sends)

    def test_finished_owner_thread_does_not_leave_busy_state(self):
        activity = self.supervisor.activity("orphaned")
        owner = threading.Thread(target=activity.__enter__)
        owner.start()
        owner.join()
        try:
            with self.assertLogs(runtime_keepalive.logger, level="ERROR"):
                self.assertEqual(self.supervisor.tick(), 0)
            self.send.assert_not_called()
        finally:
            activity.__exit__(None, None, None)

    def test_failed_ping_does_not_release_running_work(self):
        self.send.return_value = None
        with self.supervisor.activity("model"):
            self.assertEqual(self.supervisor.tick(), 1)
            self.now += 60
            self.assertEqual(self.supervisor.tick(), 1)
        self.assertEqual(self.send.call_count, 2)

    def test_native_middleware_preserves_results_and_cleans_errors(self):
        context = Mock()
        with patch.object(runtime_keepalive, "get_keepalive", return_value=self.supervisor):
            runtime_keepalive.register(context)
        middleware = dict(call.args for call in context.register_middleware.call_args_list)
        for name, argument in (("llm_execution", "request"), ("tool_execution", "args")):
            with self.subTest(middleware=name):
                payload = {"opaque": "never-inspected"}
                result = object()

                def execute(value):
                    self.assertIs(value, payload)
                    self.assertEqual(self.supervisor.tick(), 1)
                    return result

                self.assertIs(middleware[name](**{argument: payload}, next_call=execute), result)
                self.assertEqual(self.supervisor.tick(), 0)
                with self.assertRaisesRegex(RuntimeError, "native error"):
                    middleware[name](
                        **{argument: payload},
                        next_call=Mock(side_effect=RuntimeError("native error")),
                    )
                self.assertEqual(self.supervisor.tick(), 0)

    def test_invalid_timing_is_rejected(self):
        for interval, maximum in (
            (0, 1800), (60, 60), (60, float("nan")), (60, float("inf")),
        ):
            with self.subTest(interval=interval, maximum=maximum):
                with self.assertRaises(ValueError):
                    runtime_keepalive.Keepalive("https://sandbox.example", interval=interval, maximum=maximum)


class KeepaliveConfigurationTests(unittest.TestCase):
    def test_native_environment_supplies_own_ingress_without_management_calls(self):
        with patch.dict(os.environ, {
            "ADC_SANDBOX_ID": "446309e7-0bb8-42a8-a220-fef8baa0b22e",
            "ADC_PROXY_DNS_SUFFIX": "proxy.swedencentral.azuredevcompute.io",
            "API_SERVER_PORT": "8642",
        }, clear=True):
            self.assertEqual(
                runtime_keepalive.sandbox_health_url(),
                "https://446309e7-0bb8-42a8-a220-fef8baa0b22e--8642."
                "proxy.swedencentral.azuredevcompute.io/health",
            )

    def test_local_execution_needs_no_sandbox_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(runtime_keepalive.sandbox_health_url())

    def test_partial_or_invalid_native_configuration_is_rejected(self):
        base = {
            "ADC_SANDBOX_ID": "446309e7-0bb8-42a8-a220-fef8baa0b22e",
            "ADC_PROXY_DNS_SUFFIX": "proxy.swedencentral.azuredevcompute.io",
        }
        for values in (
            {"ADC_SANDBOX_ID": base["ADC_SANDBOX_ID"]},
            {**base, "ADC_PROXY_DNS_SUFFIX": "host/path"},
            {**base, "ADC_SANDBOX_ID": "not-a-sandbox-id"},
            {**base, "API_SERVER_PORT": "0"},
        ):
            with self.subTest(values=values), patch.dict(os.environ, values, clear=True):
                with self.assertRaises(ValueError):
                    runtime_keepalive.sandbox_health_url()

    def test_ping_failure_is_logged_instead_of_reported_as_success(self):
        errors = (
            urllib.error.URLError("unreachable"),
            urllib.error.HTTPError("https://sandbox.example", 503, "not ready", {}, None),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                with (
                    patch.object(runtime_keepalive.urllib.request, "urlopen", side_effect=error),
                    self.assertLogs(runtime_keepalive.logger),
                ):
                    status = runtime_keepalive.ping("https://sandbox.example")
                self.assertEqual(status, 503 if isinstance(error, urllib.error.HTTPError) else None)


if __name__ == "__main__":
    unittest.main()
