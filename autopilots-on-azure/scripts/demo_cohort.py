from __future__ import annotations

import argparse
import json
from typing import Any

from azure.containerapps.sandbox import SandboxGroupClient
from azure.identity import DefaultAzureCredential

from scripts.sandbox_runtime import endpoint_for_region
from scripts.setup_agent365 import load_json
from scripts.setup_app_tfvars import runtime_app_tfvars_path, runtime_outputs_path
from scripts.tf_helpers import PLATFORM_DIR, output, terraform_output


DEMO_PREFIX = "demo-"


def require_demo_owned(
    *,
    state_name: str,
    worker_id: str,
    volume_name: str,
    workspace: str,
) -> None:
    values = {
        "state name": state_name,
        "Worker ID": worker_id,
        "Data Disk": volume_name,
        "Terraform workspace": workspace,
    }
    invalid = [name for name, value in values.items() if not value.startswith(DEMO_PREFIX)]
    if invalid:
        raise ValueError(
            "Demo reset refuses non-demo resources: " + ", ".join(invalid)
        )


def matching_sandboxes(
    sandboxes: list[dict[str, Any]],
    *,
    worker_id: str,
    volume_name: str,
) -> list[dict[str, Any]]:
    return [
        sandbox
        for sandbox in sandboxes
        if (sandbox.get("labels") or {}).get("worker") == worker_id
        and any(
            volume.get("volumeName") == volume_name
            for volume in sandbox.get("volumes") or []
        )
    ]


def reset(args: argparse.Namespace) -> None:
    tfvars = load_json(runtime_app_tfvars_path("hermes", args.state_name))
    outputs = load_json(runtime_outputs_path("hermes", args.state_name))
    worker_id = str(tfvars.get("autopilot_name") or "")
    volume_name = str(tfvars.get("runtime_data_volume_name") or "")
    require_demo_owned(
        state_name=args.state_name,
        worker_id=worker_id,
        volume_name=volume_name,
        workspace=args.workspace,
    )
    if tfvars.get("hermes_role_release") != args.baseline_release:
        raise ValueError("Demo Worker is not pinned to the requested baseline Role Release.")
    if tfvars.get("hermes_role_release_commit") != args.baseline_commit:
        raise ValueError("Demo Worker is not pinned to the requested baseline commit.")
    if outputs.get("worker_id") != worker_id or outputs.get("runtime_data_volume_name") != volume_name:
        raise ValueError("Demo Worker outputs do not match the reset configuration.")

    platform = terraform_output(PLATFORM_DIR)
    client = SandboxGroupClient(
        endpoint_for_region(str(platform["sandbox_location"])),
        DefaultAzureCredential(),
        subscription_id=output(
            ["az", "account", "show", "--query", "id", "-o", "tsv"]
        ),
        resource_group=str(platform["resource_group_name"]),
        sandbox_group=str(platform["sandbox_group_name"]),
    )
    sandboxes = client._dp_get(f"{client._group_path}/sandboxes")
    matches = matching_sandboxes(
        sandboxes if isinstance(sandboxes, list) else [],
        worker_id=worker_id,
        volume_name=volume_name,
    )
    summary = {
        "stateName": args.state_name,
        "workerId": worker_id,
        "workspace": args.workspace,
        "dataVolume": volume_name,
        "baselineRelease": args.baseline_release,
        "baselineCommit": args.baseline_commit,
        "sandboxIds": [str(item.get("id") or "") for item in matches],
        "execute": args.execute,
    }
    if not args.execute:
        summary["next"] = "Rerun with --execute to delete only these demo Sandboxes and this demo Data Disk."
        print(json.dumps(summary, indent=2, sort_keys=True))
        return

    for sandbox in matches:
        sandbox_id = str(sandbox.get("id") or "")
        if not sandbox_id:
            raise RuntimeError("A matching demo Sandbox has no ID.")
        client.begin_delete_sandbox(sandbox_id, polling_timeout=600).result()
    volume_names = {
        str(getattr(volume, "name", "") or getattr(volume, "id", ""))
        for volume in client.list_volumes()
    }
    if volume_name in volume_names:
        client.begin_delete_volume(volume_name, polling_timeout=600).result()
    summary["deleted"] = True
    summary["next"] = (
        "Invoke the demo Worker. The bridge will recreate its Data Disk and install "
        "the immutable baseline Role Release."
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reset disposable demo Workers to an immutable Role Release baseline."
    )
    parser.add_argument("--state-name", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--baseline-release", required=True)
    parser.add_argument("--baseline-commit", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    reset(args)


if __name__ == "__main__":
    main()
