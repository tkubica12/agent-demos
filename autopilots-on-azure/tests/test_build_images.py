import json
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
        with patch.object(build_images, "run", return_value=result) as run_mock:
            digest = build_images.acr_build(
                registry="registry", repository="hermes-runtime", dockerfile="Dockerfile", context=".", tag="t",
                build_args={"HERMES_BASE_IMAGE": "registry.azurecr.io/hermes-agent-upstream@sha256:abc"},
            )
        command = run_mock.call_args.args[0]
        self.assertEqual(digest, "sha256:1")
        self.assertIn("HERMES_BASE_IMAGE=registry.azurecr.io/hermes-agent-upstream@sha256:abc", command)


if __name__ == "__main__":
    unittest.main()
