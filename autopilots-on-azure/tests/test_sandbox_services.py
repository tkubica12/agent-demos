from __future__ import annotations

import hashlib
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts.sandbox_services import (
    BOOTSTRAP,
    SERVICE_CONFIG_PATH,
    SandboxService,
    deploy_service,
    deploy_sandbox_services,
    gateway_environment,
    mcp_service,
    prepare_disk_image,
    service_body,
)
from scripts.sandbox_runtime import AgentSandboxConfig, config_from_environment, runtime_telemetry_environment


class SandboxServicesTests(unittest.TestCase):
    def group(self, role="gateway"):
        return {
            "name": f"worker-{role}", "identity_client_id": f"{role}-client",
            "identity_resource_id": f"/identities/{role}",
            "vnet_connection_name": "private-tools" if role in {"runtime", "gateway"} else "",
        }

    def config(self):
        values = dict.fromkeys([
            "runtime_disk_source_image", "openclaw_gateway_token", "openclaw_bridge_device_token",
            "openclaw_bridge_device_private_key_pem", "previous_api_server_key",
            "agent365_agent_user_principal_name", "worker_assignment_scope",
        ], "")
        values.update({
            "agent_runtime": "hermes", "autopilot_name": "worker",
            "runtime_image": "registry/hermes@sha256:123",
            "bridge_image": "registry/bridge@sha256:abc",
            "bridge_disk_source_image": "registry/bridge:build123",
            "runtime_disk_image_name": "hermes-release", "runtime_data_volume_name": "worker-data",
            "private_mcp_image": "registry/private@sha256:456",
            "private_mcp_disk_source_image": "registry/private:build123",
            "public_shipments_mcp_image": "registry/public@sha256:789",
            "public_shipments_mcp_disk_source_image": "registry/public:build123",
            "api_server_key": "runtime-test-key", "agent365_tenant_id": "tenant",
            "agent365_client_id": "existing-blueprint",
            "agent365_agent_identity_client_id": "existing-agent-client",
            "agent365_agent_identity_object_id": "existing-agent-object",
            "agent365_agent_user_id": "existing-agent-user",
            "private_mcp_api_audience": "api://incidents", "public_shipments_mcp_api_audience": "api://shipments",
            "user_scheduling_enabled": True, "document_retry_enabled": True,
            "user_scheduling_lock_renewal_seconds": 1800, "user_scheduling_max_delivery_count": 5,
            "hermes_role_blueprint": "junior-project-manager",
        })
        return values

    def infrastructure(self):
        return {
            "sandbox_groups": {role: self.group(role) for role in ("runtime", "gateway", "private-mcp", "public-mcp", "generated-apps")},
            "tenant_id": "tenant", "subscription_id": "subscription",
            "resource_group_name": "resources", "sandbox_location": "swedencentral",
            "scheduler_servicebus_queue_name": "worker-schedule",
            "foundry_agent_name": "autopilots-hermes",
        }

    def test_gateway_is_awake_with_native_anonymous_ingress(self):
        service = SandboxService("gateway", "image", 8000, ("python3",), {}, auto_suspend=False)
        body = service_body(service, self.group(), "disk", "worker")
        self.assertFalse(body["lifecycle"]["autoSuspendPolicy"]["enabled"])
        self.assertEqual(body["ports"][0]["auth"], {"anonymous": True})
        self.assertEqual(body["customerVnetConnectionName"], "private-tools")
        self.assertEqual(body["entrypoint"], ["python3", "-c", BOOTSTRAP])
        self.assertTrue(all(len(value) <= 63 for value in body["labels"].values()))
        self.assertEqual(body["resources"], {"cpu": "500m", "memory": "1024Mi", "disk": "10Gi"})
        self.assertNotIn("keepalive", BOOTSTRAP)
        self.assertIn('os.open("/app/.sandbox-service.log"', BOOTSTRAP)
        self.assertIn("os.dup2(log, 2)", BOOTSTRAP)

    def test_private_and_public_mcp_keep_distinct_authorization(self):
        private = mcp_service("private-mcp", self.config(), self.infrastructure())
        public = mcp_service("public-mcp", self.config(), self.infrastructure())
        self.assertEqual(private.environment["MCP_ALLOWED_OBJECT_IDS"], "existing-agent-object")
        self.assertEqual(private.environment["MCP_JWT_AUDIENCE"], "incidents")
        self.assertEqual(public.environment["MCP_JWT_AUDIENCE"], "shipments")
        self.assertEqual(public.environment["MCP_ALLOWED_ROLES"], "Shipments.Read.All")
        self.assertNotIn("MCP_ALLOWED_OBJECT_IDS", public.environment)

    def test_gateway_uses_federation_and_worker_runtime_group(self):
        env = gateway_environment(
            self.config(),
            {"foundry_openai_base_url": "https://foundry/openai/v1", "model_deployment_name": "model",
             "acr_name": "registry", "scheduler_servicebus_fully_qualified_namespace": "schedule.servicebus.windows.net",
             "application_insights_connection_string": "instrumentation-identifier",
             "foundry_project_endpoint": "https://foundry/api/projects/project"},
            self.infrastructure(),
            {"private_mcp_url": "https://private/mcp", "public_shipments_mcp_url": "https://public/mcp",
             "runtime_disk_image_id": "runtime-disk"},
        )
        self.assertEqual(env["AZURE_SANDBOX_GROUP"], "worker-runtime")
        self.assertEqual(env["AZURE_CLIENT_ID"], "gateway-client")
        self.assertEqual(env["AGENT_RUNTIME_MANAGED_IDENTITY_CLIENT_ID"], "runtime-client")
        self.assertEqual(env["AGENT_RUNTIME_DISK_IMAGE_ID"], "runtime-disk")
        self.assertNotIn("ACR_NAME", env)
        self.assertNotIn("AGENT_RUNTIME_REGISTRY_IDENTITY_RESOURCE_ID", env)
        self.assertEqual(env["CONNECTIONS__SERVICE_CONNECTION__SETTINGS__AUTHTYPE"], "FederatedCredentials")
        self.assertEqual(env["CONNECTIONS__SERVICE_CONNECTION__SETTINGS__FEDERATEDCLIENTID"], "gateway-client")
        self.assertEqual(env["AGENT365_BLUEPRINT_CLIENT_ID"], "existing-blueprint")
        self.assertEqual(env["APPLICATIONINSIGHTS_CONNECTION_STRING"], "instrumentation-identifier")
        self.assertEqual(env["FOUNDRY_AGENT_NAME"], "autopilots-hermes")
        self.assertEqual(env["OTEL_CONTAINER_IMAGE"], "registry/bridge@sha256:abc")
        self.assertEqual(env["OTEL_SERVICE_VERSION"], "sha256:abc")
        self.assertFalse(any("CLIENTSECRET" in key or "REGISTRY_PASSWORD" in key for key in env))

    def test_runtime_telemetry_uses_runtime_image_not_gateway_image(self):
        config = AgentSandboxConfig("sub", "rg", "group", "region", "registry/runtime:build123",
                                    runtime_image_reference="registry/runtime@sha256:123")
        with patch.dict(os.environ, {
            "APPLICATIONINSIGHTS_CONNECTION_STRING": "instrumentation-identifier",
            "FOUNDRY_AGENT_NAME": "autopilots-hermes",
            "OTEL_CONTAINER_IMAGE": "registry/bridge@sha256:abc",
            "OTEL_SERVICE_VERSION": "sha256:abc",
        }, clear=True):
            environment = runtime_telemetry_environment(config)
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
        with patch("scripts.sandbox_runtime.get_config", side_effect=lambda name, fallback="": values.get(name, fallback)):
            config = config_from_environment(
                subscription_id="sub", resource_group="rg", sandbox_group="group", region="region",
                runtime_kind="hermes", api_server_key="runtime-test-key",
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

    def test_native_url_supplied_before_gateway_process_starts(self):
        service = SandboxService("gateway", "image@sha256:123", 8000, ("python3", "-m", "server"), {}, False,
                                 disk_source_image="image:build123")
        client = Mock()
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
        health.assert_called_once_with(sandbox, 8000)

    def test_failed_configuration_update_restores_previous_settings(self):
        service = SandboxService("gateway", "image", 8000, ("python3",), {"RELEASE": "new"}, False)
        client = Mock()
        client._group_path = "/group"
        client._dp_get.return_value = [{
            "id": "sandbox", "labels": service_body(service, self.group(), "disk", "worker")["labels"],
        }]
        sandbox = client.get_sandbox_client.return_value
        sandbox.get.return_value = SimpleNamespace(
            state="Running", ports=[SimpleNamespace(port=8000, url="https://native.adcproxy.io")]
        )
        sandbox.exec.return_value = SimpleNamespace(exit_code=0, stderr="")
        previous = {"environment": {"RELEASE": "old"}, "command": ["python3"]}
        sandbox.read_file.return_value = json.dumps(previous)
        with patch("scripts.sandbox_services.require_service_health", side_effect=TimeoutError("unhealthy")):
            with self.assertRaisesRegex(TimeoutError, "unhealthy"):
                deploy_service(client, service, self.group(), "worker", disk_image_id="disk")
        self.assertEqual(json.loads(sandbox.write_file.call_args.args[1]), previous)
        self.assertEqual(sandbox.begin_stop.call_count, 2)
        sandbox.begin_delete.assert_not_called()

    def test_changed_bootstrap_replaces_stopped_service_without_stopping_it_again(self):
        service = SandboxService("private-mcp", "image", 8765, ("python3",), {})
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
        old.begin_delete.assert_called_once()
        self.assertIn("deployment", client._dp_put.call_args.args[1]["labels"])

    def test_failed_service_is_replaced_even_when_deployment_labels_match(self):
        for matching_labels in (True, False):
            for healthy_replacement in (True, False):
                with self.subTest(matching_labels=matching_labels, healthy_replacement=healthy_replacement):
                    service = SandboxService("private-mcp", "image", 8765, ("python3",), {})
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
                            old.begin_delete.assert_called_once()
                            new.begin_delete.assert_not_called()
                    client._dp_put.assert_called_once()
                    old.ensure_running.assert_not_called()
                    old.begin_stop.assert_not_called()

    def test_current_stopped_or_suspended_service_resumes_without_replacement(self):
        for state in ("Stopped", "Suspended"):
            with self.subTest(state=state):
                service = SandboxService("private-mcp", "image", 8765, ("python3",), {})
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
                sandbox.read_file.return_value = json.dumps({"environment": {}, "command": ["python3"]})
                with patch("scripts.sandbox_services.require_service_health"):
                    result = deploy_service(client, service, self.group("private-mcp"), "worker", disk_image_id="disk")
                self.assertEqual(result["sandbox_id"], "existing")
                sandbox.ensure_running.assert_called_once_with(timeout=600)
                sandbox.begin_stop.assert_not_called()
                sandbox.begin_delete.assert_not_called()
                sandbox.write_file.assert_not_called()
                client._dp_put.assert_not_called()

    def test_conversion_passes_transient_token_only_to_sdk(self):
        client = Mock()
        client.list_disk_images.return_value = []
        client.begin_create_disk_image.return_value.result.return_value = SimpleNamespace(
            id="disk", status=SimpleNamespace(state="Ready")
        )
        with patch("scripts.sandbox_services.run_text", side_effect=["sha256:123", "short-lived-token"]) as run:
            result = prepare_disk_image(
                client, role="runtime", image="registry.azurecr.io/runtime@sha256:123",
                source="registry.azurecr.io/runtime:build123", acr_name="registry", subscription_id="sub",
            )
        self.assertEqual(result, "disk")
        self.assertIn("--expose-token", run.call_args.args[0])
        credentials = client.begin_create_disk_image.call_args.kwargs["registry_credentials"]
        self.assertEqual(credentials.username, "00000000-0000-0000-0000-000000000000")
        self.assertEqual(credentials.token, "short-lived-token")
        self.assertNotIn("short-lived-token", str(run.call_args_list))
        self.assertNotIn("managed_identity_resource_id", client.begin_create_disk_image.call_args.kwargs)

    def test_existing_ready_disk_needs_no_registry_login(self):
        client = Mock()
        image = "registry.azurecr.io/runtime@sha256:123"
        name = "runtime-" + hashlib.sha256(image.encode()).hexdigest()[:20]
        client.list_disk_images.return_value = [
            SimpleNamespace(id="disk", name=name, status=SimpleNamespace(state="Ready"))
        ]
        with patch("scripts.sandbox_services.run_text") as run:
            result = prepare_disk_image(
                client, role="runtime", image=image, source="registry.azurecr.io/runtime:build123",
                acr_name="registry", subscription_id="sub",
            )
        self.assertEqual(result, "disk")
        run.assert_not_called()
        client.begin_create_disk_image.assert_not_called()

    def test_conversion_rejects_retagged_source_before_requesting_token(self):
        client = Mock()
        client.list_disk_images.return_value = []
        with patch("scripts.sandbox_services.run_text", return_value="sha256:unexpected") as run:
            with self.assertRaisesRegex(ValueError, "does not match"):
                prepare_disk_image(
                    client, role="runtime", image="registry.azurecr.io/runtime@sha256:123",
                    source="registry.azurecr.io/runtime:build123", acr_name="registry", subscription_id="sub",
                )
        self.assertEqual(run.call_count, 1)
        client.begin_create_disk_image.assert_not_called()

    def test_missing_runtime_disk_fails_before_lifecycle_changes(self):
        config = AgentSandboxConfig("sub", "rg", "group", "region", "image")
        with patch("scripts.sandbox_runtime.create_sandbox_group_client") as create:
            from scripts.sandbox_runtime import ensure_agent_sandbox

            with self.assertRaisesRegex(ValueError, "AGENT_RUNTIME_DISK_IMAGE_ID"):
                ensure_agent_sandbox(config)
        create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
