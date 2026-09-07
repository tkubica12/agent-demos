import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.deploy_apps_runtime as deploy_apps_runtime
from scripts.deploy_apps_runtime import load_runtime_tfvars, terraform_workspace_name


class DeployAppsRuntimeTests(unittest.TestCase):
    def test_workspace_name_is_runtime_scoped(self):
        self.assertEqual(terraform_workspace_name("openclaw"), "autopilot-openclaw")
        self.assertEqual(terraform_workspace_name("hermes"), "autopilot-hermes")

    def test_load_runtime_tfvars_rejects_wrong_runtime(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "generated.app.auto.tfvars.json"
            path.write_text(json.dumps({"agent_runtime": "openclaw"}), encoding="utf-8")

            with patch.object(deploy_apps_runtime, "runtime_app_tfvars_path", return_value=path):
                with self.assertRaises(ValueError):
                    load_runtime_tfvars("hermes")

    def test_custom_worker_state_uses_separate_tfvars_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hermes2" / "generated.app.auto.tfvars.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps({"agent_runtime": "hermes", "autopilot_name": "hermes2"}),
                encoding="utf-8",
            )
            with patch.object(deploy_apps_runtime, "runtime_app_tfvars_path", return_value=path):
                payload = load_runtime_tfvars("hermes", "hermes2")

        self.assertEqual(payload["autopilot_name"], "hermes2")

    def test_activate_runtime_tfvars_writes_active_runtime_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_path = root / ".local" / "hermes" / "apps" / "generated.app.auto.tfvars.json"
            runtime_path.parent.mkdir(parents=True)
            runtime_path.write_text(json.dumps({"agent_runtime": "hermes", "autopilot_name": "hermes"}), encoding="utf-8")
            apps_dir = root / "terraform" / "apps"
            apps_dir.mkdir(parents=True)

            with patch.object(deploy_apps_runtime, "runtime_app_tfvars_path", return_value=runtime_path), patch.object(
                deploy_apps_runtime, "APPS_DIR", apps_dir
            ):
                deploy_apps_runtime.activate_runtime_tfvars("hermes")

            self.assertEqual(
                json.loads((apps_dir / "generated.runtime.auto.tfvars.json").read_text(encoding="utf-8"))["agent_runtime"],
                "hermes",
            )

    def test_capture_omits_deployment_secrets_and_keeps_current_native_urls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "outputs.json"
            groups = {"gateway": {"id": "current-group"}}
            path.write_text(json.dumps({"sandbox_groups": groups, "bridge_url": "https://native",
                                        "runtime_disk_image_id": "runtime-disk"}))
            with patch.object(deploy_apps_runtime, "runtime_outputs_path", return_value=path), patch.object(
                deploy_apps_runtime, "terraform_output",
                return_value={"sandbox_groups": groups, "deployment_config": {"api_server_key": "not-for-output"}},
            ):
                deploy_apps_runtime.capture_runtime_outputs("hermes", "autopilot-hermes")
            captured = json.loads(path.read_text())
        self.assertEqual(captured["bridge_url"], "https://native")
        self.assertEqual(captured["runtime_disk_image_id"], "runtime-disk")
        self.assertNotIn("deployment_config", captured)

    def test_capture_does_not_reuse_url_from_deleted_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "outputs.json"
            path.write_text(json.dumps({"sandbox_groups": {"gateway": {"id": "old"}}, "bridge_url": "https://old"}))
            with patch.object(deploy_apps_runtime, "runtime_outputs_path", return_value=path), patch.object(
                deploy_apps_runtime, "terraform_output",
                return_value={"sandbox_groups": {"gateway": {"id": "new"}}},
            ):
                deploy_apps_runtime.capture_runtime_outputs("hermes", "autopilot-hermes")
            captured = json.loads(path.read_text())
        self.assertNotIn("bridge_url", captured)


if __name__ == "__main__":
    unittest.main()
