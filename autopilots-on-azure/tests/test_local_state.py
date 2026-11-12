from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from bridge.local_state import locked_file


class LocalStateLockTests(unittest.TestCase):
    def test_another_process_waits_until_local_transaction_releases_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ready = root / "ready"
            completed = root / "completed"
            script = """
import sys
from pathlib import Path
from bridge.local_state import locked_file
root = Path(sys.argv[1])
(root / 'ready').touch()
with locked_file(root / 'state.lock'):
    (root / 'completed').touch()
"""
            with locked_file(root / "state.lock"):
                process = subprocess.Popen(
                    [sys.executable, "-c", script, str(root)],
                    cwd=Path(__file__).resolve().parents[1],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                try:
                    deadline = time.monotonic() + 10
                    while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(ready.exists(), "Child did not reach the local lock.")
                    time.sleep(0.1)
                    self.assertIsNone(process.poll(), "Child unexpectedly completed while parent held lock.")
                    self.assertFalse(completed.exists())
                except BaseException:
                    process.kill()
                    process.communicate()
                    raise
            _, error = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, error.decode())
            self.assertTrue(completed.exists())


if __name__ == "__main__":
    unittest.main()
