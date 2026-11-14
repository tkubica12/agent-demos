import json
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import build_images
from scripts.build_images import write_worker_images


class BuildImagesTests(unittest.TestCase):
    def test_one_build_updates_named_workers_without_changing_their_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / f"{name}.json" for name in ("hermes", "hermes2")]
            for path in paths:
                path.write_text(
                    json.dumps({"agent_runtime": "hermes", "autopilot_name": path.stem}),
                    encoding="utf-8",
                )
            write_worker_images(paths, {"runtime_image": "image@sha256:123"}, runtime="hermes")
            for path in paths:
                actual = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(actual["autopilot_name"], path.stem)
                self.assertEqual(actual["runtime_image"], "image@sha256:123")

    def test_wrong_runtime_is_rejected_before_any_state_is_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / f"{runtime}.json" for runtime in ("hermes", "invalid")]
            for path in paths:
                path.write_text(json.dumps({"agent_runtime": path.stem}), encoding="utf-8")
            with self.assertRaises(ValueError):
                write_worker_images(paths, {"runtime_image": "new"}, runtime="hermes")
            self.assertNotIn("runtime_image", json.loads(paths[0].read_text(encoding="utf-8")))

    def test_official_hermes_image_is_mirrored_by_digest_only_when_missing(self):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            return SimpleNamespace(returncode=1 if command[2] == "manifest" else 0, stdout="")

        with patch.object(build_images, "run", side_effect=run):
            base = build_images.import_hermes_upstream("registry", "registry.azurecr.io")
        self.assertEqual(base, f"registry.azurecr.io/hermes-agent-upstream@{build_images.HERMES_UPSTREAM_DIGEST}")
        self.assertEqual(calls[1][:3], ["az", "acr", "import"])
        self.assertIn(f"docker.io/nousresearch/hermes-agent@{build_images.HERMES_UPSTREAM_DIGEST}", calls[1])
        self.assertFalse(any("--username" in part or "--password" in part for part in calls[1]))

        calls.clear()
        with patch.object(build_images, "run", return_value=SimpleNamespace(returncode=0, stdout="")) as run_mock:
            build_images.import_hermes_upstream("registry", "registry.azurecr.io")
        self.assertEqual(run_mock.call_count, 1)

    def test_runtime_build_receives_the_mirrored_base_image(self):
        result = SimpleNamespace(stdout=json.dumps({"outputImages": [{"repository": "hermes-runtime", "digest": "sha256:1"}]}))
        with tempfile.TemporaryDirectory() as temporary, patch.object(build_images, "run", return_value=result) as run_mock:
            manifest = Path(temporary) / "deployment-manifest.json"
            dockerfile = Path(temporary) / "runtimes/hermes/Dockerfile"
            dockerfile.parent.mkdir(parents=True)
            dockerfile.write_bytes(b"FROM source")
            manifest.write_text(json.dumps({
                "source_commit": "a" * 40,
                "files": {"runtimes/hermes/Dockerfile": hashlib.sha256(dockerfile.read_bytes()).hexdigest()},
            }))
            with patch.object(build_images, "REPO_ROOT", Path(temporary)):
                digest = build_images.acr_build(
                    registry="registry", repository="hermes-runtime", dockerfile="runtimes/hermes/Dockerfile",
                    context=".", tag="t", build_args={
                        "HERMES_BASE_IMAGE": "registry.azurecr.io/hermes-agent-upstream@sha256:abc",
                    },
                )
        command = run_mock.call_args.args[0]
        self.assertEqual(digest, "sha256:1")
        self.assertIn("HERMES_BASE_IMAGE=registry.azurecr.io/hermes-agent-upstream@sha256:abc", command)

    def test_pinned_git_context_excludes_ignored_files_and_rejects_dirty_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def git(*arguments):
                return subprocess.check_output(["git", *arguments], cwd=root, stderr=subprocess.DEVNULL, text=True)
            git("init", "--quiet")
            (root / ".gitignore").write_text("node_modules/\n__pycache__/\n.local/\n")
            (root / "Dockerfile").write_text("FROM source\n")
            git("add", ".gitignore", "Dockerfile")
            git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--quiet",
                "-m", "Test source\n\nCo-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>")
            for name in ("node_modules/tsc", "__pycache__/module.pyc", ".local/secrets.json"):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("test-only ignored content")
            with patch.object(build_images, "REPO_ROOT", root):
                with build_images.pinned_build_context(".") as staged:
                    self.assertEqual({p.relative_to(staged).as_posix() for p in staged.rglob("*") if p.is_file()},
                                     {"Dockerfile", ".gitignore"})
                (root / "Dockerfile").write_text("FROM changed\n")
                with self.assertRaisesRegex(ValueError, "dirty"):
                    with build_images.pinned_build_context("."):
                        self.fail("Dirty source must not be exported.")

    def test_git_free_context_includes_only_verified_pinned_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pinned = {"Dockerfile": b"FROM source", "frontend/index.html": b"pinned source"}
            for name, content in {**pinned, "frontend/node_modules/.bin/tsc": b"windows binary",
                                  ".local/secrets.json": b"test-only secret", ".env": b"test-only secret"}.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            (root / "deployment-manifest.json").write_text(json.dumps({
                "source_commit": "a" * 40,
                "files": {name: hashlib.sha256(value).hexdigest() for name, value in pinned.items()},
            }))
            with patch.object(build_images, "REPO_ROOT", root):
                with build_images.pinned_build_context(".") as staged:
                    self.assertEqual({path.relative_to(staged).as_posix() for path in staged.rglob("*") if path.is_file()},
                                     set(pinned))
                    self.assertEqual((staged / "frontend/index.html").read_bytes(), b"pinned source")
                (root / "frontend/index.html").write_bytes(b"changed source")
                with self.assertRaisesRegex(ValueError, "differs"):
                    with build_images.pinned_build_context("."):
                        self.fail("Changed source must not be staged.")

    def test_git_free_context_requires_inventory_and_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "deployment-manifest.json"
            with patch.object(build_images, "REPO_ROOT", root):
                for values in ({"source_commit": "a" * 40},
                               {"source_commit": "a" * 40, "files": {"../secret": "ignored"}}):
                    manifest.write_text(json.dumps(values))
                    with self.assertRaises(ValueError):
                        with build_images.pinned_build_context("."):
                            self.fail("An unverified source inventory must not be staged.")


if __name__ == "__main__":
    unittest.main()
