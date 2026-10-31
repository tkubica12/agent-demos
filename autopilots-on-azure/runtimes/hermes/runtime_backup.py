"""Durable Worker state for a Memory-suspended runtime.

The Hermes home lives on the Sandbox root disk. Native `hermes backup` archives are staged
locally (SQLite snapshots fail on blobfuse) and published atomically to the Worker's
AzureBlob Sandbox volume. A fresh Sandbox restores the newest archive with `hermes import`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UPSTREAM_HERMES = "/opt/hermes/bin/hermes"
HERMES_ACCOUNT = "hermes"
BACKUP_PREFIX = "hermes-backup-"
BACKUP_KEEP = 24
BACKUP_INTERVAL_SECONDS = 60
BACKUP_BUSY_EXIT_CODE = 2
BACKUP_BUSY_WAIT_SECONDS = 120
BACKUP_BUSY_RETRY_SECONDS = 2
HERMES_COMMAND_TIMEOUT_SECONDS = 600
WORKER_MANIFEST = Path("local") / "worker.json"
QUIET_ROOT_DIRECTORIES = frozenset({"logs", "cache", "state", ".telemetry", "backups", "checkpoints"})
QUIET_DIRECTORIES = frozenset({"__pycache__"})
QUIET_FILES = frozenset(
    {"gateway_state.json", "channel_directory.json", "ticker_heartbeat", "ticker_last_success", "processes.json"}
)
QUIET_SUFFIXES = (".lock", ".pid", ".sock", ".db-shm", ".db-journal")


class BackupError(RuntimeError):
    pass


def require_mounted(backup_dir: Path) -> None:
    if not os.path.ismount(backup_dir):
        raise BackupError(f"Worker backup volume is not mounted at {backup_dir}.")


def archives(backup_dir: Path) -> list[Path]:
    """Archives on the volume; a listing error raises, so an unreadable volume never looks empty."""
    names = os.listdir(backup_dir)
    return [
        backup_dir / name
        for name in sorted(names)
        if name.startswith(BACKUP_PREFIX) and name.endswith(".zip")
    ]


def home_fingerprint(home: Path) -> str:
    """Hash file metadata that a backup would preserve, ignoring files idle Hermes rewrites on its own."""
    digest = hashlib.sha256()
    for directory, names, files in os.walk(home):
        relative = Path(directory).relative_to(home)
        names[:] = sorted(
            name
            for name in names
            if name not in QUIET_DIRECTORIES
            and not (relative == Path(".") and name in QUIET_ROOT_DIRECTORIES)
        )
        for name in sorted(files):
            if name in QUIET_FILES or name.endswith(QUIET_SUFFIXES):
                continue
            try:
                stat = (Path(directory) / name).lstat()
            except FileNotFoundError:
                continue
            if name.endswith(".db-wal") and stat.st_size == 0:
                continue
            digest.update(f"{(relative / name).as_posix()}\0{stat.st_mtime_ns}\0{stat.st_size}\n".encode())
    return digest.hexdigest()


def run_hermes(arguments: list[str], home: Path, **account: Any) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["HERMES_HOME"] = str(home)
    environment["HOME"] = str(home / "home")
    return subprocess.run(
        [UPSTREAM_HERMES, *arguments],
        env=environment,
        capture_output=True,
        text=True,
        timeout=HERMES_COMMAND_TIMEOUT_SECONDS,
        **account,
    )


def output_tail(result: subprocess.CompletedProcess[str]) -> str:
    return " ".join(f"{result.stdout}\n{result.stderr}".split()[-60:])


def backup_name() -> str:
    return f"{BACKUP_PREFIX}{datetime.now(timezone.utc).strftime('%Y-%m-%d-%H%M%S-%f')}.zip"


def native_backup(home: Path, local: Path) -> subprocess.CompletedProcess[str]:
    """Run `hermes backup`, waiting while another backup of the same home holds Hermes' backup lock."""
    deadline = time.monotonic() + BACKUP_BUSY_WAIT_SECONDS
    while True:
        result = run_hermes(["backup", "-o", str(local), "-k", "0"], home)
        if result.returncode != BACKUP_BUSY_EXIT_CODE or time.monotonic() >= deadline:
            return result
        time.sleep(BACKUP_BUSY_RETRY_SECONDS)


