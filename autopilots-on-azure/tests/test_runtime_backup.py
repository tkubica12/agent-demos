import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtimes" / "hermes"))

import runtime_backup
from runtime_backup import BackupError, BackupLoop, create_backup, home_fingerprint, restore_latest


def completed(returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


def fake_backup(arguments, home, **account):
    Path(arguments[2]).write_bytes(b"zip")
    return completed()


class RuntimeBackupTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.home = self.root / "hermes"
        self.volume = self.root / "backup"
        self.volume.mkdir()
        mounted = patch.object(runtime_backup.os.path, "ismount", side_effect=lambda path: Path(path) == self.volume)
        mounted.start()
        self.addCleanup(mounted.stop)

    def write(self, relative: str, content: bytes = b"x", stamp: int = 1_000) -> Path:
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        os.utime(path, (stamp, stamp))
        return path

    def test_fingerprint_ignores_files_idle_hermes_rewrites(self):
        noise = [
            "logs/agent.log", "state/gateway.heartbeat", "gateway_state.json", "channel_directory.json",
            "cron/ticker_heartbeat", "cron/ticker_last_success", "cron/.tick.lock", "kanban.db-shm",
            "skills/__pycache__/x.pyc",
        ]
        self.write("state.db", b"db")
        self.write("kanban.db-wal", b"")
        for relative in noise:
            self.write(relative)
        before = home_fingerprint(self.home)

        for relative in noise:
            self.write(relative, b"changed", stamp=2_000)
        self.write("kanban.db-wal", b"", stamp=2_000)

        self.assertEqual(home_fingerprint(self.home), before)

    def test_fingerprint_tracks_worker_state(self):
        self.write("state.db", b"db")
        self.write("state.db-wal", b"frames")
        before = home_fingerprint(self.home)

        self.write("state.db-wal", b"frames", stamp=2_000)
        after_wal = home_fingerprint(self.home)
        self.write("skills/release-notes/logs/example.md")
        after_skill = home_fingerprint(self.home)

        self.assertNotEqual(after_wal, before)
        self.assertNotEqual(after_skill, after_wal)

    def test_backup_is_staged_locally_published_atomically_and_pruned(self):
        self.home.mkdir()
        names = [f"hermes-backup-2026-10-31-12000{index}-000000.zip" for index in range(3)]
        with (
            patch.object(runtime_backup, "backup_name", side_effect=names),
            patch.object(runtime_backup, "run_hermes", side_effect=fake_backup) as hermes,
        ):
            published = [create_backup(self.home, self.volume, keep=2) for _ in range(3)]

        arguments = hermes.call_args.args[0]
        self.assertEqual(arguments[0:2], ["backup", "-o"])
        self.assertEqual(Path(arguments[2]).parent, self.home / "backups")
        self.assertEqual(arguments[3:], ["-k", "0"])
        self.assertEqual(sorted(path.name for path in self.volume.iterdir()), [path.name for path in published[1:]])
        self.assertEqual(list((self.home / "backups").iterdir()), [])

    def test_incomplete_native_backup_is_never_published(self):
        self.home.mkdir()
        with patch.object(runtime_backup, "run_hermes", return_value=completed(1, "Archive kept, but 1 file")):
            with self.assertRaisesRegex(BackupError, "hermes backup failed"):
                create_backup(self.home, self.volume)

        self.assertEqual(list(self.volume.iterdir()), [])

    def test_backup_requires_the_mounted_volume(self):
        self.home.mkdir()
        with patch.object(runtime_backup, "run_hermes") as hermes:
            with self.assertRaisesRegex(BackupError, "not mounted"):
                create_backup(self.home, self.root / "unmounted")
        hermes.assert_not_called()

    def test_existing_home_is_never_overwritten(self):
        self.write("config.yaml")
        (self.volume / "hermes-backup-2026.zip").write_bytes(b"zip")
        with patch.object(runtime_backup, "run_hermes") as hermes:
            self.assertIsNone(restore_latest(self.home, self.volume, worker_id="worker-1"))
        hermes.assert_not_called()

    def test_empty_home_without_backups_starts_a_fresh_worker(self):
        with patch.object(runtime_backup, "run_hermes") as hermes:
            self.assertIsNone(restore_latest(self.home, self.volume, worker_id="worker-1"))
        hermes.assert_not_called()

    def test_empty_home_restores_the_newest_archive(self):
        for name in ("hermes-backup-2026-10-31-120000-000000.zip", "hermes-backup-2026-10-31-130000-000000.zip"):
            (self.volume / name).write_bytes(b"zip")

        def imported(arguments, home, **account):
            self.write("local/worker.json", json.dumps({"workerId": "worker-1"}).encode())
            return completed(0, "Import complete: 3 files restored")

        with patch.object(runtime_backup, "run_hermes", side_effect=imported) as hermes:
            archive = restore_latest(self.home, self.volume, worker_id="worker-1")

        self.assertEqual(archive.name, "hermes-backup-2026-10-31-130000-000000.zip")
        self.assertEqual(hermes.call_args.args[0], ["import", "--force", str(archive)])

    def test_restore_rejects_another_workers_archive(self):
        (self.volume / "hermes-backup-1.zip").write_bytes(b"zip")

        def imported(arguments, home, **account):
            self.write("local/worker.json", json.dumps({"workerId": "worker-2"}).encode())
            return completed(0, "Import complete")

        with patch.object(runtime_backup, "run_hermes", side_effect=imported):
            with self.assertRaisesRegex(BackupError, "belongs to Worker 'worker-2'"):
                restore_latest(self.home, self.volume, worker_id="worker-1")

    def test_failed_import_stops_startup(self):
        (self.volume / "hermes-backup-1.zip").write_bytes(b"zip")
        with patch.object(runtime_backup, "run_hermes", return_value=completed(1, "backup archive is damaged")):
            with self.assertRaisesRegex(BackupError, "import of hermes-backup-1.zip failed"):
                restore_latest(self.home, self.volume, worker_id="worker-1")

    def test_unreadable_volume_stops_startup_instead_of_starting_a_fresh_worker(self):
        (self.volume / "hermes-backup-1.zip").write_bytes(b"zip")
        with (
            patch.object(runtime_backup.os, "listdir", side_effect=OSError(5, "Input/output error")),
            patch.object(runtime_backup, "run_hermes") as hermes,
        ):
            with self.assertRaises(OSError):
                restore_latest(self.home, self.volume, worker_id="worker-1")
        hermes.assert_not_called()

    def test_backup_waits_for_a_concurrent_backup_of_the_same_home(self):
        self.home.mkdir()
        busy = completed(runtime_backup.BACKUP_BUSY_EXIT_CODE, "another Hermes backup is already running")
        outcomes = iter([busy, busy, None])

        def hermes(arguments, home, **account):
            outcome = next(outcomes)
            return outcome if outcome else fake_backup(arguments, home)

        with (
            patch.object(runtime_backup, "run_hermes", side_effect=hermes) as native,
            patch.object(runtime_backup.time, "sleep") as sleep,
        ):
            published = create_backup(self.home, self.volume)

        self.assertEqual(native.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertTrue(published.is_file())

    def test_loop_backs_up_only_after_changes(self):
        self.write("state.db", b"db")
        loop = BackupLoop(self.home, self.volume)
        with patch.object(runtime_backup, "run_hermes", side_effect=fake_backup) as hermes:
            first = loop.tick()
            unchanged = loop.tick()
            self.write("memories/MEMORY.md", b"new fact")
            changed = loop.tick()

        self.assertIsNotNone(first)
        self.assertIsNone(unchanged)
        self.assertIsNotNone(changed)
        self.assertEqual(hermes.call_count, 2)
        self.assertEqual(loop.status()["lastArchive"], changed.name)


if __name__ == "__main__":
    unittest.main()
