from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.deploy_simulator_environment import environment_root, load_config, load_stack_configs, prepare, state_path
from scripts.simulator_channel import simulator_environment, simulator_worker_environment


class SimulatorDeploymentTests(unittest.TestCase):
    def input_text(self):
        return (
            'environment = "training"\n'
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
        with tempfile.TemporaryDirectory() as temporary, patch(
            "scripts.deploy_simulator_environment.output", return_value="44444444-4444-4444-4444-444444444444"
        ) as command:
            root = Path(temporary)
            first = prepare(self.config(), root)
            second = prepare(self.config(), root)
            self.assertEqual(first["deployer_principal_id"], second["deployer_principal_id"])
            command.assert_called_once()
            self.assertNotEqual(state_path(root, "platform", "training"), root / "platform" / "terraform.tfstate")
            with self.assertRaisesRegex(ValueError, "state boundary"):
                prepare({**self.config(), "tenant_id": "different"}, root)

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


if __name__ == "__main__":
    unittest.main()
