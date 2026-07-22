from __future__ import annotations

import argparse
import json
import subprocess
from typing import Any

from azure.containerapps.sandbox import SandboxGroupClient
from azure.identity import DefaultAzureCredential

from scripts.sandbox_runtime import endpoint_for_region
from scripts.setup_agent365 import load_json
from scripts.setup_app_tfvars import runtime_app_tfvars_path, runtime_outputs_path
from scripts.tf_helpers import (
    PLATFORM_DIR,
    REPO_ROOT,
    output,
    resolve_executable,
    terraform_output,
)


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
    volume_exists = any(
        str(getattr(volume, "name", "")) == volume_name
        or str(getattr(volume, "id", "")).rstrip("/").endswith(f"/{volume_name}")
        for volume in client.list_volumes()
    )
    if volume_exists:
        client.begin_delete_volume(volume_name, polling_timeout=600).result()
    summary["deleted"] = True
    summary["next"] = (
        "Invoke the demo Worker. The bridge will recreate its Data Disk and install "
        "the immutable baseline Role Release."
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


def require_demo_branch(branch: str) -> None:
    if not branch.startswith("demo/") or ".." in branch or "//" in branch:
        raise ValueError("Disposable Git branches must use the demo/* namespace.")


def create_git_base(args: argparse.Namespace) -> None:
    require_demo_branch(args.branch)
    subprocess.run(
        [
            resolve_executable("git"),
            "push",
            args.remote,
            f"{args.baseline_commit}:refs/heads/{args.branch}",
        ],
        cwd=REPO_ROOT,
        check=True,
    )
    print(
        json.dumps(
            {
                "branch": args.branch,
                "baselineCommit": args.baseline_commit,
                "remote": args.remote,
            },
            indent=2,
            sort_keys=True,
        )
    )


def delete_git_base(args: argparse.Namespace) -> None:
    require_demo_branch(args.branch)
    pull_requests = json.loads(
        subprocess.run(
            [
                resolve_executable("gh"),
                "pr",
                "list",
                "--base",
                args.branch,
                "--state",
                "open",
                "--json",
                "number",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        or "[]"
    )
    if pull_requests and not args.close_pull_requests:
        raise RuntimeError(
            "Disposable base branch still has open pull requests. "
            "Use --close-pull-requests to close them before deletion."
        )
    for pull_request in pull_requests:
        subprocess.run(
            [
                resolve_executable("gh"),
                "pr",
                "close",
                str(pull_request["number"]),
                "--comment",
                "Disposable collective-learning demo reset.",
            ],
            cwd=REPO_ROOT,
            check=True,
        )
    subprocess.run(
        [
            resolve_executable("git"),
            "push",
            args.remote,
            "--delete",
            args.branch,
        ],
        cwd=REPO_ROOT,
        check=True,
    )
    print(
        json.dumps(
            {
                "branch": args.branch,
                "deleted": True,
                "closedPullRequests": [
                    pull_request["number"] for pull_request in pull_requests
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reset disposable demo Workers to an immutable Role Release baseline."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    reset_parser = subparsers.add_parser("reset")
    reset_parser.add_argument("--state-name", required=True)
    reset_parser.add_argument("--workspace", required=True)
    reset_parser.add_argument("--baseline-release", required=True)
    reset_parser.add_argument("--baseline-commit", required=True)
    reset_parser.add_argument("--execute", action="store_true")
    reset_parser.set_defaults(func=reset)

    create_base = subparsers.add_parser("create-git-base")
    create_base.add_argument("--branch", required=True)
    create_base.add_argument("--baseline-commit", required=True)
    create_base.add_argument("--remote", default="origin")
    create_base.set_defaults(func=create_git_base)

    delete_base = subparsers.add_parser("delete-git-base")
    delete_base.add_argument("--branch", required=True)
    delete_base.add_argument("--remote", default="origin")
    delete_base.add_argument("--close-pull-requests", action="store_true")
    delete_base.set_defaults(func=delete_git_base)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
