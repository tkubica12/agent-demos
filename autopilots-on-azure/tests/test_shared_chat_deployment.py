from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.deploy_simulator_environment import (
    chat_host_root, deploy, destroy, environment_root, hosts_chat, infrastructure, load_stack_configs,
    run_stack_action, worker_values,
)


CHAT = "aaaaaaaa-0000-0000-0000-000000000001"
CHANNEL = "aaaaaaaa-0000-0000-0000-000000000002"


def agent365(index: int) -> dict:
    return {
        "blueprint_client_id": f"bbbbbbbb-0000-0000-0000-00000000000{index}",
        "agent_identity_id": f"cccccccc-0000-0000-0000-00000000000{index}",
        "agent_user_id": f"dddddddd-0000-0000-0000-00000000000{index}",
        "agent_user_principal_name": f"agent-{index}@example.test",
    }


class SharedChatDeploymentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        state = patch.dict(os.environ, {"AUTOPILOTS_STATE_ROOT": str(self.directory / "state")})
        state.start()
        self.addCleanup(state.stop)
        images = self.directory / "deployment-images.json"
        images.write_text(json.dumps({
            "schema": 1, "source_commit": "a" * 40,
            "images": {role: f"ghcr.io/example/demo-{role}@sha256:" + "b" * 64
                       for role in ("runtime", "gateway", "simulator")},
        }))
        self.base = (
            'environment = "training"\n'
            'network_mode = "public"\nresource_group_mode = "create"\n'
            'subscription_id = "22222222-2222-2222-2222-222222222222"\n'
            'tenant_id = "11111111-1111-1111-1111-111111111111"\n'
            'owner_id = "33333333-3333-3333-3333-333333333333"\n'
            '[role]\nblueprint = "role"\nsource = "https://example.test/role.git"\n'
            'release = "1.0.0"\ncommit = "' + "a" * 40 + '"\n'
            + f"[images]\nmanifest = '{images}'\n"
        )

    def stack(self, index: int, **overrides: str) -> str:
        values = {**agent365(index), **overrides}
        return f'[[stacks]]\nname = "agent-{index}"\n[stacks.agent365]\n' + "".join(
            f'{key} = "{value}"\n' for key, value in values.items()
        )

    def configs(self, text: str | None = None) -> list[dict]:
        path = self.directory / "inputs.toml"
        path.write_text(text if text is not None else self.base + (
            f'[shared_chat]\nhost = "agent-2"\nclient_id = "{CHAT}"\nchannel_client_id = "{CHANNEL}"\n'
            + self.stack(1) + self.stack(2)
        ), encoding="utf-8")
        return load_stack_configs(path, [])

    def prepared(self, config: dict) -> Path:
        root = environment_root(config)
        root.mkdir(parents=True, exist_ok=True)
        (root / "secrets.json").write_text(json.dumps({
            "demo_access_code": "unused", "agent_keys": {config["stack_name"]: "k" * 43},
            "registration_client_ids": {},
        }))
        (root / "platform-outputs.json").write_text(json.dumps({"resource_group_name": "rg-" + config["stack_name"]}))
        (root / "simulator-image.json").write_text(json.dumps({"image": "image", "source": "image"}))
        (root / "bindings.json").write_text(json.dumps({"channel": {"name": "chat"}, "agents": {}}))
        return root

    def test_one_stack_hosts_the_chat_and_every_stack_carries_its_agent365_identity(self):
        first, second = self.configs()
        self.assertFalse(hosts_chat(first))
        self.assertTrue(hosts_chat(second))
        self.assertEqual(chat_host_root(first, environment_root(first)), environment_root(second))
        self.assertEqual(first["shared_chat"], {"host": "agent-2", "client_id": CHAT, "channel_client_id": CHANNEL})
        self.assertEqual(first["agent365"], agent365(1))

    def test_incomplete_or_ambiguous_shared_chat_inputs_are_rejected(self):
        chat = f'[shared_chat]\nhost = "agent-1"\nclient_id = "{CHAT}"\nchannel_client_id = "{CHANNEL}"\n'
        cases = {
            "requires \\[shared_chat\\]": self.base + self.stack(1),
            "must name one of the stacks": self.base + chat.replace("agent-1", "agent-9") + self.stack(1),
            "must be distinct": self.base + chat.replace(CHANNEL, CHAT) + self.stack(1),
            "requires stacks.agent365": self.base + chat + self.stack(1) + '[[stacks]]\nname = "agent-2"\n',
            "requires auth_mode = entra": self.base.replace(
                'environment = "training"\n', 'environment = "training"\nauth_mode = "demo"\n',
            ) + chat + self.stack(1),
            "own blueprint and Agent Identity": self.base + chat + self.stack(1) + self.stack(
                2, agent_identity_id=agent365(1)["agent_identity_id"],
            ),
            "user principal name": self.base + chat + self.stack(1, agent_user_principal_name="no-domain"),
        }
        for message, text in cases.items():
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.configs(text)

    def test_stack_worker_becomes_an_agent365_worker_without_native_transport(self):
        first, _ = self.configs()
        root = self.prepared(first)
        values = worker_values(
            first, root, first["agents"][0], {"deployer_principal_id": "44444444-4444-4444-4444-444444444444"},
            {"channel": {"client_id": CHANNEL}}, simulator_url="https://chat.example.test",
        )
        self.assertEqual(values["channel"], "agent365")
        self.assertFalse(values["native_transport_enabled"])
        self.assertFalse(values["generated_chat_files_enabled"])
        self.assertFalse(values["user_scheduling_enabled"])
        self.assertEqual(values["agent365_client_id"], agent365(1)["blueprint_client_id"])
        self.assertEqual(values["agent365_agent_identity_client_id"], agent365(1)["agent_identity_id"])
        self.assertEqual(values["agent365_agent_identity_object_id"], agent365(1)["agent_identity_id"])
        self.assertEqual(values["agent365_agent_user_principal_name"], "agent-1@example.test")
        self.assertEqual(values["agent365_tenant_id"], first["tenant_id"])
        self.assertEqual(values["simulator_channel_client_id"], CHANNEL)
        self.assertEqual(values["simulator_url"], "https://chat.example.test")
        self.assertEqual(values["simulator_agent_client_id"], "")

    def test_only_the_host_applies_chat_infrastructure_and_no_stack_creates_entra_applications(self):
        first, second = self.configs()
        ownership = {"deployer_principal_id": "44444444-4444-4444-4444-444444444444"}
        worker = {"sandbox_groups": {
            "gateway": {"identity_client_id": "gateway-client", "identity_principal_id": "gateway-principal"},
            "runtime": {"identity_principal_id": "runtime-principal"},
        }}

        def apply(layer, workspace, values, **_):
            return {"channel_binding": {"name": "chat"}} if layer.name == "simulator" else worker

        for config, layers in ((first, ["platform", "agent-1"]), (second, ["platform", "simulator", "agent-2"])):
            root = self.prepared(config)
            (root / "bindings.json").unlink()
            with patch("scripts.deploy_simulator_environment.terraform", side_effect=apply) as terraform, patch(
                "scripts.deploy_simulator_environment.terraform_output", return_value={},
            ):
                infrastructure(config, root, ownership)
            self.assertEqual([call.args[0].name for call in terraform.call_args_list], layers)
            self.assertEqual("channel" in json.loads((root / "bindings.json").read_text()), hosts_chat(config))
        with patch("scripts.deploy_simulator_environment.assert_account"), patch(
            "scripts.deploy_simulator_environment.prepare", return_value=ownership,
        ), patch("scripts.deploy_simulator_environment.identities") as identities, redirect_stdout(io.StringIO()):
            run_stack_action(first, "identities")
        identities.assert_not_called()

    def test_every_deployment_registers_all_known_agents_in_the_one_chat(self):
        first, second = self.configs()
        roots = {config["stack_name"]: self.prepared(config) for config in (first, second)}
        ownership = {"deployer_principal_id": "44444444-4444-4444-4444-444444444444"}
        hosting = {"url": "https://chat.example.test", "sandbox_id": "chat-sandbox"}

        def deploy_host(config, binding, platform, image, path):
            path.write_text(json.dumps(hosting))
            return hosting

        def services(worker, platform):
            return {"bridge_url": "https://" + worker["name"] + ".example.test"}

        def apply(layer, workspace, values, **_):
            self.assertEqual(values["simulator_url"], hosting["url"])
            return {"name": layer.name, "deployment_config": {}}

        with patch("scripts.simulator_hosting.deploy_host", side_effect=deploy_host) as host, patch(
            "scripts.simulator_hosting.configure_host",
        ) as configure, patch("scripts.sandbox_services.deploy_sandbox_services", side_effect=services), patch(
            "scripts.deploy_simulator_environment.terraform", side_effect=apply,
        ), patch("scripts.deploy_simulator_environment.source_revision", return_value="a" * 40), patch(
            "scripts.deploy_simulator_environment.identities",
        ) as identities, redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "Deploy the chat host stack agent-2 before agent-1"):
                deploy(first, roots["agent-1"], ownership)
            deploy(second, roots["agent-2"], ownership)
            deploy(first, roots["agent-1"], ownership)
            deploy(second, roots["agent-2"], ownership)
        identities.assert_not_called()
        self.assertEqual(host.call_count, 2)
        self.assertEqual([[agent["name"] for agent in call.args[3]] for call in configure.call_args_list],
                         [["agent-2"], ["agent-1", "agent-2"], ["agent-1", "agent-2"]])
        hosted_config, binding, identity, agents = configure.call_args_list[1].args
        self.assertEqual(binding, hosting)
        self.assertEqual(identity["spa"]["client_id"], CHAT)
        self.assertEqual(identity["channel"]["client_id"], CHANNEL)
        self.assertEqual(agents[0], {
            "name": "agent-1", "displayName": "agent-1", "clientId": agent365(1)["agent_identity_id"],
            "messagingEndpoint": "https://agent-1.example.test/api/messages",
        })
        self.assertFalse((roots["agent-1"] / "hosting.json").exists())
        self.assertFalse((roots["agent-2"] / "deployment.lock").exists())
        self.assertEqual(json.loads((roots["agent-1"] / "deployment.json").read_text())["url"], hosting["url"])

    def test_the_chat_host_is_destroyed_last(self):
        first, second = self.configs()
        roots = {config["stack_name"]: self.prepared(config) for config in (first, second)}
        registry = roots["agent-2"] / "registrations.json"
        registry.write_text(json.dumps({"agent-1": {"name": "agent-1"}, "agent-2": {"name": "agent-2"}}))
        for config in (first, second):
            (roots[config["stack_name"]] / "environment.json").write_text(json.dumps({
                key: config[key] for key in ("environment", "tenant_id", "subscription_id")
            }))
        with self.assertRaisesRegex(ValueError, "Destroy the other stacks of the shared chat first: agent-1"):
            destroy(second, roots["agent-2"])
        self.assertEqual(set(json.loads(registry.read_text())), {"agent-1", "agent-2"})
        destroy(first, roots["agent-1"])
        self.assertEqual(set(json.loads(registry.read_text())), {"agent-2"})
        destroy(second, roots["agent-2"])
        self.assertEqual(json.loads(registry.read_text()), {})


if __name__ == "__main__":
    unittest.main()
