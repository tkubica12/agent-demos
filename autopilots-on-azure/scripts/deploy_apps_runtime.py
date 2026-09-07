from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from scripts.setup_app_tfvars import runtime_app_tfvars_path, runtime_outputs_path
from scripts.tf_helpers import APPS_DIR, PLATFORM_DIR, run, terraform_output, write_tfvars


def terraform_workspace_name(state_name: str = "hermes") -> str:
    return f"autopilot-{state_name}"


def terraform_current_workspace() -> str:
    return run(["terraform", "workspace", "show"], cwd=APPS_DIR, capture=True).stdout.strip()


def select_or_create_workspace(workspace: str) -> None:
    result = run(["terraform", "workspace", "select", workspace], cwd=APPS_DIR, capture=True, check=False)
    if result.returncode == 0:
        return
    run(["terraform", "workspace", "new", workspace], cwd=APPS_DIR)


def load_runtime_tfvars(runtime: str, state_name: str = "") -> dict[str, Any]:
    path = runtime_app_tfvars_path(runtime, state_name)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} does not exist. Run `uv run python -m scripts.setup_app_tfvars --state-name {state_name or 'hermes'}` first."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("agent_runtime") != runtime:
        raise ValueError(f"{path} contains agent_runtime={payload.get('agent_runtime')!r}, expected {runtime!r}.")
    return payload


def activate_runtime_tfvars(runtime: str, state_name: str = "") -> dict[str, Any]:
    tfvars = load_runtime_tfvars(runtime, state_name)
    write_tfvars(APPS_DIR / "generated.app.auto.tfvars.json", tfvars)
    write_tfvars(APPS_DIR / "generated.runtime.auto.tfvars.json", tfvars)
    return tfvars


def capture_runtime_outputs(runtime: str, workspace: str, state_name: str = "", *, service_outputs: dict | None = None) -> Path:
    outputs = terraform_output(APPS_DIR)
    outputs.pop("deployment_config", None)
    output_path = runtime_outputs_path(runtime, state_name)
    if service_outputs is not None:
        outputs.update(service_outputs)
    elif output_path.exists():
        previous = json.loads(output_path.read_text(encoding="utf-8"))
        if previous.get("sandbox_groups") == outputs.get("sandbox_groups"):
            for key, value in previous.items():
                if key.endswith(("_url", "_fqdn", "_sandbox_id")) or key in {"sandbox_services", "runtime_disk_image_id"}:
                    outputs[key] = value
    outputs["terraform_workspace"] = workspace
    outputs["captured_agent_runtime"] = runtime
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(outputs, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}", flush=True)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy or plan one Hermes Worker using a dedicated Terraform workspace."
    )
    parser.add_argument("--state-name", default="hermes", help="Worker state directory under .local (default: hermes).")
    parser.add_argument("--workspace", default="", help="Terraform workspace name. Defaults to autopilot-<state-name>.")
    parser.add_argument("--plan", action="store_true", help="Run terraform plan for this runtime.")
    parser.add_argument("--apply", action="store_true", help="Run terraform apply for this runtime.")
    parser.add_argument("--auto-approve", action="store_true", help="Pass -auto-approve to terraform apply.")
    parser.add_argument("--capture", action="store_true", help="Capture terraform outputs for this runtime.")
    parser.add_argument("--deploy-services", action="store_true", help="Deploy Sandbox services after the apps infrastructure exists.")
    parser.add_argument("--infrastructure-only", action="store_true", help="Apply only ARM resources, allowing blueprint federation trust setup before Sandbox deployment.")
    parser.add_argument("--skip-init", action="store_true", help="Do not run terraform init before selecting the workspace.")
    args = parser.parse_args()
    if args.infrastructure_only and args.deploy_services:
        parser.error("--infrastructure-only and --deploy-services cannot be combined.")

    state_name = args.state_name
    workspace = args.workspace or terraform_workspace_name(state_name)
    activate_runtime_tfvars("hermes", state_name)

    if not args.skip_init:
        run(["terraform", "init"], cwd=APPS_DIR)
    select_or_create_workspace(workspace)
    active_workspace = terraform_current_workspace()
    if active_workspace != workspace:
        raise RuntimeError(f"Terraform workspace is {active_workspace!r}, expected {workspace!r}.")

    if args.plan:
        run(["terraform", "plan"], cwd=APPS_DIR)
    if args.apply:
        command = ["terraform", "apply"]
        if args.auto_approve:
            command.append("-auto-approve")
        run(command, cwd=APPS_DIR)
        args.capture = True
        args.deploy_services = not args.infrastructure_only
    service_outputs = None
    if args.deploy_services:
        from scripts.sandbox_services import deploy_sandbox_services

        service_outputs = deploy_sandbox_services(terraform_output(APPS_DIR), terraform_output(PLATFORM_DIR))
        args.capture = True
    if args.capture:
        capture_runtime_outputs("hermes", workspace, state_name, service_outputs=service_outputs)

    print(
        json.dumps(
            {
                "runtime": "hermes",
                "stateName": state_name,
                "terraformWorkspace": workspace,
                "runtimeTfvarsFile": str(runtime_app_tfvars_path("hermes", state_name)),
                "runtimeOutputsFile": str(runtime_outputs_path("hermes", state_name)),
                "next": f"Run Agent 365 setup with --state-name {state_name}; it will use the captured endpoint when present.",
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from exc
