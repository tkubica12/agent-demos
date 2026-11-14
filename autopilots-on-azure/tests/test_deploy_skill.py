from __future__ import annotations

import json
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import zipfile

from scripts.deploy_simulator_environment import load_stack_configs
from scripts.prebuilt_images import load_manifest


SKILL = Path(__file__).resolve().parents[1] / "skills" / "autopilots-deploy"
SHELLS = [name for name in ("powershell", "pwsh") if shutil.which(name)]


class DeploySkillTests(unittest.TestCase):
    def test_frontmatter_and_relative_links(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        frontmatter = re.match(r"\A---\nname: ([a-z0-9-]+)\ndescription: ([^\n]+)\n---\n", text)
        self.assertIsNotNone(frontmatter)
        self.assertEqual(frontmatter[1], "autopilots-deploy")
        for document in SKILL.rglob("*.md"):
            content = document.read_text(encoding="utf-8")
            for link in re.findall(r"\[[^\]]+\]\(([^)]+)\)", content):
                if "://" not in link:
                    with self.subTest(document=document.name, link=link):
                        self.assertTrue((document.parent / link).is_file())
            for snippet in re.findall(r"@'\n(.*?)\n'@ \| uv run python", content, re.DOTALL):
                compile(snippet, str(document), "exec")

    def test_templates_use_deployer_validation_and_reject_placeholder_release(self):
        with self.assertRaises(ValueError):
            load_manifest(SKILL / "assets" / "image-release.example.json")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copy(SKILL / "assets" / "dns-zones.example.json", root / "dns-zones.json")
            release = {
                "schema": 1, "source_commit": "a" * 40,
                "images": {name: f"ghcr.io/example/{name}@sha256:" + "b" * 64
                           for name in ("runtime", "gateway", "simulator")},
            }
            (root / "image-release.json").write_text(json.dumps(release))
            for template in (SKILL / "assets").glob("*.toml"):
                target = root / template.name
                text = template.read_text(encoding="utf-8")
                target.write_text(text)
                configs = load_stack_configs(target, [])
                count = 7 if "seven" in template.name else 2
                self.assertEqual(len(configs), count)
                self.assertTrue(all(config["network_mode"] == "private" for config in configs))
                self.assertEqual(len({config["platform"]["existing_resource_group_name"] for config in configs}), count)
                self.assertEqual(len(load_stack_configs(target, ["agent-1"])), 1)
                if count == 7:
                    self.assertEqual(load_stack_configs(target, ["agent-dev"])[0]["foundry_mode"], "shared")
                expiry = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()
                target.write_text(
                    f'demo_acknowledge = "test-data-only"\ndemo_expires_at = "{expiry}"\n'
                    + text.replace('auth_mode = "entra"', 'auth_mode = "demo"')
                )
                self.assertTrue(all(config["auth_mode"] == "demo" for config in load_stack_configs(target, [])))

    @unittest.skipUnless(SHELLS, "PowerShell parser unavailable")
    def test_powershell_scripts_parse_and_release_reader_fails_closed(self):
        script = SKILL / "scripts" / "Read-ImageRelease.ps1"
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "images.json"
            release = {
                "schema": 1, "source_commit": "a" * 40,
                "images": {name: f"ghcr.io/example/{name}@sha256:" + "b" * 64
                           for name in ("runtime", "gateway", "simulator")},
            }
            manifest.write_text(json.dumps(release))
            for shell in SHELLS:
                for path in (SKILL / "scripts").glob("*.ps1"):
                    command = (
                        "$tokens=$null;$errors=$null;"
                        f"[System.Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$tokens,[ref]$errors)|Out-Null;"
                        "if($errors.Count){$errors|Out-String|Write-Host;exit 1}"
                    )
                    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", command],
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                for commit, expected in (("a" * 40, 0), ("c" * 40, 1)):
                    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File", str(script),
                                             "-Path", str(manifest), "-Commit", commit],
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                release["images"]["gateway"] = "ghcr.io/example/gateway:latest"
                manifest.write_text(json.dumps(release))
                result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File", str(script),
                                         "-Path", str(manifest), "-Commit", "a" * 40],
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                release["images"]["gateway"] = "ghcr.io/example/gateway@sha256:" + "b" * 64
                manifest.write_text(json.dumps(release))

    @unittest.skipUnless(SHELLS, "PowerShell unavailable")
    def test_initializer_extracts_matching_source_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "package"
            skill = package / ".agents" / "skills" / "autopilots-deploy"
            shutil.copytree(SKILL, skill)
            commit = "a" * 40
            archive = package / "source.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("autopilots-on-azure/scripts/deploy_simulator_environment.py", "")
                bundle.writestr("autopilots-on-azure/deployment-manifest.json",
                                json.dumps({"source_commit": commit}))
            images = package / "image-release.json"
            images.write_text(json.dumps({
                "schema": 1, "source_commit": commit,
                "images": {name: f"ghcr.io/example/{name}@sha256:" + "b" * 64
                           for name in ("runtime", "gateway", "simulator")},
            }))

            def digest(path):
                return hashlib.sha256(path.read_bytes()).hexdigest()

            (skill / "assets" / "source.lock.json").write_text(json.dumps({
                "commit": commit, "archive": "source.zip", "sha256": digest(archive),
                "sourcePath": "autopilots-on-azure",
                "imageRelease": {"path": "image-release.json", "sha256": digest(images)},
            }))
            manifest = {
                "entryPoint": ".agents/skills/autopilots-deploy/SKILL.md", "source": {"commit": commit},
                "files": [{"path": path.relative_to(package).as_posix(), "sha256": digest(path)}
                          for path in package.rglob("*") if path.is_file()],
            }
            (package / "manifest.json").write_text(json.dumps(manifest))
            for shell in SHELLS:
                work = root / shell
                command = [shell, "-NoProfile", "-NonInteractive", "-File",
                           str(skill / "scripts" / "Initialize-Workspace.ps1"),
                           "-PackageRoot", str(package), "-WorkRoot", str(work)]
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual((work / "state" / "image-release.json").read_bytes(), images.read_bytes())
                self.assertTrue((work / "state" / "stacks.toml").exists())
                second = subprocess.run(command, capture_output=True, text=True)
                self.assertNotEqual(second.returncode, 0)
            images.write_text("{}")
            result = subprocess.run([SHELLS[0], "-NoProfile", "-NonInteractive", "-File",
                                     str(skill / "scripts" / "Initialize-Workspace.ps1"),
                                     "-PackageRoot", str(package), "-WorkRoot", str(root / "tampered")],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "tampered").exists())