def create_backup(home: Path, backup_dir: Path, *, keep: int = BACKUP_KEEP) -> Path:
    """Write a complete native archive locally, then publish it to the volume under its final name."""
    require_mounted(backup_dir)
    staging = home / "backups"
    staging.mkdir(parents=True, exist_ok=True)
    name = backup_name()
    local = staging / name
    try:
        result = native_backup(home, local)
        if result.returncode != 0 or not local.is_file():
            raise BackupError(f"hermes backup failed ({result.returncode}): {output_tail(result)}")
        partial = backup_dir / f".{name}.partial"
        shutil.copyfile(local, partial)
        os.replace(partial, backup_dir / name)
    finally:
        local.unlink(missing_ok=True)
    for stale in archives(backup_dir)[:-keep]:
        stale.unlink(missing_ok=True)
    return backup_dir / name


def restore_latest(home: Path, backup_dir: Path, *, worker_id: str = "", **account: Any) -> Path | None:
    """Restore the newest archive into an empty Hermes home; an existing home is never overwritten."""
    if home.exists() and any(home.iterdir()):
        return None
    require_mounted(backup_dir)
    available = archives(backup_dir)
    if not available:
        return None
    archive = available[-1]
    home.mkdir(parents=True, exist_ok=True)
    if "user" in account:
        os.chown(home, account["user"], account["group"])
    result = run_hermes(["import", "--force", str(archive)], home, **account)
    if result.returncode != 0 or "Import complete" not in result.stdout:
        raise BackupError(f"hermes import of {archive.name} failed: {output_tail(result)}")
    manifest_path = home / WORKER_MANIFEST
    if worker_id and manifest_path.exists():
        restored_worker = json.loads(manifest_path.read_text(encoding="utf-8")).get("workerId")
        if restored_worker != worker_id:
            raise BackupError(f"{archive.name} belongs to Worker {restored_worker!r}, not {worker_id!r}.")
    return archive


class BackupLoop:
    """Publish a backup at most once per interval, only after the Hermes home changed."""

    def __init__(self, home: Path, backup_dir: Path, *, interval: float = BACKUP_INTERVAL_SECONDS) -> None:
        self.home = home
        self.backup_dir = backup_dir
        self.interval = interval
        self.last_fingerprint: str | None = None
        self.last_archive = ""
        self.last_succeeded_at = ""
        self.last_error = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="hermes-backup", daemon=True)

    def start(self) -> BackupLoop:
        self._thread.start()
        return self

    def tick(self) -> Path | None:
        fingerprint = home_fingerprint(self.home)
        if fingerprint == self.last_fingerprint:
            return None
        archive = create_backup(self.home, self.backup_dir)
        self.last_fingerprint = fingerprint
        self.last_archive = archive.name
        self.last_succeeded_at = datetime.now(timezone.utc).isoformat()
        self.last_error = ""
        return archive

    def status(self) -> dict[str, str]:
        return {
            "volume": str(self.backup_dir),
            "lastArchive": self.last_archive,
            "lastSucceededAt": self.last_succeeded_at,
            "lastError": self.last_error,
        }

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                archive = self.tick()
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                print(f"Hermes backup failed: {self.last_error}", flush=True)
                continue
            if archive:
                print(f"Hermes backup published: {archive.name}", flush=True)

    def close(self) -> None:
        self._stop.set()


def drop_to_hermes_account() -> None:
    if os.geteuid() != 0:
        return
    import pwd

    entry = pwd.getpwnam(HERMES_ACCOUNT)
    os.setgroups([])
    os.setgid(entry.pw_gid)
    os.setuid(entry.pw_uid)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Publish one Hermes backup to the Worker backup volume now.")
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    drop_to_hermes_account()
    started = time.monotonic()
    archive = create_backup(args.home, args.backup_dir)
    print(json.dumps({"archive": archive.name, "seconds": round(time.monotonic() - started, 1)}))


if __name__ == "__main__":
    main()
