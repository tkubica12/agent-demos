from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest

from scripts.deploy_simulator_environment import hosts_chat, load_stack_configs


SKILLS = Path(__file__).resolve().parents[1] / "skills"
SKILL = SKILLS / "shared-chat-deploy"


def state_script():
    spec = importlib.util.spec_from_file_location("shared_chat_state", SKILL / "scripts" / "shared_chat_state.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SharedChatSkillTests(unittest.TestCase):
    def inputs(self, root: Path) -> Path:
        shutil.copy(SKILLS / "autopilots-deploy" / "assets" / "dns-zones.example.json", root / "dns-zones.json")
        (root / "image-release.json").write_text(json.dumps({
            "schema": 1, "source_commit": "a" * 40,
            "images": {name: f"ghcr.io/example/{name}@sha256:" + "b" * 64
                       for name in ("runtime", "gateway", "simulator")},
        }))
        text = (SKILL / "assets" / "stacks-shared-chat.example.toml").read_text(encoding="utf-8")
        # The role bundle has its own tests; a remote role keeps this one about the shared chat inputs.
        text = re.sub(r"\[role\]\n.*?\n\n", (
            '[role]\nblueprint = "role"\nsource = "https://example.test/role.git"\n'
            'release = "1.0.0"\ncommit = "' + "a" * 40 + '"\n\n'
        ), text, flags=re.DOTALL)
        path = root / "stacks.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_frontmatter_relative_links_and_embedded_python(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        frontmatter = re.match(r"\A---\nname: ([a-z0-9-]+)\ndescription: ([^\n]+)\n---\n", text)
        self.assertIsNotNone(frontmatter)
        self.assertEqual(frontmatter[1], "shared-chat-deploy")
        for document in SKILL.rglob("*.md"):
            content = document.read_text(encoding="utf-8")
            for link in re.findall(r"\[[^\]]+\]\(([^)]+)\)", content):
                if "://" not in link:
                    with self.subTest(document=document.name, link=link):
                        self.assertTrue((document.parent / link).is_file())
            for snippet in re.findall(r"@'\n(.*?)\n'@ \| uv run python", content, re.DOTALL):
                compile(snippet, str(document), "exec")

    def test_example_inputs_pass_the_deployer_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            configs = load_stack_configs(self.inputs(Path(temporary)), [])
        self.assertEqual([config["stack_name"] for config in configs], ["agent-1", "agent-2"])
        self.assertEqual([hosts_chat(config) for config in configs], [True, False])
        self.assertTrue(all(config["network_mode"] == "private" for config in configs))
        self.assertEqual(len({config["agent365"]["agent_identity_id"] for config in configs}), 2)

    def test_state_script_reports_handoff_values_registration_and_stale_urls(self):
        state = state_script().shared_chat_state
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self.inputs(root)
            empty = state(config, root)
            self.assertEqual(empty["redirect_uri"], "")
            self.assertFalse(empty["stacks"]["agent-2"]["registered"])

            def write(stack: str, name: str, payload: dict) -> None:
                folder = root / f"simulator-training-{stack}"
                folder.mkdir(exist_ok=True)
                (folder / name).write_text(json.dumps(payload))

            write("agent-1", "bindings.json", {
                "channel": {"identity_principal_id": "channel-principal"},
                "agents": {"agent-1": {"identity_principal_id": "gateway-1",
                                       "runtime_identity_principal_id": "runtime-1"}},
            })
            write("agent-2", "bindings.json", {"agents": {"agent-2": {
                "identity_principal_id": "gateway-2", "runtime_identity_principal_id": "runtime-2",
            }}})
            write("agent-1", "hosting.json", {"url": "https://chat-new.example.test"})
            write("agent-1", "registrations.json", {"agent-1": {"name": "agent-1", "clientId": "identity-1"}})
            write("agent-1", "deployment.json", {"url": "https://chat-new.example.test"})
            write("agent-2", "deployment.json", {"url": "https://chat-old.example.test"})
            self.assertEqual(state(config, root), {
                "host": "agent-1",
                "channel_identity_principal_id": "channel-principal",
                "redirect_uri": "https://chat-new.example.test/",
                "stacks": {
                    "agent-1": {"gateway_identity_principal_id": "gateway-1",
                                "runtime_identity_principal_id": "runtime-1", "registered": True,
                                "registered_client_id": "identity-1", "stale_chat_url": False},
                    "agent-2": {"gateway_identity_principal_id": "gateway-2",
                                "runtime_identity_principal_id": "runtime-2", "registered": False,
                                "registered_client_id": "", "stale_chat_url": True},
                },
            })
            config.write_text(config.read_text().replace('[shared_chat]', '[other]'))
            with self.assertRaisesRegex(ValueError, "no \\[shared_chat\\] block"):
                state(config, root)


if __name__ == "__main__":
    unittest.main()
