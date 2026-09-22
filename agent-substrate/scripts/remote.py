"""Run a named, non-replayed Linux stage through the verified Bastion connection."""

import argparse
import hashlib
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from access import ssh_args
from bastion import accrue
from infra import ROOT, RUN, now, read, write


class ExecutionLimit(RuntimeError):
    """An approved stopping limit forbids another deployment attempt."""


def deployment_count(state):
    return state.get("terraform_applies", 0) + state.get("runtime_stages", 0)


def check_limits(state, at):
    started = state.get("runtime_gate_started_at")
    if not started or state.get("entra_ssh") != "PASS_VIA_BASTION":
        raise RuntimeError("Verify Entra SSH through Bastion first.")
    if (at - datetime.fromisoformat(started)).total_seconds() > 7200:
        raise ExecutionLimit("Runtime gate elapsed; no further deployment is allowed.")
    maximum = state.get("maximum_deployments", 12)
    if deployment_count(state) >= maximum:
        raise ExecutionLimit(f"Combined Terraform/runtime deployment cap reached ({maximum}).")
    if state.get("repair_cycles", 0) >= 6:
        raise ExecutionLimit("Repair-cycle cap reached (6).")


def execute(stage, source, python=False):
    if not stage.replace("-", "").isalnum():
        raise ValueError("Stage name must be alphanumeric with hyphens.")
    receipt = RUN / f"remote-{stage}.json"
    if receipt.exists():
        raise RuntimeError("Stage already attempted. Read its receipt and remote marker, never replay.")
    unknown = [path.name for path in RUN.glob("remote-*.json")
               if read(path).get("state") == "OUTCOME_UNKNOWN"]
    if unknown:
        raise RuntimeError(f"Unresolved remote attempts block new operations: {unknown}")
    state = read(RUN / "status.json")
    accrue(state)
    state.setdefault("accounting", {})["deployment_attempts"] = deployment_count(state)
    try:
        check_limits(state, datetime.now(UTC))
    except ExecutionLimit as error:
        state.update(state="CAPPED", blocker=str(error), stopped_at=now())
        write(RUN / "status.json", state)
        raise
    count = state.get("runtime_stages", 0)
    content = source.read_text(encoding="utf-8")
    executable = ("export PATH=/opt/substrate-mvp/bin:$PATH\n"
                  "export KUBECONFIG=/opt/substrate-mvp/kubeconfig\n"
                  "python3 - <<'SUBSTRATE_PYTHON'\n" + content +
                  "\nSUBSTRATE_PYTHON") if python else content
    wrapper = f"""set -euo pipefail
umask 077
mkdir -p /opt/substrate-mvp/operations
mkdir /opt/substrate-mvp/operations/{stage}
trap 'rc=$?; printf "%s\\n" "$rc" > /opt/substrate-mvp/operations/{stage}/exit-code' EXIT
cd /opt/substrate-mvp
export SUBSTRATE_STAGE={stage}
{executable}
"""
    state["runtime_stages"] = count + 1
    state["accounting"]["deployment_attempts"] = deployment_count(state)
    write(RUN / "status.json", state)
    record = {"stage": stage, "started_at": now(), "source": str(source.relative_to(ROOT)),
              "sha256": hashlib.sha256(content.encode()).hexdigest(), "state": "OUTCOME_UNKNOWN"}
    write(receipt, record)
    result = subprocess.run(ssh_args() + ["sudo -n bash -s"], input=wrapper.encode("utf-8"),
                            capture_output=True, timeout=2400)
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    record.update(state="COMMAND_RETURNED", completed_at=now(), exit_code=result.returncode,
                  stdout=stdout, stderr=stderr)
    write(receipt, record)
    print(stdout[-10000:])
    print(stderr[-4000:])
    if result.returncode:
        raise RuntimeError(f"Stage {stage} failed, receipt preserved; inspect before repair.")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("stage")
    parser.add_argument("source", type=Path)
    parser.add_argument("--python", action="store_true")
    args = parser.parse_args()
    execute(args.stage, args.source.resolve(), args.python)
