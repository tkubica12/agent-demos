from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import urlopen


SHOWCASE_ROOT = Path(__file__).resolve().parents[1]
PLATFORM_DIR = SHOWCASE_ROOT / "terraform" / "platform"
APPS_DIR = SHOWCASE_ROOT / "terraform" / "apps"
CASE_MCP_DIR = SHOWCASE_ROOT / "case-mcp"
MAIN_AGENT_DIR = SHOWCASE_ROOT / "main-agent"


def run(
    command: list[str],
    *,
    cwd: Path = SHOWCASE_ROOT,
    env: dict[str, str] | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    print(f"+ {' '.join(command)}", flush=True)
    executable = shutil.which(command[0])
    if executable is None:
        raise FileNotFoundError(f"Executable not found on PATH: {command[0]}")
    resolved_command = [executable, *command[1:]]
    use_shell = os.name == "nt" and Path(executable).suffix.lower() in {".cmd", ".bat"}
    process_command: str | list[str]
    process_command = subprocess.list2cmdline(resolved_command) if use_shell else resolved_command
    return subprocess.run(
        process_command,
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        capture_output=capture,
        shell=use_shell,
    )


def run_json(command: list[str], *, cwd: Path = SHOWCASE_ROOT) -> Any:
    return json.loads(run(command, cwd=cwd, capture=True).stdout)


def terraform_apply(
    directory: Path,
    variables: dict[str, str],
    auto_approve: bool,
) -> None:
    run(["terraform", "init", "-input=false", "-no-color"], cwd=directory)
    command = ["terraform", "apply", "-input=false", "-no-color"]
    if auto_approve:
        command.append("-auto-approve")
    for name, value in variables.items():
        command.extend(["-var", f"{name}={value}"])
    run(command, cwd=directory)


def terraform_outputs(directory: Path) -> dict[str, Any]:
    raw = run_json(["terraform", "output", "-json"], cwd=directory)
    return {name: entry["value"] for name, entry in raw.items()}


def wait_for_health(url: str, attempts: int = 30) -> None:
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(url, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("status") == "ok":
                print(json.dumps(payload, indent=2))
                return
        except Exception as exc:
            if attempt == attempts:
                raise RuntimeError(f"Case MCP health check failed: {exc}") from exc
        time.sleep(10)


def hosted_agent_identity(project_endpoint: str) -> dict[str, str]:
    url = (
        f"{project_endpoint.rstrip('/')}/agents/foundry-showcase-main"
        "?api-version=2025-11-15-preview"
    )
    payload = run_json(
        [
            "az",
            "rest",
            "--method",
            "get",
            "--url",
            url,
            "--resource",
            "https://ai.azure.com",
        ]
    )
    latest = payload["versions"]["latest"]
    if latest["status"] != "active":
        raise RuntimeError(f"Hosted agent latest version is {latest['status']}, not active.")
    instance_identity = latest["instance_identity"]
    return {
        "client_id": instance_identity["client_id"],
        "principal_id": instance_identity["principal_id"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-endpoint",
        default=os.getenv("FOUNDRY_PROJECT_ENDPOINT"),
    )
    parser.add_argument("--location", default="swedencentral")
    parser.add_argument("--apps-location", default="northeurope")
    parser.add_argument("--auto-approve", action="store_true")
    parser.add_argument("--update-skills", action="store_true")
    parser.add_argument("--new-toolbox-version", action="store_true")
    args = parser.parse_args()
    if not args.project_endpoint:
        raise RuntimeError(
            "--project-endpoint or FOUNDRY_PROJECT_ENDPOINT must identify the existing Foundry project."
        )

    account = run_json(["az", "account", "show"])
    subscription_id = account["id"]
    tenant_id = account["tenantId"]
    identity = hosted_agent_identity(args.project_endpoint)
    common_vars = {
        "subscription_id": subscription_id,
        "tenant_id": tenant_id,
    }
    terraform_apply(
        PLATFORM_DIR,
        {
            **common_vars,
            "location": args.location,
            "apps_location": args.apps_location,
            "hosted_agent_client_id": identity["client_id"],
            "hosted_agent_principal_id": identity["principal_id"],
        },
        args.auto_approve,
    )
    platform = terraform_outputs(PLATFORM_DIR)

    image_tag = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    image_name = f"foundry-showcase-case-mcp:{image_tag}"
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
        cwd=CASE_MCP_DIR,
    )
    image = f"{platform['acr_login_server']}/{image_name}"
    terraform_apply(
        APPS_DIR,
        {
            **common_vars,
            "container_image": image,
        },
        args.auto_approve,
    )
    apps = terraform_outputs(APPS_DIR)
    wait_for_health(apps["case_mcp_health_url"])

    connection_name = "foundry-showcase-case-mcp"
    run(
        [
            "azd",
            "ai",
            "connection",
            "create",
            connection_name,
            "--kind",
            "remote-tool",
            "--target",
            apps["case_mcp_endpoint"],
            "--auth-type",
            "agentic-identity",
            "--audience",
            platform["case_api_audience"],
            "--project-endpoint",
            args.project_endpoint,
            "--force",
            "--no-prompt",
        ]
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        asset_output = Path(temp_dir) / "foundry-assets.json"
        publish_command = [
            "uv",
            "run",
            "--project",
            str(MAIN_AGENT_DIR),
            "python",
            str(SHOWCASE_ROOT / "scripts" / "publish_foundry_assets.py"),
            "--project-endpoint",
            args.project_endpoint,
            "--mcp-endpoint",
            apps["case_mcp_endpoint"],
            "--connection-name",
            connection_name,
            "--output-file",
            str(asset_output),
        ]
        if args.update_skills:
            publish_command.append("--update-skills")
        if args.new_toolbox_version:
            publish_command.append("--new-toolbox-version")
        run(publish_command)
        assets = json.loads(asset_output.read_text(encoding="utf-8"))

    print(
        json.dumps(
            {
                "caseMcp": apps,
                "containerImage": image,
                "connection": connection_name,
                "toolbox": assets,
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
