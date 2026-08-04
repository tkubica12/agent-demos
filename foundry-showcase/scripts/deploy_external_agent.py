"""Build, deploy, and validate the external support-triage A2A agent."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import urlopen


SHOWCASE_ROOT = Path(__file__).resolve().parents[1]
EXTERNAL_AGENT_DIR = SHOWCASE_ROOT / "external-agent"
TF_DIR = SHOWCASE_ROOT / "terraform" / "external-agent"

FOUNDRY_RESOURCE_GROUP = "ai-services"
FOUNDRY_ACCOUNT_NAME = "tomaskubica-foundry-resource"
FOUNDRY_PROJECT_NAME = "tomaskubica-foundry-project"
APP_INSIGHTS_NAME = "appi-foundry-showcase-vz5kj8"
APP_INSIGHTS_RG = "rg-foundry-showcase-si4ons"


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
    return subprocess.run(
        process_command,
        cwd=cwd,
        check=True,
        text=True,
        capture_output=capture,
        shell=use_shell,
    )


def run_json(command: list[str], *, cwd: Path = SHOWCASE_ROOT) -> Any:
    return json.loads(run(command, cwd=cwd, capture=True).stdout)


def terraform_outputs(directory: Path) -> dict[str, Any]:
    raw = run_json(["terraform", "output", "-json"], cwd=directory)
    return {name: entry["value"] for name, entry in raw.items()}


def wait_for_health(url: str, attempts: int = 40) -> None:
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(url, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("status") == "ok":
                return
        except Exception as exc:
            if attempt == attempts:
                raise RuntimeError(f"External agent health check failed: {exc}") from exc
        time.sleep(10)


def wait_for_revision(resource_group: str, revision: str, attempts: int = 40) -> None:
    for attempt in range(1, attempts + 1):
        app = run_json(
            [
                "az",
                "containerapp",
                "show",
                "--name",
                "ca-foundry-ext-agent",
                "--resource-group",
                resource_group,
            ]
        )
        if app["properties"].get("latestReadyRevisionName") == revision:
            return
        if attempt == attempts:
            raise RuntimeError(f"Revision did not become ready: {revision}")
        time.sleep(10)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build, deploy, and validate the external support-triage A2A agent."
    )
    parser.add_argument(
        "--foundry-resource-group", default=FOUNDRY_RESOURCE_GROUP
    )
    parser.add_argument("--foundry-account-name", default=FOUNDRY_ACCOUNT_NAME)
    parser.add_argument("--apps-location", default="northeurope")
    parser.add_argument("--auto-approve", action="store_true")
    args = parser.parse_args()

    account = run_json(["az", "account", "show"])
    subscription_id = account["id"]
    tenant_id = account["tenantId"]

    resource_group_name = "rg-foundry-showcase-si4ons"
    acr_name = "fshowacrsi4ons"
    acr_login_server = f"{acr_name}.azurecr.io"
    acr_id = (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group_name}"
        f"/providers/Microsoft.ContainerRegistry/registries/{acr_name}"
    )
    container_env_id = (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group_name}"
        f"/providers/Microsoft.App/managedEnvironments/cae-foundry-showcase-si4ons-vnet"
    )
    foundry_account_id = (
        f"/subscriptions/{subscription_id}/resourceGroups/{args.foundry_resource_group}"
        f"/providers/Microsoft.CognitiveServices/accounts/{args.foundry_account_name}"
    )

    foundry_account = run_json(
        [
            "az",
            "cognitiveservices",
            "account",
            "show",
            "--name",
            args.foundry_account_name,
            "--resource-group",
            args.foundry_resource_group,
        ]
    )
    azure_openai_endpoint = foundry_account["properties"]["endpoint"]

    container_environment_default_domain = run_json(
        [
            "az",
            "containerapp",
            "env",
            "show",
            "--ids",
            container_env_id,
        ]
    )["properties"]["defaultDomain"]

    connection_string = run_json(
        [
            "az",
            "monitor",
            "app-insights",
            "component",
            "show",
            "--app",
            APP_INSIGHTS_NAME,
            "--resource-group",
            APP_INSIGHTS_RG,
        ]
    )["connectionString"]

    image_tag = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    image_name = f"foundry-showcase-external-agent:{image_tag}"
    run(
        [
            "az",
            "acr",
            "build",
            "--registry",
            acr_name,
            "--image",
            image_name,
            "--file",
            "Dockerfile",
            ".",
        ],
        cwd=EXTERNAL_AGENT_DIR,
    )
    image = f"{acr_login_server}/{image_name}"

    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    run(["terraform", "init", "-input=false", "-no-color"], cwd=TF_DIR)
    apply = ["terraform", "apply", "-input=false", "-no-color", "-parallelism=1"]
    if args.auto_approve:
        apply.append("-auto-approve")
    variables = {
        "subscription_id": subscription_id,
        "tenant_id": tenant_id,
        "resource_group_name": resource_group_name,
        "apps_location": args.apps_location,
        "container_environment_id": container_env_id,
        "container_environment_default_domain": container_environment_default_domain,
        "acr_login_server": acr_login_server,
        "acr_id": acr_id,
        "foundry_account_id": foundry_account_id,
        "azure_openai_endpoint": azure_openai_endpoint,
        "applicationinsights_connection_string": connection_string,
        "container_image": image,
    }
    for name, value in variables.items():
        apply.extend(["-var", f"{name}={value}"])
    run(apply, cwd=TF_DIR)

    output = terraform_outputs(TF_DIR)
    wait_for_revision(resource_group_name, output["external_agent_revision"])
    wait_for_health(output["external_agent_health_url"])

    smoke = run_json(
        [
            "uv",
            "run",
            "--project",
            str(EXTERNAL_AGENT_DIR),
            "python",
            str(EXTERNAL_AGENT_DIR / "smoke_a2a.py"),
            "--url",
            output["external_agent_url"],
        ]
    )

    print(
        json.dumps(
            {
                **output,
                "container_image": image,
                "agent_id_for_registration": "support-triage-assistant",
                "protocol": "A2A",
                "smoke": smoke,
            },
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
