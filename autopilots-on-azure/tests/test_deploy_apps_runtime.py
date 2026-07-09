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


if __name__ == "__main__":
    unittest.main()
