import unittest
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import scripts.demo_ops as ops
import scripts.user_schedule_smoke as user_smoke
import scripts.servicebus_dream_smoke as dream_smoke
from scripts.user_schedule_smoke import require_awake_gateway, require_suspend_window
from scripts.servicebus_dream_smoke import completed_receipt, schedule_definition, system_job

from scripts.demo_ops import (
    SANDBOX_DATA_OWNER_ROLE,
    invoke_body,
    missing_expected_markers,
    role_assignment_command,
    runtime_list,
    runtime_sandbox_selector,
    sandbox_group_url,
    sandbox_matches,
    sandbox_items,
    worker_sandbox,
)


class DemoOpsTests(unittest.TestCase):
    def test_runtime_list_expands_both_in_stable_order(self):
        self.assertEqual(runtime_list("both"), ["openclaw", "hermes"])

    def test_invoke_body_uses_runtime_default_prompt(self):
        body = invoke_body("hermes")

        self.assertTrue(body["conversationId"].startswith("hermes-operator-smoke-"))
        self.assertEqual(body["message"], "Reply with exactly: Hermes bridge OK")

    def test_missing_expected_markers_detects_failed_openclaw_smoke(self):
        missing = missing_expected_markers("openclaw", {"response": "core_banking only"})

        self.assertIn("card_payments", missing)

    def outputs(self):
        return {
            "agent_runtime": "hermes", "worker_id": "worker-one",
            "sandbox_location": "swedencentral", "resource_group_name": "rg-test",
            "subscription_id": "sub-test", "runtime_data_volume_name": "worker-data",
            "sandbox_groups": {
                role: {"name": f"worker-{role}", "id": f"/groups/worker-{role}"}
                for role in ops.SANDBOX_ROLES
            },
            "sandbox_services": {
                role: {"sandbox_id": f"sandbox-{role}"}
                for role in ("gateway", "private-mcp", "public-mcp")
            },
        }

    def service(self, role="gateway", state="Running", suspend=False):
        return {
            "id": f"sandbox-{role}", "state": state,
            "labels": {"worker": "worker-one", "service": role},
            "lifecycle": {"autoSuspendPolicy": {"enabled": suspend}},
        }

    def test_logs_read_actual_sandbox_file(self):
        group = MagicMock()
        group.__enter__.return_value = group
        group.get_sandbox_client.return_value.read_file.return_value = b"first\nsecond\n"
        args = SimpleNamespace(runtime="hermes", state_name="worker-one", app="gateway",
                               path="", tail=1, follow=False, execute=True)
        with (
            patch.object(ops, "load_json", return_value=self.outputs()),
            patch.object(ops, "worker_sandbox", return_value=self.service()),
            patch.object(ops, "SandboxGroupClient", return_value=group) as constructor,
            patch.object(ops, "DefaultAzureCredential"),
            patch("builtins.print") as printed,
        ):
            self.assertEqual(ops.run_logs(args), 0)
        self.assertEqual(constructor.call_args.kwargs["sandbox_group"], "worker-gateway")
        group.get_sandbox_client.assert_called_once_with("sandbox-gateway")
        group.get_sandbox_client.return_value.read_file.assert_called_once_with("/app/.sandbox-service.log")
        printed.assert_called_once_with("second", flush=True)

    def test_logs_fail_when_sandbox_is_suspended_without_resuming(self):
        args = SimpleNamespace(runtime="hermes", state_name="", app="gateway",
                               path="", tail=10, follow=False, execute=True)
        with (
            patch.object(ops, "load_json", return_value=self.outputs()),
            patch.object(ops, "worker_sandbox", return_value=self.service(state="Suspended")),
            patch.object(ops, "SandboxGroupClient") as constructor,
            patch.object(ops, "print_json"),
        ):
            self.assertEqual(ops.run_logs(args), 1)
        constructor.assert_not_called()

    def test_runtime_sandbox_selector_uses_captured_runtime_state(self):
        selector = runtime_sandbox_selector(
            "hermes",
            {"worker_id": "worker-one", "runtime_disk_image_name": "hermes-image", "runtime_data_volume_name": "hermes-data"},
        )

        self.assertEqual(selector["labels"]["kind"], "hermes")
        self.assertEqual(selector["dataVolume"], "hermes-data")
        self.assertEqual(selector["labels"]["worker"], "worker-one")

    def test_sandbox_matches_labels_and_data_volume(self):
        self.assertTrue(
            sandbox_matches(
                {
                    "labels": {"app": "autopilots-on-azure", "kind": "hermes"},
                    "volumes": [{"volumeName": "hermes-data"}],
                },
                {"labels": {"app": "autopilots-on-azure", "kind": "hermes"}, "dataVolume": "hermes-data"},
            )
        )

    def test_sandbox_group_url_uses_sandbox_region(self):
        url = sandbox_group_url(self.outputs(), "gateway")

        self.assertEqual(
            url,
            "https://management.swedencentral.azuredevcompute.io"
            "/subscriptions/sub-test/resourceGroups/rg-test/sandboxGroups/worker-gateway",
        )

    def test_sandbox_list_rejects_unsupported_response_instead_of_empty_success(self):
        self.assertEqual(sandbox_items({"value": [{"id": "one"}]}), [{"id": "one"}])
        with self.assertRaises(ValueError):
            sandbox_items({"unexpected": []})

    def test_worker_sandbox_does_not_select_another_worker(self):
        service = self.service()
        service["labels"]["worker"] = "other-worker"
        with patch.object(ops, "list_role_sandboxes", return_value=[service]):
            with self.assertRaisesRegex(RuntimeError, "found 0"):
                worker_sandbox(self.outputs(), "gateway")

    def test_status_checks_each_native_group_and_gateway_lifecycle(self):
        for suspend, expected in ((False, True), (True, False)):
            with (
                self.subTest(suspend=suspend),
                patch.object(ops, "load_json", return_value=self.outputs()),
                patch.object(ops, "list_role_sandboxes", side_effect=lambda outputs, role, **kwargs: (
                    [] if role in ("runtime", "generated-apps") else [self.service(role, suspend=suspend)]
                )) as listed,
            ):
                result = ops.sandbox_status_check("hermes")
            self.assertEqual(result["ok"], expected)
            self.assertEqual(listed.call_count, 5)

    def test_status_does_not_hide_failed_runtime(self):
        runtime = {"id": "runtime", "state": "Failed",
                   "labels": {"app": "autopilots-on-azure", "kind": "hermes", "worker": "worker-one"},
                   "volumes": [{"volumeName": "worker-data"}]}
        with (
            patch.object(ops, "load_json", return_value=self.outputs()),
            patch.object(ops, "list_role_sandboxes", side_effect=lambda outputs, role, **kwargs: (
                [runtime] if role == "runtime" else [] if role == "generated-apps" else [self.service(role)]
            )),
        ):
            self.assertFalse(ops.sandbox_status_check("hermes")["ok"])

    def test_activate_uses_captured_named_worker_workspace(self):
        output_path = MagicMock()
        output_path.exists.return_value = True
        args = SimpleNamespace(runtime="hermes", state_name="worker-one", workspace="")
        with (
            patch.object(ops, "runtime_outputs_path", return_value=output_path),
            patch.object(ops, "load_json", return_value={"terraform_workspace": "autopilot-worker-one"}),
            patch.object(ops, "activate_runtime_tfvars") as activate,
            patch.object(ops, "print_json") as printed,
        ):
            self.assertEqual(ops.run_activate(args), 0)
        activate.assert_called_once_with("hermes", "worker-one")
        self.assertEqual(printed.call_args.args[0]["terraformWorkspace"], "autopilot-worker-one")

    def test_unavailable_diagnostics_are_not_success(self):
        with (
            patch.object(ops, "load_json", return_value={"bridge_url": "https://gateway.example"}),
            patch.object(ops, "http_json", return_value={"ok": False, "statusCode": 404}),
        ):
            self.assertFalse(ops.diag_check("hermes")["ok"])

    def test_schedule_smoke_requires_awake_gateway(self):
        for state, suspend in (("Suspended", False), ("Running", True)):
            with self.subTest(state=state, suspend=suspend), patch(
                "scripts.user_schedule_smoke.worker_sandbox", return_value=self.service(state=state, suspend=suspend)
            ):
                with self.assertRaisesRegex(RuntimeError, "auto-suspend disabled"):
                    require_awake_gateway(self.outputs())

    def test_runtime_suspension_observation_uses_real_policy(self):
        with patch("scripts.user_schedule_smoke.worker_sandbox", return_value={
            "lifecycle": {"autoSuspendPolicy": {"enabled": True, "interval": 1800}}
        }):
            with self.assertRaisesRegex(ValueError, "1860"):
                require_suspend_window(self.outputs(), 180)
            require_suspend_window(self.outputs(), 1900)

    def test_dream_smoke_validates_live_schedule_not_changing_next_run(self):
        before = {"id": "dream", "schedule": {"cron": "0 2 * * *"}, "enabled": True,
                  "systemType": "dream", "repeat": None, "revision": "old", "nextRunAt": "old"}
        after = {**before, "revision": "new", "nextRunAt": "new"}
        self.assertEqual(schedule_definition(before), schedule_definition(after))
        before["repeat"] = {"times": 10, "completed": 2}
        after["repeat"] = {"times": 10, "completed": 3}
        self.assertEqual(schedule_definition(before), schedule_definition(after))
        after["repeat"]["times"] = 20
        self.assertNotEqual(schedule_definition(before), schedule_definition(after))
        with self.assertRaisesRegex(ValueError, "live schedule"):
            schedule_definition({"id": "dream"})
        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            system_job({"jobs": []})

    def test_dream_smoke_fails_immediately_on_failed_receipt(self):
        self.assertFalse(completed_receipt({"state": "pending"}))
        self.assertTrue(completed_receipt({"state": "completed", "success": True}))
        with self.assertRaisesRegex(RuntimeError, "Dreaming failed"):
            completed_receipt({"state": "completed", "success": False})

    def test_user_schedule_smoke_uses_real_urls_and_cleans_only_its_job(self):
        calls = []
        marker = "A12-SCHEDULE-123456789abc"

        def transport(request):
            calls.append((request.method, str(request.url)))
            path = request.url.path
            if path == "/internal/runtime/ensure":
                payload = {"gatewayUrl": "https://runtime.example"}
            elif path == "/api/jobs" and request.method == "POST":
                payload = {"job": {"id": "smoke-job"}}
            elif path == "/internal/cron/jobs":
                payload = {"jobs": [{"id": "smoke-job", "externallyScheduled": True, "revision": "revision"}]}
            elif path.startswith("/internal/cron/delivery-receipt/"):
                payload = {"status": "delivered", "outputSha256": hashlib.sha256(marker.encode()).hexdigest()}
            else:
                payload = {"status": "ok"}
            return httpx.Response(200, json=payload)

        config = MagicMock()
        config.read_text.return_value = json.dumps({"api_server_key": "unit-test-key"})
        output_file = MagicMock()
        output_file.read_text.return_value = json.dumps({
            **self.outputs(), "user_scheduling_enabled": True,
            "bridge_url": "https://gateway.example", "scheduler_servicebus_queue_name": "worker-queue",
        })
        client = httpx.Client(transport=httpx.MockTransport(transport))
        with (
            patch.object(user_smoke, "runtime_app_tfvars_path", return_value=config),
            patch.object(user_smoke, "runtime_outputs_path", return_value=output_file),
            patch.object(user_smoke, "terraform_output", return_value={
                "resource_group_name": "rg", "scheduler_servicebus_namespace_name": "namespace",
            }),
            patch.object(user_smoke, "require_awake_gateway", return_value={"state": "Running"}),
            patch.object(user_smoke, "runtime_state", return_value="Running"),
            patch.object(user_smoke, "az_value", return_value="1"),
            patch.object(user_smoke, "az_json", return_value={}),
            patch.object(user_smoke.uuid, "uuid4", return_value=SimpleNamespace(hex="123456789abc")),
            patch.object(user_smoke.time, "sleep"),
            patch.object(user_smoke.httpx, "Client", return_value=client),
            patch("sys.argv", ["user_schedule_smoke", "--state-name", "worker-one"]),
            patch("builtins.print") as printed,
        ):
            user_smoke.main()
        self.assertIn(("POST", "https://gateway.example/internal/runtime/ensure"), calls)
        self.assertIn(("DELETE", "https://runtime.example/api/jobs/smoke-job"), calls)
        self.assertEqual(sum(method == "POST" and url.endswith("/api/jobs") for method, url in calls), 1)
        result = json.loads(printed.call_args.args[0])
        self.assertTrue(result["ok"])
        self.assertFalse(result["runtimeSuspensionObserved"])
        self.assertNotIn("bridgeScaledToZeroBeforeDue", result)

    def test_dream_smoke_enqueues_once_and_compares_live_schedule(self):
        calls = []
        job = {"id": "system-dream", "name": "Platform Dreaming", "revision": "revision",
               "schedule": {"cron": "0 2 * * *"}, "enabled": True, "systemType": "dream"}
        receipt = {"jobId": "system-dream", "revision": "revision", "occurrenceId": "adhoc-one",
                   "state": "completed", "success": True}

        def transport(request):
            calls.append((request.method, str(request.url)))
            if request.url.path == "/internal/runtime/ensure":
                payload = {"gatewayUrl": "https://runtime.example"}
            elif request.url.path == "/internal/cron/system/run-now":
                payload = {"revision": "revision", "occurrenceId": "adhoc-one"}
            else:
                payload = {"jobs": [job], "systemReceipts": [receipt]}
            return httpx.Response(200, json=payload)

        config = MagicMock()
        config.read_text.return_value = json.dumps({"api_server_key": "unit-test-key"})
        output_file = MagicMock()
        output_file.read_text.return_value = json.dumps({
            **self.outputs(), "servicebus_dream_enabled": True,
            "bridge_url": "https://gateway.example", "scheduler_servicebus_queue_name": "worker-queue",
        })
        client = httpx.Client(transport=httpx.MockTransport(transport))
        with (
            patch.object(dream_smoke, "runtime_app_tfvars_path", return_value=config),
            patch.object(dream_smoke, "runtime_outputs_path", return_value=output_file),
            patch.object(dream_smoke, "terraform_output", return_value={
                "resource_group_name": "rg", "scheduler_servicebus_namespace_name": "namespace",
            }),
            patch.object(dream_smoke, "require_awake_gateway", return_value={"state": "Running"}),
            patch.object(dream_smoke, "runtime_state", return_value="Running"),
            patch.object(dream_smoke, "az_json", return_value={}),
            patch.object(dream_smoke.httpx, "Client", return_value=client),
            patch("sys.argv", ["servicebus_dream_smoke", "--state-name", "worker-one"]),
            patch("builtins.print") as printed,
        ):
            dream_smoke.main()
        writes = [(method, url) for method, url in calls if method != "GET" and not url.endswith("/internal/runtime/ensure")]
        self.assertEqual(writes, [("POST", "https://runtime.example/internal/cron/system/run-now")])
        result = json.loads(printed.call_args.args[0])
        self.assertTrue(result["productionScheduleUnchanged"])
        self.assertEqual(result["productionSchedule"]["schedule"], job["schedule"])

    def test_role_assignment_command_grants_sandbox_data_owner(self):
        command = role_assignment_command("scope-1", "user-1")

        self.assertEqual(command[:4], ["az", "role", "assignment", "create"])
        self.assertIn(SANDBOX_DATA_OWNER_ROLE, command)
        self.assertIn("user-1", command)


if __name__ == "__main__":
    unittest.main()
