from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.deploy_simulator_environment import (
    environment_root, load_config, load_stack_configs, prepare, prepare_root, remove_owned_smart_alert, source_revision, state_path,
    validate_config,
    terraform,
)
from scripts.simulator_channel import simulator_environment, simulator_worker_environment


class SimulatorDeploymentTests(unittest.TestCase):
    def input_text(self):
        return (
            'environment = "training"\n'
            'network_mode = "public"\nresource_group_mode = "create"\n'
            'subscription_id = "22222222-2222-2222-2222-222222222222"\n'
            'tenant_id = "11111111-1111-1111-1111-111111111111"\n'
            'owner_id = "33333333-3333-3333-3333-333333333333"\n'
            '[role]\nblueprint = "role"\nsource = "https://example.test/role.git"\n'
            'release = "1.0.0"\ncommit = "' + "a" * 40 + '"\n'
        )

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
            self.assertEqual(configs[0]["build"]["mode"], "source")

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

    def test_import_preserves_digest_and_reuses_an_existing_copy(self):
        from scripts.deploy_simulator_environment import import_image

        digest = "sha256:" + "a" * 64
        target = {"acr_name": "target", "acr_login_server": "target.azurecr.io"}
        image = "source.azurecr.io/runtime@" + digest
        for tags, expected_imports in (([], 1), (["bundle-" + "a" * 12], 0)):
            with self.subTest(tags=tags), patch(
                "scripts.deploy_simulator_environment.output",
                side_effect=[json.dumps(["runtime"]), json.dumps(tags), digest],
            ), patch("scripts.deploy_simulator_environment.run") as command:
                copied, tagged = import_image(target, "/source-registry", "source.azurecr.io", image)
                self.assertEqual(copied, "target.azurecr.io/runtime@" + digest)
                self.assertEqual(tagged, "target.azurecr.io/runtime:bundle-" + "a" * 12)
                self.assertEqual(command.call_count, expected_imports)
        with patch("scripts.deploy_simulator_environment.output", side_effect=["[]", "sha256:" + "b" * 64]), patch(
            "scripts.deploy_simulator_environment.run"
        ), self.assertRaisesRegex(ValueError, "preserve"):
            import_image(target, "/source-registry", "source.azurecr.io", image)
        for invalid in ("other.azurecr.io/runtime@" + digest, "source.azurecr.io/runtime:latest"):
            with self.assertRaises(ValueError):
                import_image(target, "/source-registry", "source.azurecr.io", invalid)

    def test_private_import_requires_both_explicit_exceptions(self):
        from scripts.deploy_simulator_environment import import_configured_images

        config = {"network_mode": "private", "build": {"allow_trusted_service_import": True}}
        with patch("scripts.deploy_simulator_environment.output") as command:
            with self.assertRaisesRegex(ValueError, "explicit"):
                import_configured_images(config, Path("unused"), {"acr_trusted_services_bypass_enabled": False})
            command.assert_not_called()

    def test_private_source_build_uses_pool_without_arm_import_and_invalidates_changed_dependencies(self):
        from scripts.deploy_simulator_environment import build_images, save

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "snapshot"
            runtime = source / "runtimes" / "hermes"
            dependencies = (
                "Dockerfile", "pyproject.toml", "uv.lock", "hermes-site.txt", "package.json", "package-lock.json",
                "office_helper/OfficeCollaboration.csproj", "office_helper/Program.cs",
            )
            for name in dependencies:
                path = runtime / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(name)
            (source / "chat-simulator").mkdir()
            (source / "chat-simulator" / "Dockerfile").write_text("FROM simulator")
            save(root / "platform-outputs.json", {
                "acr_name": "target", "acr_login_server": "target.azurecr.io", "acr_agent_pool_name": "privatebuild",
            })
            config = {"network_mode": "private", "build": {"mode": "source"}}
            with patch("scripts.deploy_simulator_environment.REPO_ROOT", source), patch(
                "scripts.deploy_simulator_environment.source_revision", return_value="a" * 40
            ), patch("scripts.build_images.acr_build", return_value="sha256:" + "b" * 64) as build, patch(
                "scripts.build_images.import_hermes_upstream", side_effect=AssertionError("No ARM import permitted")
            ), patch("scripts.deploy_simulator_environment.copy_images", side_effect=AssertionError("No implicit import")):
                build_images(config, root, source_root=root / "other-stack")
                self.assertEqual(build.call_count, 3)
                self.assertTrue(all(call.kwargs["agent_pool_name"] == "privatebuild" for call in build.call_args_list))
                runtime_build = next(call for call in build.call_args_list if call.kwargs["repository"] == "hermes-runtime")
                self.assertTrue(runtime_build.kwargs["build_args"]["HERMES_BASE_IMAGE"].startswith("docker.io/"))
                build.reset_mock()
                (runtime / "server.py").write_text("source-only update")
                build_images(config, root)
                self.assertEqual(build.call_count, 2)
                (runtime / "Dockerfile").write_text("changed dependency")
                build.reset_mock()
                build_images(config, root)
                self.assertEqual(build.call_count, 3)

    def test_differing_per_stack_build_settings_do_not_reuse_image_seed(self):
        from scripts.deploy_simulator_environment import main

        configs = [
            {"environment": f"test-agent-{index}", "stack_name": f"agent-{index}",
             "network_mode": "public", "build": {"mode": "source", "gateway_base_image": base}}
            for index, base in ((1, "first"), (2, "second"), (3, "second"))
        ]
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            "os.environ", {"AUTOPILOTS_STATE_ROOT": temporary}
        ), patch("sys.argv", ["deploy", "--config", "inputs.toml", "build"]), patch(
            "scripts.deploy_simulator_environment.load_stack_configs", return_value=configs
        ), patch("scripts.deploy_simulator_environment.assert_account"), patch(
            "scripts.deploy_simulator_environment.prepare"
        ), patch("scripts.deploy_simulator_environment.build_images") as build:
            main()
            self.assertIsNone(build.call_args_list[0].kwargs["source_root"])
            self.assertIsNone(build.call_args_list[1].kwargs["source_root"])
            self.assertEqual(build.call_args_list[2].kwargs["source_root"], environment_root(configs[1]))

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
