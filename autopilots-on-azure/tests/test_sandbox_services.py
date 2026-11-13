from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import ast
import io
import json
import os
import unittest
import shlex
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, mock_open, patch

from scripts.sandbox_services import (
    BOOTSTRAP,
    SERVICE_CONFIG_PATH,
    SandboxService,
    configure_callback_port,
    convert_disk_image,
    deploy_service,
    deploy_runtime,
    deploy_sandbox_services,
    delete_owned_sandboxes,
    gateway_environment,
    mcp_service,
    prepare_disk_image,
    require_always_running,
    require_service_health,
    service_body,
    worker_settings,
)
from scripts.sandbox_runtime import AgentSandboxConfig, config_from_environment, runtime_telemetry_environment
from scripts.application_releases import ApplicationRelease, PYTHON_BASE_IMAGE


class SandboxServicesTests(unittest.TestCase):
    def setUp(self):
        self.release = ApplicationRelease("gateway", "f" * 64, b"release")
        for target, value in (
            ("scripts.sandbox_services.build_release", self.release),
            ("scripts.sandbox_services.install_release", self.release.directory),
            ("scripts.sandbox_services.configure_callback_port", "https://gateway--8001.example"),
        ):
            item = patch(target, return_value=value)
            item.start()
            self.addCleanup(item.stop)

    def group(self, role="gateway"):
        return {
            "name": f"worker-{role}", "identity_client_id": f"{role}-client",
            "identity_resource_id": f"/identities/{role}",
            "vnet_connection_name": "private-tools" if role in {"runtime", "gateway"} else "",
        }

    def test_public_digest_conversion_has_no_registry_identity_or_acr_lookup(self):
        image = "ghcr.io/example/gateway@sha256:" + "a" * 64
        client = Mock()
        client.list_disk_images.return_value = []
        client.get_disk_image.return_value = SimpleNamespace(
            id="converted", image=SimpleNamespace(base=image), status=SimpleNamespace(state="Ready"),
        )
        with patch("scripts.sandbox_services.convert_disk_image", return_value="converted") as convert, patch(
            "scripts.sandbox_services.run_text", side_effect=AssertionError("No ACR lookup"),
        ):
            result = prepare_disk_image(
                client, role="gateway", image=image, source=image, acr_name="", subscription_id="subscription",
                identity_client_id="workload-client", vnet_connection_name="private",
            )
            self.assertEqual(result, "converted")
            body = convert.call_args.args[1]
            self.assertEqual(body["source"], {"kind": "registry", "imageUrl": image})
            self.assertEqual(body["customerVnetConnectionName"], "private")
            with self.assertRaisesRegex(ValueError, "exact immutable"):
                prepare_disk_image(
                    client, role="gateway", image=image, source="ghcr.io/example/gateway:latest", acr_name="",
                    subscription_id="subscription", identity_client_id="workload-client",
                )

    def test_prebuilt_gateway_uses_packaged_startup_and_disables_both_lifecycle_policies(self):
        service = SandboxService(
            role="gateway", image="image", port=8001, command=("unused",), environment={},
            source_commit="a" * 40,
        )
        body = service_body(service, self.group(), "disk", "worker")
        self.assertEqual(body["entrypoint"], ["/app/.venv/bin/python", "/app/container_start.py", "gateway"])
        self.assertIs(body["lifecycle"]["autoSuspendPolicy"]["enabled"], False)
        self.assertIs(body["lifecycle"]["autoDeletePolicy"]["enabled"], False)
        self.assertEqual(body["environment"]["AUTOPILOTS_SERVICE_CONFIG"], SERVICE_CONFIG_PATH)

    def test_never_suspend_verification_rejects_missing_or_inferred_booleans(self):
        group = Mock()
        group._group_path = "/group"
        model = {"state": "Running", "lifecycle": {
            "autoSuspendPolicy": {"enabled": False}, "autoDeletePolicy": {"enabled": False},
        }}
        group._dp_get.return_value = model
        self.assertEqual(require_always_running(group, "sandbox")["state"], "Running")
        for value in (None, True, 0):
            group._dp_get.return_value = {
                **model, "lifecycle": {**model["lifecycle"], "autoSuspendPolicy": {"enabled": value}},
            }
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, "explicitly disabled"):
                require_always_running(group, "sandbox")

    def test_source_release_readiness_accepts_minimal_public_health(self):
        response = io.BytesIO(json.dumps({"status": "ok", "release": self.release.digest}).encode())
        response.status = 200
        sandbox = Mock()

        def execute(command):
            probe = shlex.split(command)[2]
            stdout = io.StringIO()
            with patch("urllib.request.urlopen", return_value=response) as request, redirect_stdout(stdout):
                exec(compile(probe, "<source-release-health-probe>", "exec"), {})
            request.assert_called_once_with("http://127.0.0.1:8000/health", timeout=5)
            return SimpleNamespace(exit_code=0, stderr="", stdout=stdout.getvalue())

        sandbox.exec.side_effect = execute
        require_service_health(sandbox, 8000, self.release.digest)
        sandbox.exec.assert_called_once()

    def test_health_does_not_accept_sdk_default_exit_zero_without_this_invocations_completion(self):
        sandbox = Mock()
        sandbox.exec.return_value = SimpleNamespace(exit_code=0, stdout="", stderr="")
        with patch("scripts.sandbox_services.time.monotonic", side_effect=[0, 0, 181]), patch(
            "scripts.sandbox_services.time.sleep",
        ), self.assertRaises(TimeoutError):
            require_service_health(sandbox, 8001)

    def test_remote_bootstrap_and_quiescence_commands_are_valid_python(self):
        from scripts.sandbox_services import require_quiescent_service
        from scripts.application_releases import INSTALL_RELEASE
        for source in (BOOTSTRAP, INSTALL_RELEASE):
            ast.parse(source)
        sandbox = Mock()
        def execute(command):
            script = shlex.split(command)[2]
            ast.parse(script)
            self.assertIn("TRANSPORT_ROLE", script)
            self.assertIn("retainedReceiptsAwaitingSettlement", script)
            self.assertIn("127.0.0.1:8000", script)
            self.assertNotIn("TRANS8000", script)
            return SimpleNamespace(exit_code=0, stderr="")
        sandbox.exec.side_effect = execute
        require_quiescent_service(
            sandbox, {"environment": {"TRANSPORT_CONNECTION_ID": "/connection"}}, 8000,
        )

    def test_quiescence_preserves_inactive_pending_receipts_but_rejects_live_work(self):
        from scripts.sandbox_services import require_quiescent_service

        environment = {
            "TRANSPORT_ROLE": "gateway", "TRANSPORT_CONNECTION_ID": "/connection",
            "TRANSPORT_JOURNAL_PATH": "retained.db", "API_SERVER_KEY": "test-key",
        }
        for active in (0, 1):
            with self.subTest(active=active):
                sandbox = Mock()
                connection = Mock()
                connection.execute.return_value = [{"phase": "retry"}]
                result = {"active": active, "unfinished": 1, "unsettled": 1}

                def execute(command):
                    script = shlex.split(command)[2]
                    with (
                        patch("builtins.open", mock_open(read_data=json.dumps({"environment": environment}))),
                        patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(result).encode())),
                        patch("sqlite3.connect", return_value=connection) as database,
                        redirect_stdout(io.StringIO()),
                    ):
                        exec(compile(script, "<quiescence>", "exec"), {})
                        database.assert_not_called()
                    return SimpleNamespace(exit_code=0, stderr="")

                sandbox.exec.side_effect = execute
                if active:
                    with self.assertRaisesRegex(RuntimeError, "unfinished"):
                        require_quiescent_service(sandbox, {"environment": environment}, 8000)
                else:
                    require_quiescent_service(sandbox, {"environment": environment}, 8000)
                connection.execute.assert_not_called()

    def config(self):
        values = dict.fromkeys([
            "runtime_disk_source_image", "previous_api_server_key",
            "agent365_agent_user_principal_name", "worker_assignment_scope",
        ], "")
        values.update({
            "agent_runtime": "hermes", "autopilot_name": "worker",
            "runtime_image": "registry/hermes@sha256:123",
            "runtime_disk_image_name": "hermes-release",
            "api_server_key": "runtime-test-key", "agent365_tenant_id": "tenant",
            "agent365_client_id": "existing-blueprint",
            "agent365_agent_identity_client_id": "existing-agent-client",
            "agent365_agent_identity_object_id": "existing-agent-object",
            "agent365_agent_user_id": "existing-agent-user",
            "private_mcp_api_audience": "api://incidents", "public_shipments_mcp_api_audience": "api://shipments",
            "user_scheduling_enabled": True, "document_retry_enabled": True,
            "servicebus_dream_enabled": True, "servicebus_dream_cron_expression": "15 3 * * *",
            "scheduled_learning_focus": "review delivery handoffs",
            "scheduled_learning_max_records": 2, "scheduled_learning_retry_limit": 4,
            "scheduled_learning_retry_backoff_seconds": 15,
            "user_scheduling_lock_renewal_seconds": 1800, "user_scheduling_max_delivery_count": 5,
            "hermes_role_blueprint": "junior-project-manager",
            "worker_owner_id": "owner-object",
            "collective_learning_operator_client_id": "operator-api-client",
        })
        return values

    def infrastructure(self):
        return {
            "sandbox_groups": {role: self.group(role) for role in ("runtime", "gateway", "private-mcp", "public-mcp", "generated-apps")},
            "tenant_id": "tenant", "subscription_id": "subscription",
            "resource_group_name": "resources", "sandbox_location": "swedencentral",
            "scheduler_servicebus_queue_name": "worker-schedule",
            "foundry_agent_name": "autopilots-hermes",
            "transport": {
                "connector_principal_id": "namespace-mi",
                "connection_ids": {"gateway": "/connections/gateway"},
                "namespace": "schedule.servicebus.windows.net",
            },
        }

    def test_gateway_uses_memory_suspend_and_native_on_demand_ingress(self):
        service = SandboxService("gateway", "image", 8000, ("python3",), {}, release=self.release)
        body = service_body(service, self.group(), "disk", "worker")
        self.assertTrue(body["lifecycle"]["autoSuspendPolicy"]["enabled"])
        self.assertEqual(body["lifecycle"]["autoSuspendPolicy"]["mode"], "Memory")
        self.assertEqual(body["lifecycle"]["autoSuspendPolicy"]["interval"], 300)
        self.assertEqual(body["ports"][0]["auth"], {"anonymous": True})
        self.assertEqual(body["ports"][0]["activationMode"], "OnDemand")
        self.assertEqual(body["customerVnetConnectionName"], "private-tools")
        self.assertEqual(body["entrypoint"], ["python3", "-c", BOOTSTRAP])
        self.assertTrue(all(len(value) <= 63 for value in body["labels"].values()))
        self.assertEqual(body["resources"], {"cpu": "500m", "memory": "1024Mi", "disk": "10Gi"})
        self.assertNotIn("keepalive", BOOTSTRAP)
        self.assertIn('open("/app/.sandbox-service.log", "a")', BOOTSTRAP)
        self.assertIn("stderr=subprocess.STDOUT", BOOTSTRAP)
        self.assertIn("subprocess.Popen", BOOTSTRAP)

    def test_native_callback_port_is_reconciled_without_replacing_public_ingress(self):
        sandbox = Mock()
        sandbox._sbx_path = "/sandbox"
        sandbox.sandbox_id = "sandbox"
        public = {
            "port": 8000, "url": "https://sandbox--8000.swedencentral.adcproxy.io",
            "auth": {"anonymous": True},
        }
        native = {
            "port": 8001, "protocol": "Http", "activationMode": "OnDemand",
            "url": "https://sandbox--8001.swedencentral.adcproxy.io",
            "auth": {"anonymous": False, "entraId": {
                "enabled": True, "tenantIds": ["tenant"], "objectIds": ["namespace-mi"],
            }},
        }
        sandbox._dp_get.side_effect = [
            {"ports": [public]}, {"ports": [public, {
                **native,
                "auth": {**native["auth"], "entraId": {**native["auth"]["entraId"], "emails": []}},
            }]},
        ]
        result = configure_callback_port(sandbox, {
            "TRANSPORT_CALLBACK_PRINCIPAL_ID": "namespace-mi", "TRANSPORT_CALLBACK_TENANT_ID": "tenant",
        })
        self.assertEqual(result, "https://sandbox--8001.swedencentral.adcproxy.io")
        sandbox._dp_put.assert_called_once_with("/sandbox/ports", {"ports": [public, native]})
        sandbox.begin_create_sandbox.assert_not_called()

    def test_native_callback_port_readback_cannot_silently_allow_another_principal(self):
        sandbox = Mock()
        sandbox._sbx_path = "/sandbox"
        sandbox.sandbox_id = "sandbox"
        sandbox._dp_get.return_value = {"ports": [
            {"port": 8000, "url": "https://sandbox--8000.swedencentral.adcproxy.io", "auth": {"anonymous": True}},
            {"port": 8001, "auth": {"anonymous": True}},
        ]}
        with self.assertRaisesRegex(RuntimeError, "namespace MI"):
            configure_callback_port(sandbox, {
                "TRANSPORT_CALLBACK_PRINCIPAL_ID": "namespace-mi", "TRANSPORT_CALLBACK_TENANT_ID": "tenant",
            })

    def test_native_callback_port_does_not_guess_a_url_for_unknown_public_ingress(self):
        sandbox = Mock()
        sandbox._sbx_path = "/sandbox"
        sandbox.sandbox_id = "sandbox"
        sandbox._dp_get.return_value = {"ports": [{"port": 8000, "url": "https://custom.example"}]}
        with self.assertRaisesRegex(ValueError, "native Sandbox port URL"):
            configure_callback_port(sandbox, {
                "TRANSPORT_CALLBACK_PRINCIPAL_ID": "namespace-mi", "TRANSPORT_CALLBACK_TENANT_ID": "tenant",
            })
        sandbox._dp_put.assert_not_called()

    def test_private_and_public_mcp_keep_distinct_authorization(self):
        private = mcp_service("private-mcp", self.config(), self.infrastructure())
        public = mcp_service("public-mcp", self.config(), self.infrastructure())
        self.assertEqual(private.environment["MCP_ALLOWED_OBJECT_IDS"], "existing-agent-object")
        self.assertEqual(private.environment["MCP_JWT_AUDIENCE"], "incidents")
        self.assertEqual(public.environment["MCP_JWT_AUDIENCE"], "shipments")
        self.assertEqual(public.environment["MCP_ALLOWED_ROLES"], "Shipments.Read.All")
        self.assertNotIn("MCP_ALLOWED_OBJECT_IDS", public.environment)

    def test_suspended_mcp_ingress_wakes_instead_of_rejecting_requests(self):
        service = mcp_service("private-mcp", self.config(), self.infrastructure())
        body = service_body(service, self.group("private-mcp"), "disk", "worker")
        self.assertTrue(body["lifecycle"]["autoSuspendPolicy"]["enabled"])
        self.assertEqual(body["ports"][0]["activationMode"], "OnDemand")

    def test_gateway_receives_messaging_settings_and_no_runtime_provisioning(self):
        settings = worker_settings(
            self.config(),
            {"foundry_openai_base_url": "https://foundry/openai/v1", "model_deployment_name": "model",
             "acr_name": "registry", "scheduler_servicebus_fully_qualified_namespace": "schedule.servicebus.windows.net",
             "application_insights_connection_string": "instrumentation-identifier",
             "foundry_project_endpoint": "https://foundry/api/projects/project"},
            self.infrastructure(),
            {"private_mcp_url": "https://private/mcp", "public_shipments_mcp_url": "https://public/mcp",
             "runtime_disk_image_id": "runtime-disk"},
        )
        self.assertEqual(settings["AZURE_SANDBOX_GROUP"], "worker-runtime")
        self.assertEqual(settings["AGENT_RUNTIME_MANAGED_IDENTITY_CLIENT_ID"], "runtime-client")
        self.assertEqual(settings["AGENT_RUNTIME_DISK_IMAGE_ID"], "runtime-disk")
        self.assertNotIn("RUNTIME_CONFIG_REVISION", settings)
        env = gateway_environment(settings)
        for runtime_only in ("AZURE_SANDBOX_GROUP", "AZURE_REGION", "AGENT_RUNTIME_MANAGED_IDENTITY_CLIENT_ID",
                             "AGENT_RUNTIME_DISK_IMAGE_ID", "AGENT_RUNTIME_IMAGE", "PREVIOUS_API_SERVER_KEY",
                             "FOUNDRY_OPENAI_BASE_URL", "PRIVATE_INCIDENTS_MCP_URL"):
            self.assertNotIn(runtime_only, env)
        self.assertFalse(any(key.startswith("WORKIQ_") for key in env))
        self.assertEqual(env["GENERATED_APPS_SANDBOX_GROUP"], "worker-generated-apps")
        self.assertEqual(env["AZURE_CLIENT_ID"], "gateway-client")
        self.assertEqual(env["TRANSPORT_CALLBACK_PRINCIPAL_ID"], "namespace-mi")
        self.assertEqual(env["TRANSPORT_CALLBACK_TENANT_ID"], "tenant")
        self.assertEqual(env["WORKER_OWNER_ID"], "owner-object")
        self.assertEqual(env["COLLECTIVE_LEARNING_OPERATOR_CLIENT_ID"], "operator-api-client")
        self.assertNotIn("ACR_NAME", env)
        self.assertNotIn("AGENT_RUNTIME_REGISTRY_IDENTITY_RESOURCE_ID", env)
        self.assertEqual(env["CONNECTIONS__SERVICE_CONNECTION__SETTINGS__AUTHTYPE"], "FederatedCredentials")
        self.assertEqual(env["CONNECTIONS__SERVICE_CONNECTION__SETTINGS__FEDERATEDCLIENTID"], "gateway-client")
        self.assertEqual(env["AGENT365_BLUEPRINT_CLIENT_ID"], "existing-blueprint")
        self.assertEqual(env["APPLICATIONINSIGHTS_CONNECTION_STRING"], "instrumentation-identifier")
        self.assertEqual(env["FOUNDRY_AGENT_NAME"], "autopilots-hermes")
        self.assertEqual(env["OTEL_CONTAINER_IMAGE"], PYTHON_BASE_IMAGE)
        self.assertEqual(env["OTEL_SERVICE_VERSION"], PYTHON_BASE_IMAGE.split("@")[-1])
        self.assertEqual(env["SERVICEBUS_DREAM_ENABLED"], "true")
        self.assertEqual(env["SERVICEBUS_DREAM_CRON_EXPRESSION"], "15 3 * * *")
        self.assertEqual(env["SCHEDULED_LEARNING_FOCUS"], "review delivery handoffs")
        self.assertEqual(env["SCHEDULED_LEARNING_MAX_RECORDS"], "2")
        self.assertNotIn("SCHEDULED_LEARNING_PREPARE_PACKET", env)
        self.assertFalse(any("CLIENTSECRET" in key or "REGISTRY_PASSWORD" in key for key in env))
        self.assertNotIn("GENERATED_CHAT_FILES_ENABLED", env)

    def test_runtime_telemetry_uses_runtime_image_not_gateway_image(self):
        config = AgentSandboxConfig("sub", "rg", "group", "region", "registry/runtime:build123",
                                    runtime_image_reference="registry/runtime@sha256:123")
        with patch.dict(os.environ, {"APPLICATIONINSIGHTS_CONNECTION_STRING": "operator-shell"}, clear=True):
            environment = runtime_telemetry_environment(config, {
                "APPLICATIONINSIGHTS_CONNECTION_STRING": "instrumentation-identifier",
                "FOUNDRY_AGENT_NAME": "autopilots-hermes",
                "OTEL_CONTAINER_IMAGE": "registry/bridge@sha256:abc",
                "OTEL_SERVICE_VERSION": "sha256:abc",
            })
        self.assertEqual(environment["APPLICATIONINSIGHTS_CONNECTION_STRING"], "instrumentation-identifier")
        self.assertEqual(environment["FOUNDRY_AGENT_NAME"], "autopilots-hermes")
        self.assertEqual(environment["OTEL_CONTAINER_IMAGE"], "registry/runtime@sha256:123")
        self.assertEqual(environment["OTEL_SERVICE_VERSION"], "sha256:123")

    def test_runtime_environment_uses_prepared_disk_and_digest_not_conversion_source(self):
        values = {
            "AGENT_RUNTIME_IMAGE": "registry/runtime@sha256:123",
            "AGENT_RUNTIME_DISK_SOURCE_IMAGE": "registry/runtime:build123",
            "AGENT_RUNTIME_DISK_IMAGE_ID": "runtime-disk",
        }
        config = config_from_environment(
            values, subscription_id="sub", resource_group="rg", sandbox_group="group", region="region",
            api_server_key="runtime-test-key",
        )
        self.assertEqual(config.image_name, "registry/runtime@sha256:123")
        self.assertEqual(config.disk_image_id, "runtime-disk")
        self.assertEqual(config.runtime_image_reference, "registry/runtime@sha256:123")
        self.assertEqual(config.environment["OTEL_CONTAINER_IMAGE"], "registry/runtime@sha256:123")

    def test_private_ingress_must_be_disabled_before_deploy(self):
        infra = self.infrastructure()
        infra.update({"deployment_config": self.config(), "private_ingress": {"publicNetworkAccess": "Enabled"}})
        with self.assertRaisesRegex(RuntimeError, "disabled public ingress"):
            deploy_sandbox_services(infra, {})

    def test_runtime_is_deployed_with_gateway_url_and_verified_through_ondemand_ingress(self):
        runtime = SimpleNamespace(sandbox_id="runtime-1", endpoint_url="https://runtime.example",
                                  reused_existing_sandbox=False)
        settings = {"AZURE_SUBSCRIPTION_ID": "sub", "AZURE_RESOURCE_GROUP": "rg", "AZURE_SANDBOX_GROUP": "runtime",
                    "AZURE_REGION": "swedencentral", "API_SERVER_KEY": "key", "AGENT_RUNTIME_DISK_IMAGE_ID": "disk",
                    "GENERATED_CHAT_FILES_ENABLED": "true"}
        with (
            patch("scripts.sandbox_services.deploy_agent_sandbox", return_value=runtime) as deploy,
            patch("scripts.sandbox_services.require_runtime_ready", return_value=9.5) as ready,
            patch("scripts.sandbox_services.require_service_health"),
            patch("scripts.sandbox_services.SandboxGroupClient") as group_factory,
        ):
            group = group_factory.return_value.__enter__.return_value
            group.get_sandbox_client.return_value.exec.return_value = SimpleNamespace(exit_code=0, stderr="")
            result = deploy_runtime(settings, "https://gateway.example", "credential")
        config = deploy.call_args.args[0]
        self.assertEqual(config.environment["AUTOPILOT_BRIDGE_URL"], "https://gateway.example")
        self.assertEqual(config.environment["GENERATED_CHAT_FILES_ENABLED"], "true")
        self.assertEqual(gateway_environment(settings)["GENERATED_CHAT_FILES_ENABLED"], "true")
        self.assertEqual(deploy.call_args.kwargs, {"credential": "credential"})
        ready.assert_called_once_with("https://runtime.example")
        self.assertEqual(result, {
            "runtime_url": "https://runtime.example", "runtime_sandbox_id": "runtime-1",
            "runtime_application_release": self.release.digest,
        })

    def test_teardown_drains_owned_instances_before_native_connection_deletion(self):
        group = {"name": "owned", "id": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.App/sandboxGroups/owned"}
        labels = {"app": "autopilots-on-azure", "worker": "agent-1"}
        with patch("scripts.sandbox_services.AzureCliCredential"), patch(
            "scripts.sandbox_services.SandboxGroupClient",
        ) as factory:
            client = factory.return_value.__enter__.return_value
            client._group_path = "/owned"
            client._dp_get.side_effect = [[{"id": "instance", "labels": labels}], []]
            delete_owned_sandboxes([group], labels, subscription_id="sub", resource_group="rg", location="region")
            client.get_sandbox_client.assert_called_once_with("instance")
            client.get_sandbox_client.return_value.begin_delete.return_value.result.assert_called_once()

    def test_teardown_rejects_unowned_instances_and_out_of_scope_groups(self):
        group = {"name": "owned", "id": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.App/sandboxGroups/owned"}
        with patch("scripts.sandbox_services.AzureCliCredential"), patch(
            "scripts.sandbox_services.SandboxGroupClient",
        ) as factory:
            client = factory.return_value.__enter__.return_value
            client._group_path = "/owned"
            client._dp_get.return_value = [{"id": "unowned", "labels": {"worker": "someone-else"}}]
            with self.assertRaisesRegex(ValueError, "ownership"):
                delete_owned_sandboxes([group], {"worker": "agent-1"},
                                      subscription_id="sub", resource_group="rg", location="region")
            client.get_sandbox_client.assert_not_called()
            with self.assertRaisesRegex(ValueError, "resource group"):
                delete_owned_sandboxes([{**group, "id": group["id"].replace("/rg/", "/unrelated/")}],
                                      {"worker": "agent-1"}, subscription_id="sub",
                                      resource_group="rg", location="region")
    def test_gateway_settings_receive_runtime_url_derived_from_gateway_url(self):
        service = SandboxService("gateway", "image@sha256:123", 8000, ("python3",), {}, release=self.release)
        client = Mock()
        client.list_volumes.return_value = []
        client._group_path = "/group"
        client._dp_get.return_value = []
        client._dp_put.return_value = {"id": "sandbox"}
        sandbox = client.get_sandbox_client.return_value
        sandbox.sandbox_id = "sandbox"
        sandbox.get.return_value = SimpleNamespace(ports=[SimpleNamespace(port=8000, url="https://gateway.example/")])
        sandbox.exec.return_value = SimpleNamespace(exit_code=0, stderr="")
        seen = []

        def endpoint_environment(url):
            seen.append(url)
            return {"AGENT_RUNTIME_URL": "https://runtime.example"}

        with patch("scripts.sandbox_services.require_service_health"):
            deploy_service(client, service, self.group(), "worker", disk_image_id="disk",
                           endpoint_environment=endpoint_environment)
        written = json.loads(sandbox.write_file.call_args.args[1])["environment"]
        self.assertEqual(seen, ["https://gateway.example"])
        self.assertEqual(written["AGENT_RUNTIME_URL"], "https://runtime.example")
        self.assertEqual(written["AUTOPILOT_BRIDGE_URL"], "https://gateway.example")

    def test_prebuilt_replacement_detaches_disk_before_create_and_never_restarts_deleted_compute(self):
        for healthy in (True, False):
            with self.subTest(healthy=healthy):
                service = SandboxService(
                    "gateway", "new-image", 8001, ("python3",), {}, source_commit="a" * 40,
                )
                client = Mock()
                client._group_path = "/group"
                client.list_volumes.return_value = [SimpleNamespace(
                    name="gateway-state", type="DataDisk",
                    labels={"app": "autopilots-on-azure", "worker": "worker"},
                )]
                client._dp_get.return_value = [{
                    "id": "old", "labels": {"worker": "worker", "service": "gateway", "image": "old-image"},
                }]
                old, new = Mock(), Mock()
                old.get.return_value = SimpleNamespace(state="Stopped")
                new.sandbox_id = "new"
                new.get.return_value = SimpleNamespace(
                    state="Running", ports=[SimpleNamespace(port=8001, url="https://gateway.example")],
                )
                new.exec.return_value = SimpleNamespace(exit_code=0, stderr="")
                client.get_sandbox_client.side_effect = lambda identity: old if identity == "old" else new
                order = []
                old.begin_delete.return_value.result.side_effect = lambda: order.append("delete")
                client.get_volume.side_effect = lambda name: (order.append("detach"), SimpleNamespace(is_attached=False))[1]
                client._dp_put.side_effect = lambda path, body: (order.append("create"), {"id": "new"})[1]
                with patch("scripts.sandbox_services.require_always_running"), patch(
                    "scripts.sandbox_services.require_service_health",
                    side_effect=None if healthy else TimeoutError("not healthy"),
                ):
                    if healthy:
                        deploy_service(client, service, self.group(), "worker", disk_image_id="disk")
                    else:
                        with self.assertRaises(TimeoutError):
                            deploy_service(client, service, self.group(), "worker", disk_image_id="disk")
                self.assertEqual(order, ["delete", "detach", "create"])
                old.ensure_running.assert_not_called()
                client.delete_volume.assert_not_called()
                self.assertEqual(new.begin_delete.call_count, 0 if healthy else 1)

    def test_native_url_supplied_before_gateway_process_starts(self):
        service = SandboxService("gateway", "image@sha256:123", 8000, ("python3", "-m", "server"), {}, False,
                                 disk_source_image="image:build123", release=self.release)
        client = Mock()
        client.list_volumes.return_value = []
        client._group_path = "/group"
        client._dp_get.return_value = []
        client.list_disk_images.return_value = []
        client.begin_create_disk_image.return_value.result.return_value = SimpleNamespace(id="disk")
        client._dp_put.return_value = {"id": "sandbox"}
        sandbox = client.get_sandbox_client.return_value
        sandbox.sandbox_id = "sandbox"
        sandbox.get.return_value = SimpleNamespace(ports=[SimpleNamespace(port=8000, url="https://native.adcproxy.io")])
        sandbox.exec.return_value = SimpleNamespace(exit_code=0, stderr="")
        with patch("scripts.sandbox_services.require_service_health") as health:
            result = deploy_service(client, service, self.group(), "worker", disk_image_id="disk")
        written_path, written = sandbox.write_file.call_args.args
        self.assertEqual(written_path, SERVICE_CONFIG_PATH + ".next")
        self.assertEqual(json.loads(written)["environment"]["AUTOPILOT_BRIDGE_URL"], result["url"])
        client.begin_create_disk_image.assert_not_called()
        self.assertEqual(client._dp_put.call_args.args[1]["sourcesRef"]["diskImage"]["id"], "disk")
        self.assertEqual(result["image"], "image@sha256:123")
        health.assert_called_once_with(sandbox, 8000, self.release.digest)

    def test_failed_configuration_update_restores_previous_settings(self):
        service = SandboxService("gateway", "image", 8000, ("python3",), {"RELEASE": "new"}, release=self.release)
        client = Mock()
        client.list_volumes.return_value = []
        client._group_path = "/group"
        client._dp_get.return_value = [{
            "id": "sandbox", "labels": service_body(service, self.group(), "disk", "worker")["labels"],
        }]
        sandbox = client.get_sandbox_client.return_value
        sandbox.get.return_value = SimpleNamespace(
            state="Running", ports=[SimpleNamespace(port=8000, url="https://native.adcproxy.io")]
        )
        sandbox.exec.return_value = SimpleNamespace(exit_code=0, stderr="")
        previous = {
            "environment": {"RELEASE": "old", "TRANSPORT_CONNECTION_ID": "/connections/gateway"},
            "command": ["python3"],
        }
        sandbox.read_file.return_value = json.dumps(previous)
        with patch("scripts.sandbox_services.require_service_health", side_effect=TimeoutError("unhealthy")):
            with self.assertRaisesRegex(TimeoutError, "unhealthy"):
                deploy_service(client, service, self.group(), "worker", disk_image_id="disk")
        self.assertEqual(json.loads(sandbox.write_file.call_args.args[1]), previous)
        sandbox.begin_stop.assert_not_called()
        sandbox.begin_delete.assert_not_called()

    def test_changed_bootstrap_replaces_stopped_service_without_stopping_it_again(self):
        service = SandboxService("private-mcp", "image", 8765, ("python3",), {}, release=self.release)
        client = Mock()
        client._group_path = "/group"
        client._dp_get.return_value = [{
            "id": "old", "labels": {"worker": "worker", "service": "private-mcp",
                                  "image": hashlib.sha256(b"image").hexdigest()[:32]},
        }]
        old, new = Mock(), Mock()
        old.get.return_value = SimpleNamespace(state="Stopped")
        new.sandbox_id = "new"
        new.get.return_value = SimpleNamespace(ports=[SimpleNamespace(port=8765, url="https://native.adcproxy.io")])
        new.exec.return_value = SimpleNamespace(exit_code=0, stderr="")
        client.get_sandbox_client.side_effect = lambda identifier: old if identifier == "old" else new
        client._dp_put.return_value = {"id": "new"}
        with patch("scripts.sandbox_services.require_service_health"):
            result = deploy_service(client, service, self.group("private-mcp"), "worker", disk_image_id="disk")
        self.assertEqual(result["sandbox_id"], "new")
        old.begin_stop.assert_not_called()
        old.begin_delete.assert_not_called()
        self.assertEqual(result["superseded_sandbox_ids"], ["old"])
        self.assertIn("deployment", client._dp_put.call_args.args[1]["labels"])

    def test_failed_service_is_replaced_even_when_deployment_labels_match(self):
        for matching_labels in (True, False):
            for healthy_replacement in (True, False):
                with self.subTest(matching_labels=matching_labels, healthy_replacement=healthy_replacement):
                    service = SandboxService("private-mcp", "image", 8765, ("python3",), {}, release=self.release)
                    client = Mock()
                    client._group_path = "/group"
                    labels = service_body(service, self.group("private-mcp"), "disk", "worker")["labels"]
                    if not matching_labels:
                        labels["deployment"] = "previous-release"
                    client._dp_get.return_value = [{"id": "failed", "labels": labels}]
                    old, new = Mock(), Mock()
                    old.get.return_value = SimpleNamespace(state="Failed")
                    new.sandbox_id = "replacement"
                    new.get.return_value = SimpleNamespace(
                        state="Running", ports=[SimpleNamespace(port=8765, url="https://native.adcproxy.io")]
                    )
                    new.exec.return_value = SimpleNamespace(exit_code=0, stderr="")
                    client.get_sandbox_client.side_effect = lambda identifier: old if identifier == "failed" else new
                    client._dp_put.return_value = {"id": "replacement"}
                    with patch("scripts.sandbox_services.require_service_health") as health:
                        if not healthy_replacement:
                            health.side_effect = TimeoutError("unhealthy")
                            with self.assertRaisesRegex(TimeoutError, "unhealthy"):
                                deploy_service(client, service, self.group("private-mcp"), "worker", disk_image_id="disk")
                            old.begin_delete.assert_not_called()
                            new.begin_delete.assert_called_once()
                        else:
                            result = deploy_service(
                                client, service, self.group("private-mcp"), "worker", disk_image_id="disk"
                            )
                            self.assertEqual(result["sandbox_id"], "replacement")
                            old.begin_delete.assert_not_called()
                            self.assertEqual(result["superseded_sandbox_ids"], ["failed"])
                            new.begin_delete.assert_not_called()
                    client._dp_put.assert_called_once()
                    old.ensure_running.assert_not_called()
                    old.begin_stop.assert_not_called()

    def test_current_stopped_or_suspended_service_resumes_without_replacement(self):
        for state in ("Stopped", "Suspended"):
            with self.subTest(state=state):
                service = SandboxService("private-mcp", "image", 8765, ("python3",), {}, release=self.release)
                client = Mock()
                client._group_path = "/group"
                client._dp_get.return_value = [{
                    "id": "existing",
                    "labels": service_body(service, self.group("private-mcp"), "disk", "worker")["labels"],
                }]
                sandbox = client.get_sandbox_client.return_value
                sandbox.sandbox_id = "existing"
                sandbox.get.return_value = SimpleNamespace(
                    state=state, ports=[SimpleNamespace(port=8765, url="https://native.adcproxy.io")]
                )
                sandbox.read_file.return_value = json.dumps({
                    "environment": {
                        "APPLICATION_RELEASE": self.release.digest,
                        "OTEL_SERVICE_VERSION": self.release.digest,
                    },
                    "command": [self.release.directory + "/.venv/bin/python"],
                    "workingDirectory": self.release.directory,
                    "release": self.release.digest,
                })
                sandbox.exec.return_value = SimpleNamespace(exit_code=0, stderr="")
                with patch("scripts.sandbox_services.require_service_health"):
                    result = deploy_service(client, service, self.group("private-mcp"), "worker", disk_image_id="disk")
                self.assertEqual(result["sandbox_id"], "existing")
                sandbox.ensure_running.assert_called_once_with(timeout=600)
                sandbox.begin_stop.assert_not_called()
                sandbox.begin_delete.assert_not_called()
                sandbox.write_file.assert_not_called()
                client._dp_put.assert_not_called()

    def test_conversion_uses_group_managed_identity_without_registry_credentials(self):
        client = Mock()
        client._endpoint = "https://adc"
        client._group_path = "/groups/runtime"
        client.list_disk_images.return_value = []
        client._send_request.return_value.json.side_effect = [
            {"operationId": "op-1"},
            {"status": "Running"},
            {"status": "Succeeded", "diskImage": {"id": "disk"}},
        ]
        client.get_disk_image.return_value = SimpleNamespace(id="disk", status=SimpleNamespace(state="Ready"))
        with (
            patch("scripts.sandbox_services.run_text", return_value="sha256:123") as run,
            patch("scripts.sandbox_services.time.sleep"),
        ):
            result = prepare_disk_image(
                client, role="runtime", image="registry.azurecr.io/runtime@sha256:123",
                source="registry.azurecr.io/runtime:build123", acr_name="registry", subscription_id="sub",
                identity_client_id="runtime-client",
            )
        self.assertEqual(result, "disk")
        self.assertEqual(run.call_count, 1)
        self.assertNotIn("--expose-token", run.call_args.args[0])
        requests = [call.args[0] for call in client._send_request.call_args_list]
        self.assertEqual((requests[0].method, requests[0].url), ("PUT", "https://adc/groups/runtime/diskimages/v2/async"))
        name = "runtime-" + hashlib.sha256(b"registry.azurecr.io/runtime@sha256:123").hexdigest()[:20]
        self.assertEqual(json.loads(requests[0].content), {
            "source": {"kind": "registry", "imageUrl": "registry.azurecr.io/runtime:build123",
                       "managedIdentityClientId": "runtime-client"},
            "labels": {"name": name},
        })
        self.assertEqual(requests[1].url, "https://adc/groups/runtime/diskimages/operations/op-1")
        self.assertTrue(all("api-version" not in request.url for request in requests))
        client.begin_create_disk_image.assert_not_called()

    def test_failed_conversion_reports_service_error(self):
        client = Mock()
        client._endpoint = "https://adc"
        client._group_path = "/groups/runtime"
        client._send_request.return_value.json.side_effect = [
            {"operationId": "op-1"},
            {"status": "Failed", "errorMessage": "UNAUTHORIZED: authentication required"},
        ]
        with self.assertRaisesRegex(RuntimeError, "UNAUTHORIZED.*before retrying"):
            convert_disk_image(client, {"source": {}})

    def test_existing_ready_disk_needs_no_registry_access(self):
        client = Mock()
        image = "registry.azurecr.io/runtime@sha256:123"
        name = "runtime-" + hashlib.sha256(image.encode()).hexdigest()[:20]
        client.list_disk_images.return_value = [
            SimpleNamespace(id="disk", name=name, status=SimpleNamespace(state="Ready"))
        ]
        with patch("scripts.sandbox_services.run_text") as run:
            result = prepare_disk_image(
                client, role="runtime", image=image, source="registry.azurecr.io/runtime:build123",
                acr_name="registry", subscription_id="sub", identity_client_id="runtime-client",
            )
        self.assertEqual(result, "disk")
        run.assert_not_called()
        client._send_request.assert_not_called()

    def test_conversion_rejects_retagged_source_before_converting(self):
        client = Mock()
        client.list_disk_images.return_value = []
        with patch("scripts.sandbox_services.run_text", return_value="sha256:unexpected") as run:
            with self.assertRaisesRegex(ValueError, "does not match"):
                prepare_disk_image(
                    client, role="runtime", image="registry.azurecr.io/runtime@sha256:123",
                    source="registry.azurecr.io/runtime:build123", acr_name="registry", subscription_id="sub",
                    identity_client_id="runtime-client",
                )
        self.assertEqual(run.call_count, 1)
        client._send_request.assert_not_called()

    def test_missing_runtime_disk_fails_before_lifecycle_changes(self):
        config = AgentSandboxConfig("sub", "rg", "group", "region", "image")
        with patch("scripts.sandbox_runtime.create_sandbox_group_client") as create:
            from scripts.sandbox_runtime import deploy_agent_sandbox

            with self.assertRaisesRegex(ValueError, "AGENT_RUNTIME_DISK_IMAGE_ID"):
                deploy_agent_sandbox(config)
        create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
