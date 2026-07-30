import base64
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from autopilots_identity.collaboration_mcp import generated_app_files
from bridge.generated_apps import (
    AUTO_SUSPEND_SECONDS,
    DEFAULT_TTL_SECONDS,
    GeneratedAppsManager,
    GeneratedAppsSettings,
    validate_generated_app_payload,
)


def payload():
    return {
        "name": "Status dashboard",
        "runtime": "python",
        "files": [
            {
                "path": "app.py",
                "contentBase64": base64.b64encode(
                    b"print('hello')"
                ).decode("ascii"),
            }
        ],
        "participantEmails": ["user@example.com"],
        "requestingUserId": "user-object-1",
        "requestingUserEmail": "user@example.com",
        "port": 8000,
        "startCommand": ["python3", "app.py"],
        "testCommand": ["python3", "-m", "unittest"],
        "egressHosts": [],
    }


class Poller:
    def __init__(self, value):
        self.value = value

    def result(self):
        return self.value


class FakeSandbox:
    def __init__(self):
        self.sandbox_id = "sandbox-1"
        self._sbx_path = "/groups/generated/sandboxes/sandbox-1"
        self.files = {}
        self.commands = []
        self.lifecycle = None
        self.ports = None
        self.deleted = False
        self.fail_on = ""

    def write_file(self, path, content, create_dirs=True):
        self.files[path] = content

    def exec(self, command, working_directory=None):
        self.commands.append((command, working_directory))
        if self.fail_on and self.fail_on in command:
            return SimpleNamespace(
                exit_code=1,
                stdout="",
                stderr="failed",
            )
        if "tail -n" in command:
            return SimpleNamespace(exit_code=0, stdout="", stderr="")
        return SimpleNamespace(exit_code=0, stdout="ok", stderr="")

    def set_lifecycle_policy(self, policy):
        self.lifecycle = policy

    def _dp_post(self, path, body):
        self.ports = body
        return {
            "port": body["port"],
            "url": "https://generated.example",
            "auth": body["auth"],
        }

    def begin_delete(self, polling_timeout):
        self.deleted = True
        return Poller(None)

    def ensure_running(self, timeout):
        self.ensure_timeout = timeout

    def get(self):
        return SimpleNamespace(
            ports=[
                SimpleNamespace(
                    port=8000,
                    url="https://generated.example",
                )
            ]
        )


class FakeGroup:
    _group_path = "/groups/generated"

    def __init__(self):
        self.child = FakeSandbox()
        self.created = None
        self.items = []

    def list_sandboxes(self, labels=None):
        return [
            SimpleNamespace(
                id=item["id"],
                state=item.get("state"),
                labels=item.get("labels", {}),
                lifecycle=item.get("lifecycle"),
                ports=[
                    SimpleNamespace(
                        port=port.get("port"),
                        url=port.get("url"),
                    )
                    for port in item.get("ports", [])
                ],
            )
            for item in self.items
        ]

    def begin_create_sandbox(self, **kwargs):
        self.created = kwargs
        return Poller(self.child)

    def begin_delete_sandbox(self, sandbox_id, polling_timeout):
        self.items = [
            item for item in self.items if item["id"] != sandbox_id
        ]
        return Poller(None)

    def get_sandbox_client(self, sandbox_id):
        return self.child


