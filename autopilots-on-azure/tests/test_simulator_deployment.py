from __future__ import annotations

import asyncio
import base64
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import itertools
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import httpx
from fastapi import FastAPI

from bridge.transport_ingress import NativeTransportIngressMiddleware
from scripts.deploy_simulator_environment import (
    select_images,
    environment_root, load_config, load_stack_configs, prepare, prepare_root, remove_owned_smart_alert, source_revision, state_path,
    validate_config,
    validate_shared_foundry,
    terraform,
    destroy,
)
from scripts.simulator_channel import simulator_environment, simulator_worker_environment
from scripts.prebuilt_images import load_manifest


class SimulatorDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.images_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.images_directory.cleanup)
        self.image_manifest = {
            "schema": 1, "source_commit": "a" * 40,
            "images": {role: f"ghcr.io/example/demo-{role}@sha256:" + "b" * 64
                       for role in ("runtime", "gateway", "simulator")},
        }
        self.images_path = Path(self.images_directory.name) / "deployment-images.json"
        self.images_path.write_text(json.dumps(self.image_manifest))

    def input_text(self):
        return (
            'environment = "training"\n'
            'network_mode = "public"\nresource_group_mode = "create"\n'
            'subscription_id = "22222222-2222-2222-2222-222222222222"\n'
            'tenant_id = "11111111-1111-1111-1111-111111111111"\n'
            'owner_id = "33333333-3333-3333-3333-333333333333"\n'
            '[role]\nblueprint = "role"\nsource = "https://example.test/role.git"\n'
            'release = "1.0.0"\ncommit = "' + "a" * 40 + '"\n'
            + f"[images]\nmanifest = '{self.images_path}'\n"
        )

    def test_prebuilt_manifest_rejects_mutable_incomplete_or_ambiguous_releases(self):
        self.assertEqual(load_manifest(self.images_path), self.image_manifest)
        invalid = [
            {**self.image_manifest, "schema": True},
            {**self.image_manifest, "source_commit": "main"},
            {**self.image_manifest, "images": {"runtime": self.image_manifest["images"]["runtime"]}},
            {**self.image_manifest, "images": {**self.image_manifest["images"], "runtime": "ghcr.io/example/runtime:latest"}},
            {**self.image_manifest, "images": {**self.image_manifest["images"], "gateway": "https://" + self.image_manifest["images"]["gateway"]}},
            {**self.image_manifest, "extra": "not permitted"},
        ]
        for manifest in invalid:
            with self.subTest(manifest=manifest):
                self.images_path.write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):
                    load_manifest(self.images_path)

    def test_pinned_images_materialize_without_registry_or_build_commands(self):
        with tempfile.TemporaryDirectory() as temporary, patch(
            "scripts.deploy_simulator_environment.source_revision", return_value="a" * 40,
        ), patch("scripts.deploy_simulator_environment.run", side_effect=AssertionError("No build commands")), patch(
            "scripts.deploy_simulator_environment.output", side_effect=AssertionError("No registry commands"),
        ):
            root = Path(temporary)
            select_images({"images": self.image_manifest}, root)
            self.assertEqual(json.loads((root / "deployment-images.json").read_text()), self.image_manifest)
            self.assertEqual(json.loads((root / "simulator-image.json").read_text())["source"],
                             self.image_manifest["images"]["simulator"])
        with patch("scripts.deploy_simulator_environment.source_revision", return_value="c" * 40):
            with self.assertRaisesRegex(ValueError, "source_commit differs"):
                select_images({"images": self.image_manifest}, Path("unused"))

    def config(self, mode="entra"):
        return {
            "environment": "training", "tenant_id": "11111111-1111-1111-1111-111111111111",
            "subscription_id": "22222222-2222-2222-2222-222222222222",
            "owner_id": "33333333-3333-3333-3333-333333333333",
            "auth_mode": mode, "agents": [{"name": "agent-1"}],
        }

    def worker(self, mode="entra"):
        return {
            "simulator_url": "https://sandbox--8000.swedencentral.adcproxy.io",
            "simulator_auth_mode": mode, "simulator_agent_client_id": "agent-client",
            "simulator_channel_client_id": "channel-client", "simulator_demo_key": "a" * 48,
            "user_scheduling_enabled": False, "servicebus_dream_enabled": False,
        }

    def test_entra_channel_uses_separate_apps_and_exact_service_origin(self):
        environment = simulator_worker_environment(self.worker(), {
            "tenant_id": "tenant", "sandbox_groups": {"gateway": {"identity_client_id": "gateway-mi"}},
        }, {"WORKER_OWNER_ID": "owner", "AGENT365_BLUEPRINT_CLIENT_ID": "obsolete",
            "WORKIQ_MAIL_MCP_UPSTREAM_URL": "obsolete", "PRIVATE_INCIDENTS_MCP_URL": "obsolete"})
        prefix = "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__"
        self.assertEqual(environment[prefix + "CLIENTID"], "agent-client")
        self.assertEqual(environment[prefix + "SCOPES__0"], "channel-client/.default")
        self.assertEqual(environment[prefix + "FEDERATEDCLIENTID"], "gateway-mi")
        self.assertEqual(environment["WORKER_OWNER_ID"], "owner")
        self.assertFalse(any(key.startswith(("AGENT365_", "WORKIQ_", "PRIVATE_INCIDENTS_")) for key in environment))
        import re
        pattern = environment["CONNECTIONSMAP__0__SERVICEURL"]
        self.assertIsNotNone(re.fullmatch(pattern, self.worker()["simulator_url"] + "/"))
        self.assertIsNone(re.fullmatch(pattern, self.worker()["simulator_url"] + ".attacker.example"))

    def test_demo_has_no_service_connection_or_entra_dependency(self):
        environment = simulator_worker_environment(self.worker("demo"), {}, {
            "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID": "obsolete",
        })
        self.assertEqual(environment["DEMO_SERVICE_URL"], self.worker()["simulator_url"] + "/demo/" + "a" * 48)
        self.assertFalse(any(key.startswith("CONNECTIONS") for key in environment))

    def test_invalid_origin_and_unsafe_demo_key_are_rejected(self):
        for changes in ({"simulator_url": "https://host/path"}, {"simulator_url": "http://host"},
                        {"simulator_demo_key": "short"}, {"simulator_demo_key": "../" + "a" * 48}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                simulator_worker_environment({**self.worker("demo"), **changes}, {}, {})

    def test_simulator_config_preserves_persistent_paths_and_demo_guard(self):
        config = {**self.config("demo"), "demo_access_code": "code", "demo_expires_at": "2099-01-01T00:00:00Z"}
        env = simulator_environment(config, {"url": "https://host"}, {}, [])
        self.assertEqual(env["TSIM_DATABASE_URL"], "sqlite:////mnt/tsim/simulator.db")
        self.assertEqual(env["TSIM_DEMO_ACKNOWLEDGE"], "test-data-only")
        self.assertEqual(json.loads(env["TSIM_AGENTS_JSON"]), [])

    def test_prepare_pins_operator_and_never_uses_shared_roots(self):
        claims = base64.urlsafe_b64encode(json.dumps({
            "oid": "44444444-4444-4444-4444-444444444444", "tid": self.config()["tenant_id"],
        }).encode()).decode().rstrip("=")
        with tempfile.TemporaryDirectory() as temporary, patch(
            "scripts.deploy_simulator_environment.output", return_value="header." + claims + ".signature"
        ) as command:
            root = Path(temporary)
            first = prepare(self.config(), root)
            second = prepare(self.config(), root)
            self.assertEqual(first["deployer_principal_id"], second["deployer_principal_id"])
            command.assert_called_once()
            values = json.loads((root / "secrets.json").read_text())
            import uuid
            self.assertNotEqual(uuid.UUID(values["registration_client_ids"]["agent-1"]).int, 0)
            prepare(self.config(), root)
            self.assertEqual(values, json.loads((root / "secrets.json").read_text()))
            self.assertNotEqual(state_path(root, "platform", "training"), root / "platform" / "terraform.tfstate")
            with self.assertRaisesRegex(ValueError, "state boundary"):
                prepare({**self.config(), "tenant_id": "different"}, root)

    def test_explicit_demo_owner_matches_simulator_identity_without_changing_state_owner(self):
        import uuid
        config = {
            **self.config("demo"), "demo_owner_display_name": " Teacher ",
            "role": {"blueprint": "role", "source": "https://example.test/role.git",
                     "release": "1.0.0", "commit": "a" * 40},
        }
        values = validate_config(config, validate_expiry=False)
        self.assertEqual(values["worker_owner_id"], str(uuid.uuid5(
            uuid.UUID("51f8c9cd-dcd4-4c33-9ef6-48b4c5919185"), "teacher",
        )))
        self.assertEqual(values["owner_id"], self.config()["owner_id"])
        self.assertEqual(values["demo_owner_display_name"], "Teacher")
        for changes in ({"auth_mode": "entra"}, {"demo_owner_display_name": "   "}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "demo_owner_display_name"):
                validate_config({**config, **changes}, validate_expiry=False)
        config.pop("demo_owner_display_name")
        self.assertEqual(validate_config(config, validate_expiry=False)["worker_owner_id"], config["owner_id"])

    def test_workspace_plugin_error_is_not_reinterpreted_as_a_missing_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "platform").mkdir()
            failure = subprocess.CalledProcessError(1, ["terraform", "workspace", "select"])
            with patch("scripts.deploy_simulator_environment.state_root", return_value=root), patch(
                "scripts.deploy_simulator_environment.run", side_effect=[None, failure],
            ) as command, self.assertRaises(subprocess.CalledProcessError):
                terraform(root / "platform", "training-agent-1", {})
            self.assertEqual(command.call_count, 2)
            self.assertEqual(command.call_args_list[-1].args[0],
                             ["terraform", "workspace", "select", "-or-create", "training-agent-1"])

    def test_partial_teardown_uses_captured_groups_after_terraform_outputs_disappear(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self.config()
            (root / "environment.json").write_text(json.dumps(config))
            state = state_path(root, "agent-1", config["environment"])
            state.parent.mkdir(parents=True)
            state.write_text("{}")
            layer = root / "agent-1"
            captured = {"resource_group_name": "owned-rg", "sandbox_location": "region",
                        "sandbox_groups": {"runtime": {"name": "owned-runtime", "id": "/owned"}}}
            (layer / "outputs.json").write_text(json.dumps(captured))
            (layer / "generated.simulator.auto.tfvars.json").write_text("{}")
            with patch("scripts.sandbox_services.delete_owned_sandboxes") as drain, patch(
                "scripts.deploy_simulator_environment.terraform",
            ), patch("scripts.deploy_simulator_environment.terraform_output") as current:
                destroy(config, root)
            current.assert_not_called()
            self.assertEqual(drain.call_args.args[0], [captured["sandbox_groups"]["runtime"]])
            self.assertEqual(drain.call_args.kwargs["resource_group"], "owned-rg")

    def test_demo_expiry_and_existing_worker_names_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "inputs.toml"
            for name, expiry, error in (("hermes", "2099-01-01T00:00:00Z", "Agent names"),
                                       ("agent-1", (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(), "future")):
                config = self.config("demo")
                path.write_text(
                    "\n".join(f'{key} = "{value}"' for key, value in config.items() if isinstance(value, str))
                    + f'\ndemo_expires_at = "{expiry}"\ndemo_acknowledge = "test-data-only"\n'
                    + f'[[agents]]\nname = "{name}"\n'
                    + '[role]\nblueprint = "role"\nsource = "https://example.test/role.git"\n'
                    + 'release = "1.0.0"\ncommit = "' + "a" * 40 + '"\n', encoding="utf-8",
                )
                with self.assertRaisesRegex(ValueError, error):
                    load_config(path)
                if name == "agent-1":
                    self.assertEqual(load_config(path, validate_expiry=False)["auth_mode"], "demo")

    def test_six_stacks_have_disjoint_state_and_one_worker_each(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "inputs.toml"
            path.write_text(self.input_text(), encoding="utf-8")
            configs = load_stack_configs(path, [])
            self.assertEqual(len(configs), 6)
            self.assertEqual(len({environment_root(config) for config in configs}), 6)
            for index, config in enumerate(configs, 1):
                self.assertEqual(config["environment"], f"training-agent-{index}")
                self.assertEqual([agent["name"] for agent in config["agents"]], [f"agent-{index}"])
                self.assertEqual(config["model_capacity"], 15)

    def test_subset_and_per_stack_capacity_do_not_change_other_stacks(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "inputs.toml"
            path.write_text(self.input_text() + (
                '[[stacks]]\nname = "agent-1"\nmodel_capacity = 10\n'
                '[[stacks]]\nname = "agent-3"\nmodel_capacity = 25\n'
            ), encoding="utf-8")
            configs = load_stack_configs(path, ["agent-3"])
            self.assertEqual(len(configs), 1)
            self.assertEqual(configs[0]["model_capacity"], 25)
            self.assertEqual(configs[0]["stack_name"], "agent-3")
            with self.assertRaisesRegex(ValueError, "Unknown selected"):
                load_stack_configs(path, ["agent-2"])

    def test_seven_stacks_include_dev_and_subset_preserves_exact_state_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "inputs.toml"
            names = [f"agent-{index}" for index in range(1, 7)] + ["agent-dev"]
            path.write_text(self.input_text() + "".join(f'[[stacks]]\nname = "{name}"\n' for name in names))
            configs = load_stack_configs(path, [])
            self.assertEqual([config["stack_name"] for config in configs], names)
            dev, = load_stack_configs(path, ["agent-dev"])
            self.assertEqual(dev, configs[-1])
            paths = {state_path(environment_root(config), "platform", config["environment"]) for config in configs}
            self.assertEqual(len(paths), 7)
            self.assertTrue(all(len("ap-" + name + "-generated-apps-abcd") <= 32 for name in names))
            self.assertEqual(dev["agents"], [{"name": "agent-dev", "display_name": "agent-dev", "assignment": ""}])

    def test_shared_foundry_contract_and_invalid_ownership_are_explicit(self):
        base = {"subscription_id": self.config()["subscription_id"], "network_mode": "public"}
        self.assertIsNone(validate_shared_foundry(dict(base)))
        shared = {"mode": "create", "existing_resource_group_name": "central"}
        value = validate_shared_foundry({**base, "foundry_mode": "shared", "shared_foundry": shared})
        self.assertEqual(value["model_capacity"], 15)
        account_id = f"/subscriptions/{base['subscription_id']}/resourceGroups/central/providers/Microsoft.CognitiveServices/accounts/shared"
        self.assertEqual(validate_shared_foundry({
            **base, "foundry_mode": "shared",
            "shared_foundry": {"mode": "existing", "account_resource_id": account_id},
        })["account_resource_id"], account_id)
        for changes in (
            {"foundry_mode": "invalid"}, {"shared_foundry": shared},
            {"foundry_mode": "shared", "shared_foundry": {"mode": "create"}},
            {"foundry_mode": "shared", "shared_foundry": {**shared, "model_capacity": True}},
            {"foundry_mode": "shared", "shared_foundry": {"mode": "existing", "account_resource_id": "/wrong"}},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_shared_foundry({**base, **changes})

    def test_private_defaults_require_explicit_customer_boundaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "inputs.toml"
            path.write_text(self.input_text().replace('network_mode = "public"\nresource_group_mode = "create"\n', ""))
            with self.assertRaisesRegex(ValueError, "existing_resource_group_name"):
                load_stack_configs(path, [])
            path.write_text(path.read_text() + (
                '[platform]\nexisting_resource_group_name = "customer-stack"\n'
                'existing_sandbox_subnet_id = "/sandbox"\nexisting_private_endpoint_subnet_id = "/pe"\n'
                '[platform.private_dns_zone_ids]\n"privatelink.azurecr.io" = "/zone"\n'
            ))
            configs = load_stack_configs(path, ["agent-1"])
            self.assertTrue(configs[0]["platform"]["private_network_enabled"])
            self.assertFalse(configs[0]["platform"]["scheduler_enabled"])
            self.assertFalse(configs[0]["platform"]["registry_enabled"])
            self.assertEqual(configs[0]["images"], self.image_manifest)

    def test_extracted_source_and_external_state_need_no_git(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "snapshot"
            template = source / "terraform" / "platform"
            template.mkdir(parents=True)
            (template / "main.tf").write_text('variable "name" { type = string }\n')
            (template / ".terraform.lock.hcl").write_text("# pinned providers\n")
            (template / "terraform.tfstate").write_text("must not be copied")
            (source / "deployment-manifest.json").write_text(json.dumps({"source_commit": "a" * 40}))
            with patch("scripts.deploy_simulator_environment.REPO_ROOT", source), patch.dict(
                "os.environ", {"AUTOPILOTS_STATE_ROOT": str(root / "durable")}
            ), patch("scripts.deploy_simulator_environment.output", side_effect=AssertionError("Git must not be called")):
                target = prepare_root(environment_root(self.config()), "platform", "platform")
                self.assertEqual(source_revision(), "a" * 40)
                self.assertEqual(target.parent.parent, root / "durable")
                self.assertEqual({path.name for path in target.iterdir()}, {"main.tf", ".terraform.lock.hcl"})

    def test_local_role_bundle_is_verified_before_packaging(self):
        from scripts.role_bundle import build_role_bundle
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "customer-roles" / "junior"
            directory.mkdir(parents=True)
            (directory / "role.toml").write_text('name = "junior"\n')
            role = {"blueprint": "junior", "path": "customer-roles/junior", "release": "1.0.0", "commit": "a" * 40}
            files, digest = build_role_bundle(role, root=root, verify=False)
            role["bundle_sha256"] = digest
            self.assertEqual(build_role_bundle(role, root=root), (files, digest))
            (directory / "role.toml").write_text('name = "changed"\n')
            with self.assertRaisesRegex(ValueError, "differs"):
                build_role_bundle(role, root=root)

    def test_role_data_publisher_writes_manifest_last_and_requires_permission_completion(self):
        from azure.core.exceptions import ResourceNotFoundError
        from scripts.role_bundle import publish_role_bundle

        files = {"role-release.json": b"manifest", "roles/junior/role.toml": b"name='junior'"}
        sandbox = Mock()
        sandbox.read_file.side_effect = ResourceNotFoundError()
        sandbox.exec.return_value = SimpleNamespace(exit_code=0, stdout="complete\n")
        with patch("scripts.role_bundle.build_role_bundle", return_value=(files, "a" * 64)), patch(
            "scripts.role_bundle.uuid.uuid4", return_value=SimpleNamespace(hex="complete"),
        ):
            directory = publish_role_bundle(sandbox, {})
            self.assertEqual(sandbox.write_file.call_args.args[0], directory + "/role-release.json")
            self.assertTrue(sandbox.exec.call_args.args[0].startswith("chmod 0600 "))
            sandbox.exec.return_value = SimpleNamespace(exit_code=0, stdout="")
            with self.assertRaisesRegex(RuntimeError, "did not complete"):
                publish_role_bundle(sandbox, {})

    def test_real_chat_smoke_requires_model_reply_from_registered_agent(self):
        import httpx
        from scripts.simulator_smoke import verify_chat

        marker = ""

        def respond(request):
            nonlocal marker
            if request.url.path == "/api/demo/login":
                return httpx.Response(200, json={"accessToken": "session"})
            self.assertEqual(request.headers["Authorization"], "Bearer session")
            if request.url.path == "/api/users":
                return httpx.Response(200, json=[{"id": "agent", "isAgent": True}])
            if request.url.path == "/api/chats":
                return httpx.Response(201, json={"conversationId": "conversation"})
            if request.method == "POST":
                marker = json.loads(request.content)["text"].split()[-1]
                return httpx.Response(201, json={"id": "user-message"})
            return httpx.Response(200, json=[{
                "id": "reply", "author": {"isAgent": True}, "text": marker,
            }])

        with httpx.Client(base_url="https://simulator.test", transport=httpx.MockTransport(respond)) as client:
            evidence = verify_chat("https://simulator.test", access_code="code", client=client)
        self.assertEqual(evidence["reply_id"], "reply")
        self.assertEqual(evidence["marker"], marker)

    def test_real_chat_smoke_does_not_accept_a_user_echo(self):
        import httpx
        from scripts.simulator_smoke import verify_chat

        marker = ""

        def respond(request):
            nonlocal marker
            if request.url.path == "/api/users":
                return httpx.Response(200, json=[{"id": "agent", "isAgent": True}])
            if request.url.path == "/api/chats":
                return httpx.Response(201, json={"conversationId": "conversation"})
            if request.method == "POST":
                marker = json.loads(request.content)["text"].split()[-1]
                return httpx.Response(201, json={"id": "user-message"})
            return httpx.Response(200, json=[{
                "id": "user-message", "author": {"isAgent": False}, "text": marker,
            }])

        with httpx.Client(base_url="https://simulator.test", transport=httpx.MockTransport(respond)) as client:
            with self.assertRaisesRegex(TimeoutError, "Hermes/model reply"):
                verify_chat("https://simulator.test", access_token="session", client=client, timeout=0)

    def test_teardown_only_removes_exact_stack_generated_alert(self):
        config = self.config()
        group = "rg-ap-training-abcd"
        boundary = f"/subscriptions/{config['subscription_id']}/resourceGroups/{group}"
        alert = boundary + "/providers/microsoft.alertsmanagement/smartDetectorAlertRules/Failure Anomalies - appi-abcd"
        platform = {"resource_group_name": group,
                    "application_insights_id": boundary + "/providers/Microsoft.Insights/components/appi-abcd"}
        resources = [
            {"id": alert, "type": "microsoft.alertsmanagement/smartDetectorAlertRules",
             "name": "Failure Anomalies - appi-abcd"},
            {"id": "unrelated", "type": "microsoft.alertsmanagement/smartDetectorAlertRules", "name": "Another alert"},
        ]
        with patch("scripts.deploy_simulator_environment.output", return_value=json.dumps(resources)), patch(
            "scripts.deploy_simulator_environment.run"
        ) as command:
            remove_owned_smart_alert(config, platform)
            self.assertEqual(command.call_count, 1)
            self.assertEqual(command.call_args.args[0][4], alert)
            with self.assertRaisesRegex(ValueError, "resource boundary"):
                remove_owned_smart_alert(config, {**platform, "resource_group_name": "rg-existing"})

    def test_each_stack_selects_its_own_pinned_manifest_without_build_or_copy(self):
        from scripts.deploy_simulator_environment import main

        configs = [
            {"environment": f"test-agent-{index}", "stack_name": f"agent-{index}",
             "network_mode": "public", "images": {
                 **self.image_manifest,
                 "images": {**self.image_manifest["images"], "runtime": "ghcr.io/example/runtime@sha256:" + base * 64},
             }}
            for index, base in ((1, "a"), (2, "b"), (3, "b"))
        ]
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            "os.environ", {"AUTOPILOTS_STATE_ROOT": temporary}
        ), patch("sys.argv", ["deploy", "--config", "inputs.toml", "images"]), patch(
            "scripts.deploy_simulator_environment.load_stack_configs", return_value=configs
        ), patch("scripts.deploy_simulator_environment.assert_account"), patch(
            "scripts.deploy_simulator_environment.prepare"
        ), patch("scripts.deploy_simulator_environment.source_revision", return_value="a" * 40), patch(
            "scripts.deploy_simulator_environment.select_images"
        ) as images:
            main()
            self.assertEqual([call.args[0] for call in images.call_args_list], configs)
            self.assertTrue(all(not call.kwargs for call in images.call_args_list))

    def test_private_conversion_and_url_validation_use_native_vnet_binding(self):
        from scripts.sandbox_services import disk_image_request, require_private_endpoint

        body = disk_image_request("acr/image:tag", "image", "identity-client", "private")
        self.assertEqual(body["customerVnetConnectionName"], "private")
        self.assertNotIn("customerVnetConnectionName", body["source"])
        binding = {"private_network_enabled": True, "public_network_access": "Disabled",
                   "private_domain": "environment.swedencentral.azurecontainerapps.io", "vnet_connection_name": "private"}
        require_private_endpoint("https://sandbox--8000.environment.swedencentral.azurecontainerapps.io", binding)
        for origin in ("https://sandbox--8000.swedencentral.adcproxy.io",
                       "https://sandbox--8000.environment.swedencentral.azurecontainerapps.io.attacker.test"):
            with self.assertRaises(RuntimeError):
                require_private_endpoint(origin, binding)

    def test_simulator_root_and_persistent_data_fit_native_cpu_disk_budget(self):
        from scripts.simulator_hosting import deploy_host

        group = Mock()
        group._group_path = "/group"
        group.list_volumes.return_value = []
        group.get_volume.return_value.is_attached = False
        group._dp_get.return_value = []
        group._dp_put.return_value = {"id": "created"}
        group.get_sandbox_client.return_value.get.return_value.ports = [
            SimpleNamespace(port=8000, url="https://simulator.example"),
        ]
        binding = {"subscription_id": "subscription", "identity_client_id": "identity"}
        with tempfile.TemporaryDirectory() as temporary, patch(
            "scripts.simulator_hosting.AzureCliCredential",
        ), patch("scripts.simulator_hosting.group_client") as factory, patch(
            "scripts.simulator_hosting.prepare_disk_image", return_value="disk",
        ), patch("scripts.simulator_hosting.require_always_running", return_value={}):
            factory.return_value.__enter__.return_value = group
            deploy_host(
                {"environment": "training"}, binding, {"acr_name": ""},
                {"image": "image", "source": "image"}, Path(temporary) / "hosting.json",
            )
        body = group._dp_put.call_args.args[1]
        data_gib = int(group.create_volume.call_args.kwargs["size"].removesuffix("Gi"))
        total_disk_gib = int(body["resources"]["disk"].removesuffix("Gi")) + data_gib
        cpu_millicores = int(body["resources"]["cpu"].removesuffix("m"))
        self.assertLessEqual(total_disk_gib * 1000, cpu_millicores * 20)

    def test_default_readiness_probes_main_gateway_not_callback_health_port(self):
        from scripts.deploy_simulator_environment import verify_readiness
        from scripts.sandbox_services import require_service_health

        app = FastAPI()
        app.add_middleware(NativeTransportIngressMiddleware)

        @app.get("/health")
        @app.get("/api/health")
        async def service_health():
            return {"release": "a" * 40}

        observed = []

        async def request(url):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
                return await client.get(url)

        def urlopen(url, timeout):
            response = asyncio.run(request(url))
            observed.append((url, response.status_code))
            if response.status_code != 200:
                raise HTTPError(url, response.status_code, "Health unavailable", {}, None)
            body = io.BytesIO(response.content)
            body.status = response.status_code
            return body

        def execute(command):
            stdout = io.StringIO()
            try:
                with redirect_stdout(stdout):
                    exec(shlex.split(command)[2], {})
            except HTTPError:
                return SimpleNamespace(exit_code=1, stdout=stdout.getvalue(), stderr="HTTP404")
            return SimpleNamespace(exit_code=0, stdout=stdout.getvalue(), stderr="")

        sandbox = Mock()
        sandbox.exec.side_effect = execute
        group = Mock()
        group.get_sandbox_client.return_value = sandbox
        ticks = itertools.count(step=100)
        clock = SimpleNamespace(monotonic=lambda: next(ticks), sleep=lambda _: None)

        config = {
            "subscription_id": "subscription", "agents": [{"name": "agent-4"}],
            "images": {"source_commit": "a" * 40},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "hosting.json").write_text(json.dumps({
                "subscription_id": "subscription", "sandbox_id": "simulator",
                "management_endpoint": "https://management.example",
            }))
            (root / "agent-4").mkdir()
            (root / "agent-4" / "outputs.json").write_text(json.dumps({
                "resource_group_name": "isolated",
                "sandbox_groups": {"runtime": {"name": "runtime"}, "gateway": {"name": "gateway"}},
                "runtime_sandbox_id": "runtime", "bridge_sandbox_id": "gateway",
            }))
            with patch("azure.identity.AzureCliCredential"), patch(
                "azure.containerapps.sandbox.SandboxGroupClient",
            ) as worker_group, patch("scripts.simulator_hosting.group_client") as simulator_group, patch(
                "scripts.sandbox_services.require_always_running", return_value={},
            ), patch("urllib.request.urlopen", side_effect=urlopen), patch(
                "scripts.sandbox_services.time", clock,
            ):
                worker_group.return_value.__enter__.return_value = group
                simulator_group.return_value.__enter__.return_value = group
                verify_readiness(config, root)
                with self.assertRaises(TimeoutError):
                    require_service_health(sandbox, 8001, "a" * 40)
            self.assertEqual(observed, [
                ("http://127.0.0.1:8000/api/health", 200),
                ("http://127.0.0.1:8642/health", 200),
                ("http://127.0.0.1:8000/health", 200),
                ("http://127.0.0.1:8001/health", 404),
            ])

    def test_entra_verification_requires_scoped_recent_manual_browser_evidence(self):
        import httpx
        from scripts.deploy_simulator_environment import save, verify

        config = self.config()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            save(root / "deployment.json", {"url": "https://simulator.test"})
            save(root / "agent-1" / "outputs.json", {"bridge_url": "https://gateway.test"})
            client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(401)))
            with patch("scripts.simulator_hosting.wait_health", return_value={"status": "ok"}), patch(
                "httpx.Client", return_value=client
            ):
                with self.assertRaisesRegex(ValueError, "never paste"):
                    verify(config, root)
            self.assertTrue((root / "verification.json").exists())
            valid = {
                "schemaVersion": 1, "environment": config["environment"], "url": "https://simulator.test",
                "authMode": "entra", "verifiedAt": datetime.now(timezone.utc).isoformat(),
                "checks": dict.fromkeys(("login", "messageReply", "typing", "reactions", "cards", "groupChat", "files"), True),
            }
            cases = (
                ({**valid, "environment": "other"}, False),
                ({**valid, "checks": {**valid["checks"], "files": False}}, False),
                ({**valid, "verifiedAt": (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()}, False),
                ({**valid, "verifiedAt": datetime.now().isoformat()}, False),
                (valid, True),
            )
            for evidence, accepted in cases:
                save(root / "browser-acceptance.json", evidence)
                client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(401)))
                with self.subTest(evidence=evidence), patch(
                    "scripts.simulator_hosting.wait_health", return_value={"status": "ok"}
                ), patch("httpx.Client", return_value=client):
                    if accepted:
                        verify(config, root)
                    else:
                        with self.assertRaises(ValueError):
                            verify(config, root)


if __name__ == "__main__":
    unittest.main()
