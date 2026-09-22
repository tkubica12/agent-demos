"""Entra-authenticated access through the approved Bastion only."""

import argparse
import json
import shutil
import subprocess
from datetime import UTC, datetime

from bastion import accrue
from infra import GROUP, PRIVATE, RUN, SUB, now, read, run, write

PORT = 22022
CONFIG = PRIVATE / "bastion-sshconfig"


def start_vm():
    state = read(RUN / "status.json")
    accrue(state)
    if state["state"] != "BASTION_READY":
        raise RuntimeError("Bastion is not verified ready.")
    vm_id = state["outputs"]["vm_id"]["value"]
    view = json.loads(run("bastion-start-preflight", [
        "az", "rest", "--method", "get", "--url",
        f"https://management.azure.com{vm_id}/instanceView?api-version=2025-11-01",
    ]))
    power = [x["code"] for x in view["statuses"] if x["code"].startswith("PowerState/")]
    if power == ["PowerState/deallocated"]:
        run("bastion-vm-start", [
            "az", "vm", "start", "--subscription", SUB, "--resource-group", GROUP,
            "--name", "substrate-mvp", "--only-show-errors",
        ], mutation=True, timeout=900)
    elif power != ["PowerState/running"]:
        raise RuntimeError(f"Unexpected power state, observe rather than replay: {power}")
    print("VM start accepted or already running; corporate shutdown configuration unchanged.")


def configure():
    state = read(RUN / "status.json")
    accrue(state)
    if CONFIG.exists():
        raise RuntimeError("Existing private SSH configuration: inspect expiry before regenerating.")
    receipt = json.loads(read(RUN / "native-bootstrap-facts-file.json")["stdout"])
    messages = "\n".join(x["message"] for x in receipt["value"])
    keys = [x.strip() for x in messages.splitlines() if x.startswith("ssh-ed25519 ")]
    if len(keys) != 1:
        raise RuntimeError("Missing ARM-authenticated host key.")
    (PRIVATE / "bastion_known_hosts").write_text(
        f"substrate-mvp {' '.join(keys[0].split()[:2])}\n", encoding="utf-8"
    )
    run("bastion-entra-config", [
        "az", "ssh", "config", "--subscription", SUB, "--ip", "127.0.0.1",
        "--port", str(PORT), "--file", str(CONFIG),
        "--keys-destination-folder", str(PRIVATE / "bastion-keys"), "--only-show-errors",
    ])
    print("Private Entra certificate configuration created for loopback tunnel only.")


def ssh_args():
    return [
        "ssh", "-F", str(CONFIG), "-p", str(PORT),
        "-o", f"UserKnownHostsFile={PRIVATE / 'bastion_known_hosts'}",
        "-o", "HostKeyAlias=substrate-mvp", "-o", "StrictHostKeyChecking=yes",
        "-o", "BatchMode=yes", "-o", "ConnectTimeout=30", "127.0.0.1",
    ]


def check():
    state = read(RUN / "status.json")
    accrue(state)
    suffix = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    result = run(f"bastion-entra-check-{suffix}", ssh_args() + [
        "id && sudo -n true && uname -r && stat -fc %T /sys/fs/cgroup"
    ], timeout=90)
    if not state["runtime_gate_started_at"]:
        state["runtime_gate_started_at"] = now()
    state.update(state="RUNTIME_GATE_ACTIVE", entra_ssh="PASS_VIA_BASTION",
                 next_action="Install pinned runtime and prove native Actor execution plus memory resume.")
    write(RUN / "status.json", state)
    print(result)


def tunnel():
    state = read(RUN / "status.json")
    if state["state"] not in {"BASTION_READY", "RUNTIME_GATE_ACTIVE"}:
        raise RuntimeError("Bastion has not been independently verified.")
    args = [
        "az", "network", "bastion", "tunnel", "--subscription", SUB,
        "--name", "substrate-mvp-bastion", "--resource-group", GROUP,
        "--target-resource-id", state["outputs"]["vm_id"]["value"],
        "--resource-port", "22", "--port", str(PORT), "--only-show-errors",
    ]
    subprocess.run([shutil.which(args[0]), *args[1:]], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start-vm", "configure", "check", "tunnel"])
    args = parser.parse_args()
    {"start-vm": start_vm, "configure": configure, "check": check, "tunnel": tunnel}[args.action]()