class GeneratedAppsTests(unittest.TestCase):
    def manager(self, group):
        return GeneratedAppsManager(
            GeneratedAppsSettings(
                subscription_id="sub",
                resource_group="rg",
                sandbox_group="generated",
                region="swedencentral",
                worker_id="hermes2",
            ),
            credential_factory=lambda: object(),
            client_factory=lambda *_args, **_kwargs: group,
        )

    def test_payload_rejects_path_traversal(self):
        value = payload()
        value["files"][0]["path"] = "../secret"

        with self.assertRaisesRegex(ValueError, "Unsafe"):
            validate_generated_app_payload(value)

    def test_deploy_applies_identity_lifecycle_and_egress(self):
        group = FakeGroup()
        manager = self.manager(group)

        result = manager.deploy_app(payload())

        self.assertEqual(result["url"], "https://generated.example")
        self.assertEqual(
            group.child.files["/app/app.py"],
            b"print('hello')",
        )
        self.assertEqual(
            group.created["disk"],
            "python-3.12",
        )
        self.assertEqual(
            group.created["egress_policy"].default_action,
            "Deny",
        )
        self.assertTrue(
            group.child.lifecycle.auto_delete.enabled
        )
        self.assertEqual(
            group.child.lifecycle.auto_delete.delete_interval_seconds,
            DEFAULT_TTL_SECONDS,
        )
        self.assertEqual(
            group.child.lifecycle.auto_suspend.interval,
            AUTO_SUSPEND_SECONDS,
        )
        self.assertEqual(
            group.created["labels"]["generatedAppOwner"],
            manager._owner_hash("user-object-1"),
        )
        port = group.child.ports
        self.assertEqual(
            port["auth"]["entraId"]["emails"],
            ["user@example.com"],
        )
        self.assertEqual(port["activationMode"], "OnDemand")
        self.assertEqual(
            result["retentionSeconds"],
            DEFAULT_TTL_SECONDS,
        )

    def test_generated_app_files_stay_in_governed_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            app = home / "workspace" / "generated-apps" / "demo"
            app.mkdir(parents=True)
            (app / "index.html").write_text(
                "<h1>Hello</h1>",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"HERMES_HOME": str(home)},
            ):
                files = generated_app_files(str(app))
                with self.assertRaisesRegex(ValueError, "must be under"):
                    generated_app_files(str(home))

        self.assertEqual(files[0]["path"], "index.html")

    def test_failed_update_preserves_previous_app(self):
        group = FakeGroup()
        manager = self.manager(group)
        group.items = [
            {
                "id": "old-sandbox",
                "state": "Running",
                "labels": {
                    "generatedAppWorker": "hermes2",
                    "generatedAppOwner": manager._owner_hash(
                        "user-object-1"
                    ),
                    "generatedAppId": "a" * 24,
                },
                "ports": [],
                "lifecycle": None,
            }
        ]
        group.child.fail_on = "false"
        value = payload()
        value["appId"] = "a" * 24
        value["installCommand"] = ["false"]

        with self.assertRaisesRegex(RuntimeError, "installCommand failed"):
            manager.deploy_app(value)

        self.assertTrue(group.child.deleted)
        self.assertEqual(group.items[0]["id"], "old-sandbox")

    def test_inventory_is_scoped_to_requesting_user(self):
        group = FakeGroup()
        manager = self.manager(group)
        owner = manager._owner_hash("user-object-1")
        other = manager._owner_hash("user-object-2")
        lifecycle = SimpleNamespace(
            auto_suspend=SimpleNamespace(interval=300),
            auto_delete=SimpleNamespace(
                delete_interval_seconds=86400
            ),
        )
        group.items = [
            {
                "id": "owned",
                "state": "Stopped",
                "labels": {
                    "generatedAppWorker": "hermes2",
                    "generatedAppOwner": owner,
                    "generatedAppId": "a" * 24,
                    "generatedAppName": "Owned",
                },
                "ports": [
                    {
                        "port": 8000,
                        "url": "https://owned.example",
                    }
                ],
                "lifecycle": lifecycle,
            },
            {
                "id": "other",
                "state": "Running",
                "labels": {
                    "generatedAppWorker": "hermes2",
                    "generatedAppOwner": other,
                    "generatedAppId": "b" * 24,
                    "generatedAppName": "Other",
                },
                "ports": [],
                "lifecycle": lifecycle,
            },
        ]

        result = manager.list_apps("user-object-1")

        self.assertEqual(len(result["apps"]), 1)
        self.assertEqual(result["apps"][0]["appId"], "a" * 24)
        self.assertEqual(
            result["apps"][0]["retentionBasis"],
            "after-suspension",
        )

    def test_renew_updates_native_lifecycle_policy(self):
        group = FakeGroup()
        manager = self.manager(group)
        group.items = [
            {
                "id": "owned",
                "state": "Stopped",
                "labels": {
                    "generatedAppWorker": "hermes2",
                    "generatedAppOwner": manager._owner_hash(
                        "user-object-1"
                    ),
                    "generatedAppId": "a" * 24,
                    "generatedAppName": "Owned",
                },
                "ports": [],
                "lifecycle": None,
            }
        ]

        result = manager.renew_app(
            "a" * 24,
            "user-object-1",
            259200,
        )

        self.assertEqual(result["status"], "renewed")
        self.assertEqual(result["retentionSeconds"], 259200)
        self.assertEqual(
            group.child.lifecycle.auto_delete.delete_interval_seconds,
            259200,
        )

    def test_delete_cannot_cross_user_boundary(self):
        group = FakeGroup()
        manager = self.manager(group)
        group.items = [
            {
                "id": "other",
                "state": "Running",
                "labels": {
                    "generatedAppWorker": "hermes2",
                    "generatedAppOwner": manager._owner_hash(
                        "user-object-2"
                    ),
                    "generatedAppId": "a" * 24,
                },
                "ports": [],
                "lifecycle": None,
            }
        ]

        result = manager.delete_app(
            "a" * 24,
            "user-object-1",
        )

        self.assertFalse(result["deleted"])
        self.assertEqual(group.items[0]["id"], "other")


if __name__ == "__main__":
    unittest.main()
