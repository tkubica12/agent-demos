"""Durable receipts and stopping limits for the approved AKS experiment."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tomllib
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / ".artifacts" / "aks-20260922"
PRIVATE = ROOT / ".local" / "aks-20260922"
CONFIG = tomllib.loads((ROOT / "demo.toml").read_text(encoding="utf-8"))


def now():
    return datetime.now(UTC).isoformat()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hash():
    files = [ROOT / "demo.toml", ROOT / "GOAL.md", ROOT / "pyproject.toml"]
    files += sorted((ROOT / "terraform").glob("*.tf"))
    files += sorted((ROOT / "scripts").glob("*.py"))
    return hashlib.sha256("".join(f"{p.relative_to(ROOT)}:{sha(p)}\n" for p in files).encode()).hexdigest()


@contextmanager
def writer():
    RUN.mkdir(parents=True, exist_ok=True)
    lock = RUN / "writer.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(descriptor, json.dumps({"pid": os.getpid(), "at": now()}).encode())
        yield
    finally:
        os.close(descriptor)
        lock.unlink()


def accounting(state, at=None):
    at = at or datetime.now(UTC)
    started = state.get("cloud_started_at")
    hours = max(0, (at - datetime.fromisoformat(started)).total_seconds() / 3600) if started else 0
    return {
        "at": at.isoformat(),
        "elapsed_hours": round(hours, 4),
        "conservative_cloud_usd": round(hours * CONFIG["limits"]["conservative_usd_per_hour"], 2),
        "billed_cloud_usd": None,
        "basis": "Elapsed-time reservation, not actual billed usage or a retail quote.",
    }


def check_limits(state, at=None):
    cost = accounting(state, at)
    limits = CONFIG["limits"]
    if cost["conservative_cloud_usd"] + 12 >= limits["cloud_usd"]:
        raise RuntimeError("CAPPED: cloud allowance leaves insufficient verification reserve.")
    if state.get("execution_window_started_at"):
        at = at or datetime.now(UTC)
        window_hours = (at - datetime.fromisoformat(state["execution_window_started_at"])).total_seconds() / 3600
    else:
        window_hours = cost["elapsed_hours"]
    if window_hours >= limits["execution_hours"]:
        raise RuntimeError("CAPPED: execution clock expired; retain resources and evidence.")
    if state["deployment_attempts"] >= limits["deployment_attempts"]:
        raise RuntimeError("CAPPED: deployment attempts exhausted.")
    if state["repair_cycles"] >= limits["repair_cycles"]:
        raise RuntimeError("CAPPED: repair cycles exhausted.")
    if state.get("unknown_operations", 0):
        raise RuntimeError("BLOCKED: reconcile unknown side effects before any mutation.")


def command(name, args, *, timeout=120, mutation=False, private_output=False, input_data=None):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,100}", name):
        raise ValueError("Invalid evidence name.")
    receipt = RUN / f"{name}.json"
    if receipt.exists():
        raise RuntimeError(f"Attempt {name} already exists; read it, do not replay it.")
    executable = shutil.which(args[0])
    if not executable:
        raise RuntimeError(f"Required executable missing: {args[0]}")
    invocation = [executable, *args[1:]]
    environment = os.environ.copy()
    if args[0] == "az" and Path(executable).suffix.lower() == ".cmd":
        cli_python = Path(executable).parent.parent / "python.exe"
        if not cli_python.is_file():
            raise RuntimeError("Azure CLI's installed Python interpreter was not found.")
        invocation = [str(cli_python), "-IBm", "azure.cli", *args[1:]]
        environment["AZ_INSTALLER"] = "MSI"
    if mutation:
        state = read(RUN / "status.json")
        check_limits(state)
        state["deployment_attempts"] += 1
        state["unknown_operations"] += 1
        state.setdefault("cloud_started_at", now())
        state["state"] = "ACTION_OUTCOME_UNKNOWN"
        state["accounting"] = accounting(state)
        write(RUN / "status.json", state)
    data = {"at": now(), "command": args, "mutation": mutation, "state": "STARTED"}
    write(receipt, data)
    try:
        result = subprocess.run(
            invocation, cwd=ROOT, env=environment, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout, input=input_data,
        )
    except (subprocess.TimeoutExpired, OSError) as error:
        data.update(state="OUTCOME_UNKNOWN" if mutation else "FAILED", error=str(error))
        write(receipt, data)
        raise
    captured = {"stdout": result.stdout, "stderr": result.stderr}
    if private_output:
        output_file = PRIVATE / f"{name}.output.json"
        write(output_file, captured)
        captured = {"private_output": str(output_file), "output_sha256": sha(output_file)}
    data.update(
        completed_at=now(), exit_code=result.returncode,
        state="RETURNED_AWAITING_READBACK" if mutation else "RETURNED",
        **captured,
    )
    write(receipt, data)
    if result.returncode:
        if private_output:
            raise RuntimeError(f"{name} failed ({result.returncode}); inspect private output {output_file}.")
        raise RuntimeError(f"{name} failed ({result.returncode}); see {receipt}\n{result.stderr[-4000:]}\n{result.stdout[-4000:]}")
    return result.stdout
