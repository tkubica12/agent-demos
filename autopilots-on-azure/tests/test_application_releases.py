from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.application_releases import PYTHON_BASE_IMAGE, build_release


class ApplicationReleaseTests(unittest.TestCase):
    def test_windows_lock_line_endings_do_not_require_a_new_runtime_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtimes" / "hermes"
            runtime.mkdir(parents=True)
            (runtime / "start_hermes.py").write_bytes(b"pass\n")
            lock = runtime / "uv.lock"
            lock.write_bytes(b"version = 1\r\n")
            windows = build_release("runtime", root=root)
            lock.write_bytes(b"version = 1\n")
            linux = build_release("runtime", root=root)
            self.assertEqual(windows.digest, linux.digest)
            lock.write_bytes(b"version = 2\n")
            self.assertNotEqual(build_release("runtime", root=root).digest, linux.digest)

    def test_identical_source_and_lock_export_produce_identical_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bridge").mkdir()
            (root / "bridge" / "app.py").write_text("value = 1\n", encoding="utf-8")
            with patch("scripts.application_releases.run", return_value=SimpleNamespace(stdout="package==1 --hash=sha256:abc\n")):
                first = build_release("gateway", root=root)
                second = build_release("gateway", root=root)
            self.assertEqual(first.digest, second.digest)
            self.assertEqual(first.archive, second.archive)
            with zipfile.ZipFile(io.BytesIO(first.archive)) as archive:
                manifest = archive.read("release.json")
                self.assertEqual(hashlib.sha256(manifest).hexdigest(), first.digest)
                payload = json.loads(manifest)
                self.assertEqual(payload["base_image"], PYTHON_BASE_IMAGE)
                self.assertEqual(set(payload["files"]), {"bridge/app.py", "requirements.txt"})
                for name, digest in payload["files"].items():
                    self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), digest)

    def test_role_mcp_release_carries_its_package_and_data_directories(self):
        source = {"source_directory": "roles/pm/mcp", "package": "pm_tools_mcp", "data_directories": ["config"]}
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "roles" / "pm" / "mcp"
            for name in ("pm_tools_mcp/server.py", "config/projects.toml", "tests/test_server.py"):
                (project / name).parent.mkdir(parents=True, exist_ok=True)
                (project / name).write_text("x\n", encoding="utf-8")
            with patch("scripts.application_releases.run", return_value=SimpleNamespace(stdout="package==1 --hash=sha256:abc\n")):
                release = build_release("pm-tools", root=Path(directory), source=source)
                with self.assertRaisesRegex(ValueError, "missing"):
                    build_release("pm-tools", root=Path(directory), source={**source, "data_directories": ["knowledge"]})
            with self.assertRaisesRegex(ValueError, "relative"):
                build_release("pm-tools", root=Path(directory), source={**source, "source_directory": "../outside"})
        with zipfile.ZipFile(io.BytesIO(release.archive)) as archive:
            self.assertEqual(set(archive.namelist()), {
                "pm_tools_mcp/server.py", "config/projects.toml", "requirements.txt", "release.json",
            })

    def test_application_change_does_not_change_base_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bridge").mkdir()
            source = root / "bridge" / "app.py"
            source.write_text("value = 1\n", encoding="utf-8")
            with patch("scripts.application_releases.run", return_value=SimpleNamespace(stdout="package==1\n")):
                first = build_release("gateway", root=root)
                source.write_text("value = 2\n", encoding="utf-8")
                second = build_release("gateway", root=root)
            self.assertNotEqual(first.digest, second.digest)
            self.assertEqual(first.directory.rsplit("/", 1)[0], second.directory.rsplit("/", 1)[0])

    def test_unknown_role_cannot_package_arbitrary_repository_paths(self):
        with self.assertRaisesRegex(ValueError, "No thin application"):
            build_release("../outside")


if __name__ == "__main__":
    unittest.main()
