from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def application_command(role: str) -> list[str]:
    if role == "runtime":
        return [sys.executable, "/app/start_hermes.py"]
    if role == "gateway":
        return [sys.executable, "-m", "bridge.server"]
    return [
        sys.executable, "-m", "uvicorn", "backend.app:create_app", "--factory",
        "--host", "0.0.0.0", "--port", "8000", "--no-access-log",
    ]


def load_settings(path: Path, owner: int) -> dict[str, str]:
    metadata = path.lstat()
    if path.is_symlink() or metadata.st_uid != owner or metadata.st_mode & 0o777 != 0o600:
        raise ValueError("Service settings require their process owner, mode 0600 and no symbolic link.")
    settings = json.loads(path.read_text(encoding="utf-8"))
    environment = settings.get("environment") if isinstance(settings, dict) else None
    if not isinstance(environment, dict) or not all(
        isinstance(key, str) and key and "=" not in key and "\0" not in key
        and isinstance(value, str) and "\0" not in value for key, value in environment.items()
    ):
        raise ValueError("Service settings require a string-valued environment mapping.")
    return environment


def prepare_simulator_storage() -> None:
    directory = Path(os.environ.get("TSIM_DATA_DIRECTORY", "/data"))
    metadata = directory.lstat()
    if directory.is_symlink() or not directory.is_dir() or metadata.st_uid not in (0, 10001):
        raise ValueError("Simulator data must be a directory owned by root or UID 10001.")
    if os.geteuid() == 0:
        os.chown(directory, 10001, 10001)
        os.chmod(directory, 0o700)
        os.setgroups([])
        os.setgid(10001)
        os.setuid(10001)
    if os.geteuid() != 10001:
        raise ValueError("The simulator must run as UID 10001.")


def supervise(path: Path, command: list[str], role: str) -> None:
    deadline = time.monotonic() + 1800
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("Deployment did not supply service configuration.")
        time.sleep(1)
    log_path = "/tmp/simulator-service.log" if role == "simulator" else "/app/.sandbox-service.log"
    descriptor = os.open(log_path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    process = None
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def stop_process():
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=15)

    try:
        with os.fdopen(descriptor, "a", buffering=1) as log:
            previous = None
            while not stopping:
                environment = load_settings(path, os.geteuid())
                if environment != previous:
                    stop_process()
                    process = subprocess.Popen(
                        command, cwd="/app", env={**os.environ, **environment},
                        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    )
                    previous = environment
                if process.poll() is not None:
                    raise RuntimeError(f"Application process exited with status {process.returncode}.")
                time.sleep(1)
    finally:
        stop_process()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=("gateway", "runtime", "simulator"))
    role = parser.parse_args().role
    if role == "simulator":
        prepare_simulator_storage()
    command = application_command(role)
    configuration = os.environ.get("AUTOPILOTS_SERVICE_CONFIG")
    if configuration:
        supervise(Path(configuration), command, role)
    else:
        os.chdir("/app")
        os.execv(command[0], command)


if __name__ == "__main__":
    main()
