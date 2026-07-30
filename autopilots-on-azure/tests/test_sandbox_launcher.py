import unittest
from types import SimpleNamespace

from scripts.sandbox_runtime import (
    AgentSandboxConfig,
    create_agent_sandbox,
    ensure_sandbox_runtime_process,
    recycle_stopped_agent_sandbox,
)


class SandboxLauncherTests(unittest.TestCase):
    def test_runtime_process_uses_current_entrypoint_fields(self):
        class Client:
            _group_path = "/groups/test"

            def __init__(self):
                self.body = None

            def _dp_put(self, path, body):
                self.assert_path = path
                self.body = body
                return {"id": "sandbox-1"}

            def get_sandbox(self, sandbox_id):
                return SimpleNamespace(
                    id=sandbox_id,
                    state="Running",
                )

            def get_sandbox_client(self, sandbox_id):
                return SimpleNamespace(sandbox_id=sandbox_id)

        client = Client()
        config = AgentSandboxConfig(
            subscription_id="subscription",
            resource_group="resource-group",
            sandbox_group="group",
            region="swedencentral",
            image_name="registry/runtime@sha256:digest",
            runtime_kind="hermes",
            command=("python3",),
            args=("/app/start_hermes.py",),
            data_volume_name="hermes-data",
        )

        create_agent_sandbox(
            client,
            config=config,
            disk_id="disk-1",
            token="",
        )

        self.assertEqual(
            client.assert_path,
            "/groups/test/sandboxes",
        )
        self.assertEqual(client.body["entrypoint"], ["python3"])
        self.assertEqual(
            client.body["cmd"],
            ["/app/start_hermes.py"],
        )
        self.assertEqual(
            client.body["lifecycle"]["autoSuspendPolicy"]["mode"],
            "Disk",
        )
        self.assertNotIn("command", client.body)
        self.assertNotIn("args", client.body)

    def test_runtime_process_has_pid_guard_and_private_log(self):
        class Sandbox:
            def __init__(self):
                self.command = ""

            def exec(self, command):
                self.command = command
                return SimpleNamespace(
                    exit_code=0,
                    stdout="",
                    stderr="",
                )

        sandbox = Sandbox()
        config = AgentSandboxConfig(
            subscription_id="subscription",
            resource_group="resource-group",
            sandbox_group="group",
            region="swedencentral",
            image_name="registry/runtime@sha256:digest",
            runtime_kind="hermes",
            command=("python3",),
            args=("/app/start_hermes.py",),
        )

        ensure_sandbox_runtime_process(sandbox, config)

        self.assertIn("/tmp/autopilot-runtime.pid", sandbox.command)
        self.assertIn("/tmp/autopilot-runtime.log", sandbox.command)
        self.assertIn(
            "http://127.0.0.1:18789/health",
            sandbox.command,
        )
        self.assertIn(
            "python3 /app/start_hermes.py",
            sandbox.command,
        )
        self.assertIn(
            "pgrep -f -x",
            sandbox.command,
        )

    def test_stopped_runtime_compute_is_recreated(self):
        class Poller:
            def result(self):
                return None

        class Client:
            def __init__(self):
                self.deleted = []

            def begin_delete_sandbox(self, sandbox_id, polling_timeout):
                self.deleted.append((sandbox_id, polling_timeout))
                return Poller()

        client = Client()

        result = recycle_stopped_agent_sandbox(
            client,
            {"id": "sandbox-1", "state": "Stopped"},
        )

        self.assertIsNone(result)
        self.assertEqual(client.deleted, [("sandbox-1", 600)])


if __name__ == "__main__":
    unittest.main()
