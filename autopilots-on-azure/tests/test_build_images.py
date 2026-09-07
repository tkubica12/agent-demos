import json
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
