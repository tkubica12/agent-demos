import unittest
import urllib.error
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from scripts.sandbox_runtime import (
    AgentSandboxConfig,
    create_agent_sandbox,
    deploy_agent_sandbox,
    recycle_failed_agent_sandbox,
    require_runtime_ready,
)


class SandboxLauncherTests(unittest.TestCase):
    def test_runtime_readiness_wakes_through_ondemand_health(self):
        healthy = MagicMock()
        healthy.__enter__.return_value.status = 200
        with (
            patch(
                "scripts.sandbox_runtime.urllib.request.urlopen",
                side_effect=[urllib.error.HTTPError("https://runtime", 503, "waking", {}, None), healthy],
            ) as urlopen,
            patch("scripts.sandbox_runtime.time.sleep") as sleep,
        ):
            seconds = require_runtime_ready("https://runtime.example/")
        self.assertGreaterEqual(seconds, 0)
        self.assertEqual(urlopen.call_args.args[0], "https://runtime.example/health")
        sleep.assert_called_once_with(2)

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
        self.assertEqual(client.body["lifecycle"]["autoSuspendPolicy"]["interval"], 300)
        self.assertEqual(client.body["ports"][0]["activationMode"], "OnDemand")
        self.assertNotIn("command", client.body)
        self.assertNotIn("args", client.body)

    def test_only_failed_runtime_compute_is_recreated(self):
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

        stopped = {"id": "sandbox-stopped", "state": "Stopped"}
        self.assertIs(recycle_failed_agent_sandbox(client, stopped), stopped)
        self.assertEqual(client.deleted, [])
        result = recycle_failed_agent_sandbox(
            client,
            {"id": "sandbox-1", "state": "Failed"},
        )

        self.assertIsNone(result)
        self.assertEqual(client.deleted, [("sandbox-1", 600)])

    def test_missing_runtime_is_created_with_its_data_disk_and_native_url(self):
        config = AgentSandboxConfig(
            subscription_id="subscription",
            resource_group="resource-group",
            sandbox_group="group",
            region="swedencentral",
            image_name="registry/runtime@sha256:digest",
            disk_image_id="disk-1",
            runtime_kind="hermes",
            data_volume_name="hermes-data",
        )
        client = Mock()
        client.list_volumes.return_value = []
        created = SimpleNamespace(id="sandbox-1", ports=[SimpleNamespace(port=8642, url="https://runtime.example/")])
        with (
            patch("scripts.sandbox_runtime.create_sandbox_group_client", return_value=client),
            patch("scripts.sandbox_runtime.stale_agent_sandboxes", return_value=[]),
            patch("scripts.sandbox_runtime.existing_agent_sandbox", return_value=None),
            patch("scripts.sandbox_runtime.create_agent_sandbox", return_value=(Mock(), created)) as create,
            patch("scripts.sandbox_runtime.time.sleep") as sleep,
        ):
            result = deploy_agent_sandbox(config)
        client.create_volume.assert_called_once()
        create.assert_called_once_with(client, config=config, disk_id="disk-1")
        client.get_sandbox_client.assert_not_called()
        sleep.assert_not_called()
        self.assertEqual(result.sandbox_id, "sandbox-1")
        self.assertEqual(result.endpoint_url, "https://runtime.example")
        self.assertFalse(result.reused_existing_sandbox)

if __name__ == "__main__":
    unittest.main()
