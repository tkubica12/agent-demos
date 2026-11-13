"""Provision this test landing zone with an explicit, isolated local backend."""

import argparse
import json
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[1]


def run(command, **kwargs):
    executable = shutil.which(command[0])
    if not executable:
        raise FileNotFoundError(f"Required tool is not installed: {command[0]}")
    try:
        return subprocess.run([executable, *command[1:]], check=True, **kwargs)
    except subprocess.CalledProcessError as error:
        if error.stderr:
            print(error.stderr, file=sys.stderr)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "apply", "outputs", "stop-clients", "destroy"])
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--subscription", required=True)
    parser.add_argument("--location", default="swedencentral")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,19}", args.prefix):
        raise ValueError("Use a 3-20 character lowercase resource prefix.")
    local = PROJECT / ".local" / f"landing-zone-{args.prefix}"
    local.mkdir(parents=True, exist_ok=True)
    inputs = local / "inputs.tfvars.json"
    if args.action == "prepare":
        if inputs.exists():
            raise FileExistsError("Inputs already exist; do not overwrite existing credentials or state.")
        settings = {
            "prefix": args.prefix, "subscription_id": args.subscription, "location": args.location,
            "windows_password": f"Az!{secrets.token_urlsafe(32)}9x",
        }
        inputs.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        print(f"Prepared private credentials in {local}; keep this directory private.")
        return
    settings = json.loads(inputs.read_text(encoding="utf-8"))
    if settings["prefix"] != args.prefix or settings["subscription_id"] != args.subscription:
        raise ValueError("Existing state belongs to another prefix or subscription.")
    if args.action == "stop-clients":
        run(["az", "vm", "deallocate", "--subscription", args.subscription,
             "--resource-group", f"rg-{args.prefix}-network", "--name", f"vm-{args.prefix}-windows",
             "--only-show-errors", "--output", "none"])
        return
    if args.action == "destroy":
        for suffix in [*[f"agent-{n}" for n in range(1, 7)], "experiments"]:
            result = run(["az", "resource", "list", "--subscription", args.subscription,
                          "--resource-group", f"rg-{args.prefix}-{suffix}", "--output", "json"],
                         capture_output=True, text=True)
            if json.loads(result.stdout):
                raise RuntimeError(f"Refusing to delete nonempty {suffix} RG; destroy its owned root first.")
    terraform = ["terraform", f"-chdir={ROOT}"]
    run([*terraform, "init", "-input=false", "-reconfigure",
         f"-backend-config=path={local / 'terraform.tfstate'}"])
    if args.action in {"apply", "destroy"}:
        run([*terraform, args.action, "-input=false", "-auto-approve", f"-var-file={inputs}"])
    if args.action in {"apply", "outputs"}:
        for output, filename in (
            ("deployment_inputs", "deployment-inputs.json"),
            ("private_dns_zone_ids", "private-dns-zone-ids.json"),
            ("clients", "clients.json"),
        ):
            result = run([*terraform, "output", "-json", output], capture_output=True, text=True)
            (local / filename).write_text(result.stdout, encoding="utf-8")
        print(f"Customer-shaped inputs: {local / 'deployment-inputs.json'}")


if __name__ == "__main__":
    main()
