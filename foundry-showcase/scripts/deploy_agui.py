from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


SHOWCASE_ROOT = Path(__file__).resolve().parents[1]
PLATFORM_DIR = SHOWCASE_ROOT / "terraform" / "platform"
AGUI_DIR = SHOWCASE_ROOT / "terraform" / "agui"
BFF_DIR = SHOWCASE_ROOT / "bff"


def run(
    command: list[str],
    *,
    cwd: Path = SHOWCASE_ROOT,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    print(f"+ {' '.join(command)}", flush=True)
    executable = shutil.which(command[0])
    if executable is None:
        raise FileNotFoundError(f"Executable not found on PATH: {command[0]}")
    resolved = [executable, *command[1:]]
    use_shell = os.name == "nt" and Path(executable).suffix.lower() in {".cmd", ".bat"}
    process_command: str | list[str] = (
        subprocess.list2cmdline(resolved) if use_shell else resolved
    )
    try:
        return subprocess.run(
            process_command,
            cwd=cwd,
            check=True,
            text=True,
            capture_output=capture,
            shell=use_shell,
        )
    except subprocess.CalledProcessError as exc:
        if capture and exc.stderr:
            print(exc.stderr, file=sys.stderr)
        raise


def run_json(command: list[str], *, cwd: Path = SHOWCASE_ROOT) -> Any:
    return json.loads(run(command, cwd=cwd, capture=True).stdout)


def terraform_outputs(directory: Path) -> dict[str, Any]:
    values = run_json(["terraform", "output", "-json"], cwd=directory)
    return {name: value["value"] for name, value in values.items()}


def wait_for_health(url: str, attempts: int = 40) -> None:
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(url, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload == {"status": "ok"}:
                return
        except Exception as exc:
            if attempt == attempts:
                raise RuntimeError(f"AG-UI health check failed: {exc}") from exc
        time.sleep(10)


def wait_for_revision(
    resource_group: str,
    revision: str,
    attempts: int = 40,
) -> None:
    for attempt in range(1, attempts + 1):
        app = run_json(
            [
                "az",
                "containerapp",
                "show",
                "--name",
                "ca-foundry-showcase-agui",
                "--resource-group",
                resource_group,
            ]
        )
        if app["properties"].get("latestReadyRevisionName") == revision:
            return
        if attempt == attempts:
            raise RuntimeError(f"AG-UI revision did not become ready: {revision}")
        time.sleep(10)


def verify_authentication(url: str) -> None:
    request = Request(
        f"{url.rstrip('/')}/agui",
        data=b'{"messages":[{"role":"user","content":"hello"}]}',
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urlopen(request, timeout=30)
    except HTTPError as exc:
        if exc.code == 401:
            return
        raise RuntimeError(f"Expected unauthenticated AG-UI status 401, got {exc.code}.")
    raise RuntimeError("Unauthenticated AG-UI request unexpectedly succeeded.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build, deploy, and validate the secretless AG-UI BFF."
    )
    parser.add_argument("--agent-invocations-url", required=True)
    parser.add_argument("--foundry-resource-group", default="ai-services")
    parser.add_argument("--foundry-account-name", required=True)
    parser.add_argument("--foundry-project-name", required=True)
    parser.add_argument(
        "--application-insights-name",
        help=(
            "Application Insights component name. Defaults to the Foundry project's "
            "single AppInsights connection."
        ),
    )
    parser.add_argument(
        "--application-insights-resource-group",
        help=(
            "Resource group containing an explicitly named Application Insights "
            "component. Defaults to --foundry-resource-group."
        ),
    )
    parser.add_argument("--auto-approve", action="store_true")
    args = parser.parse_args()

    account = run_json(["az", "account", "show"])
    subscription_id = account["id"]
    tenant_id = account["tenantId"]
    platform = terraform_outputs(PLATFORM_DIR)

    image_tag = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    image_name = f"foundry-showcase-agui:{image_tag}"
    run(
        [
            "az",
            "acr",
            "build",
            "--registry",
            platform["acr_name"],
            "--image",
            image_name,
            "--file",
            "Dockerfile",
            ".",
        ],
        cwd=BFF_DIR,
    )
    image = f"{platform['acr_login_server']}/{image_name}"
    foundry_project_id = (
        f"/subscriptions/{subscription_id}/resourceGroups/"
        f"{args.foundry_resource_group}/providers/Microsoft.CognitiveServices/"
        f"accounts/{args.foundry_account_name}/projects/{args.foundry_project_name}"
    )
    application_insights_name = args.application_insights_name
    application_insights_resource_group = (
        args.application_insights_resource_group or args.foundry_resource_group
    )
    if application_insights_name is None:
        connections = run_json(
            [
                "az",
                "rest",
                "--method",
                "get",
                "--url",
                (
                    f"https://management.azure.com{foundry_project_id}/connections"
                    "?api-version=2025-04-01-preview"
                ),
            ]
        )
        app_insights_connections = [
            connection
            for connection in connections.get("value", [])
            if connection.get("properties", {}).get("category") == "AppInsights"
        ]
        if len(app_insights_connections) != 1:
            raise RuntimeError(
                "Expected one Foundry project AppInsights connection, found "
                f"{len(app_insights_connections)}."
            )
        target = app_insights_connections[0].get("properties", {}).get("target")
        if not target:
            raise RuntimeError("The Foundry AppInsights connection has no target.")
        target_parts = target.strip("/").split("/")
        try:
            resource_group_index = target_parts.index("resourceGroups") + 1
            application_insights_resource_group = target_parts[resource_group_index]
        except (ValueError, IndexError) as exc:
            raise RuntimeError(
                f"Cannot resolve the AppInsights resource group from target {target}."
            ) from exc
        application_insights_name = target_parts[-1]
    role = run_json(
        [
            "az",
            "role",
            "definition",
            "list",
            "--name",
            "Foundry Agent Consumer",
        ]
    )
    if len(role) != 1:
        raise RuntimeError(
            f"Expected one Foundry Agent Consumer role definition, found {len(role)}."
        )
    connection_string = run_json(
        [
            "az",
            "monitor",
            "app-insights",
            "component",
            "show",
            "--app",
            application_insights_name,
            "--resource-group",
            application_insights_resource_group,
        ]
    )["connectionString"]

    # Avoid Azure CLI buffered-stdout failures when azapi invokes it on Windows.
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    run(["terraform", "init", "-input=false", "-no-color"], cwd=AGUI_DIR)
    apply = [
        "terraform",
        "apply",
        "-input=false",
        "-no-color",
        "-parallelism=1",
    ]
    if args.auto_approve:
        apply.append("-auto-approve")
    variables = {
        "subscription_id": subscription_id,
        "tenant_id": tenant_id,
        "container_image": image,
        "foundry_agent_invocations_url": args.agent_invocations_url,
        "foundry_project_resource_id": foundry_project_id,
        "foundry_consumer_role_definition_id": role[0]["id"],
        "applicationinsights_connection_string": connection_string,
    }
    for name, value in variables.items():
        apply.extend(["-var", f"{name}={value}"])
    run(apply, cwd=AGUI_DIR)
    output = terraform_outputs(AGUI_DIR)

    wait_for_revision(platform["resource_group_name"], output["bff_revision"])
    wait_for_health(output["bff_health_url"])
    verify_authentication(output["bff_url"])
    token = run_json(
        [
            "az",
            "account",
            "get-access-token",
            "--tenant",
            tenant_id,
            "--scope",
            output["entra_scope"],
        ]
    )["accessToken"]
    smoke = run_json(
        [
            "uv",
            "run",
            "--project",
            str(BFF_DIR),
            "python",
            str(BFF_DIR / "smoke_agui.py"),
            "--url",
            output["bff_url"],
            "--token",
            token,
        ]
    )
    print(
        json.dumps(
            {
                **output,
                "container_image": image,
                "smoke": smoke,
            },
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
