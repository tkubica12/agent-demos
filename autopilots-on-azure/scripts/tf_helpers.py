from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
PLATFORM_DIR = REPO_ROOT / "terraform" / "platform"
APPS_DIR = REPO_ROOT / "terraform" / "apps"


def resolve_executable(name: str) -> str:
    candidates = [name]
    if os.name == "nt" and not name.lower().endswith((".exe", ".cmd", ".bat")):
        candidates.extend([f"{name}.exe", f"{name}.cmd", f"{name}.bat"])
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    if os.name == "nt" and name == "az":
        azure_cli = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft SDKs" / "Azure" / "CLI2" / "wbin" / "az.cmd"
        if azure_cli.exists():
            return str(azure_cli)
    raise FileNotFoundError(f"Could not find executable '{name}' on PATH.")


def run(args: list[str], *, cwd: Path | None = None, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]:
    resolved_args = [resolve_executable(args[0]), *args[1:]]
    print("+ " + " ".join(args), flush=True)
    result = subprocess.run(
        resolved_args,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if check and result.returncode != 0:
        if capture:
            if result.stdout:
                print(result.stdout, file=sys.stderr)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
        raise subprocess.CalledProcessError(result.returncode, resolved_args, result.stdout, result.stderr)
    return result


def output(args: list[str], *, cwd: Path | None = None, check: bool = True) -> str:
    return run(args, cwd=cwd, capture=True, check=check).stdout.strip()


def terraform_output(root: Path) -> dict[str, Any]:
    raw = output(["terraform", "output", "-json"], cwd=root)
    payload = json.loads(raw)
    return {key: value["value"] for key, value in payload.items()}


def write_tfvars(path: Path, values: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {path}", flush=True)
