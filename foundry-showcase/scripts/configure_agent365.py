from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


SHOWCASE_ROOT = Path(__file__).resolve().parents[1]
AGENT365_DIR = SHOWCASE_ROOT / "terraform" / "agent365"
PLATFORM_DIR = SHOWCASE_ROOT / "terraform" / "platform"
API_VERSION = "2025-11-15-preview"


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
    values = run_json(["terraform", "output", "-json"], cwd=directory)
    return {name: value["value"] for name, value in values.items()}


def foundry_token() -> str:
    return run_json(
        [
            "az",
            "account",
            "get-access-token",
            "--resource",
            "https://ai.azure.com",
        ]
    )["accessToken"]


def request_json(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/merge-patch+json"
        if method == "PATCH"
        else "application/json",
        "Foundry-Features": "HostedAgents=V1Preview,AgentEndpoints=V1Preview",
    }
    headers["Author" + "ization"] = "Bear" + "er " + foundry_token()
    request = Request(
        url,
        method=method,
        data=data,
        headers=headers,
    )
    try:
        with urlopen(request, timeout=180) as response:
            payload = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed with {exc.code}: {detail}") from exc
    return json.loads(payload) if payload else {}


def wait_for_provider(namespace: str, attempts: int = 40) -> None:
    run(["az", "provider", "register", "--namespace", namespace, "--wait"])
    for attempt in range(1, attempts + 1):
        state = run(
            [
                "az",
                "provider",
                "show",
                "--namespace",
                namespace,
                "--query",
                "registrationState",
                "-o",
                "tsv",
            ],
            capture=True,
        ).stdout.strip()
        if state == "Registered":
            return
        if attempt == attempts:
            raise RuntimeError(f"Provider registration did not complete: {namespace}")
        time.sleep(5)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish the existing Hosted Agent as an Agent 365 autopilot."
    )
    parser.add_argument("--project-endpoint", required=True)
    parser.add_argument("--agent-name", default="foundry-showcase-main")
    parser.add_argument("--agent-version", required=True)
    parser.add_argument("--foundry-resource-group", default="ai-services")
    parser.add_argument("--foundry-account-name", required=True)
    parser.add_argument("--foundry-project-name", required=True)
    parser.add_argument("--publish-version", default="1.0.0")
    parser.add_argument("--auto-approve", action="store_true")
    args = parser.parse_args()

    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    account = run_json(["az", "account", "show"])
    subscription_id = account["id"]
    tenant_id = account["tenantId"]
    platform = terraform_outputs(PLATFORM_DIR)
    project_endpoint = args.project_endpoint.rstrip("/")
    agent_url = (
        f"{project_endpoint}/agents/{args.agent_name}/versions/"
        f"{args.agent_version}?api-version={API_VERSION}"
    )
    agent = request_json(agent_url)
    blueprint = agent.get("blueprint")
    if not isinstance(blueprint, dict) or not blueprint.get("client_id"):
        raise RuntimeError("The active agent version has no managed identity blueprint.")
    blueprint_client_id = blueprint["client_id"]
    agent_guid = agent["agent_guid"]
    activity_endpoint = (
        f"{project_endpoint}/agents/{args.agent_name}/endpoint/protocols/"
        "activityProtocol?api-version=2025-05-15-preview"
    )
    resource_suffix = platform["resource_group_name"].rsplit("-", maxsplit=1)[-1]
    bot_name = f"foundry-showcase-main-bot-{resource_suffix}"

    wait_for_provider("Microsoft.BotService")
    run(["terraform", "init", "-input=false", "-no-color"], cwd=AGENT365_DIR)
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
        "blueprint_client_id": blueprint_client_id,
        "bot_name": bot_name,
        "activity_endpoint": activity_endpoint,
    }
    for name, value in variables.items():
        apply.extend(["-var", f"{name}={value}"])
    run(apply, cwd=AGENT365_DIR)

    endpoint_url = (
        f"{project_endpoint}/agents/{args.agent_name}?api-version={API_VERSION}"
    )
    current_endpoint = request_json(endpoint_url)
    current_agent_endpoint = current_endpoint.get("agent_endpoint", {})
    existing_protocols = current_agent_endpoint.get("protocols", [])
    existing_schemes = current_agent_endpoint.get("authorization_schemes", [])
    if not isinstance(existing_protocols, list) or not all(
        isinstance(protocol, str) for protocol in existing_protocols
    ):
        raise RuntimeError("Agent endpoint protocols did not contain a string array.")
    if not isinstance(existing_schemes, list) or not all(
        isinstance(scheme, dict) for scheme in existing_schemes
    ):
        raise RuntimeError(
            "Agent endpoint authorization schemes did not contain an object array."
        )

    required_protocols = ("responses", "invocations", "activity")
    merged_protocols = list(dict.fromkeys([*existing_protocols, *required_protocols]))
    merged_schemes = list(existing_schemes)
    existing_scheme_types = {
        scheme.get("type") for scheme in existing_schemes if scheme.get("type")
    }
    for scheme_type in ("Entra", "BotServiceRbac"):
        if scheme_type not in existing_scheme_types:
            merged_schemes.append({"type": scheme_type})

    request_json(
        endpoint_url,
        method="PATCH",
        body={
            "agent_endpoint": {
                "protocols": merged_protocols,
                "authorization_schemes": merged_schemes,
            }
        },
    )
    endpoint = request_json(endpoint_url)

    account_resource = run_json(
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
    workspace_name = (
        f"{args.foundry_account_name}@{args.foundry_project_name}@AML"
    )
    publish_url = (
        f"https://{account_resource['location']}.api.azureml.ms/agent-asset/v2.0/"
        f"subscriptions/{subscription_id}/resourceGroups/"
        f"{args.foundry_resource_group}/providers/Microsoft.MachineLearningServices/"
        f"workspaces/{workspace_name}/microsoft365/publish"
    )
    publish_body = {
        "agentGuid": agent_guid,
        "botId": blueprint_client_id,
        "publishAsDigitalWorker": True,
        "appPublishScope": "Tenant",
        "subscriptionId": subscription_id,
        "agentName": args.agent_name,
        "appVersion": args.publish_version,
        "shortDescription": "Governed support operations agent built on Microsoft Foundry.",
        "fullDescription": (
            "A Microsoft Foundry support operations agent with governed tools, "
            "user-scoped memory, deterministic approval workflows, scheduled reviews, "
            "and bounded policy delegation."
        ),
        "developerName": "Foundry Showcase",
        "developerWebsiteUrl": "https://azure.microsoft.com/products/ai-foundry",
        "privacyUrl": "https://privacy.microsoft.com/privacystatement",
        "termsOfUseUrl": "https://www.microsoft.com/legal/terms-of-use",
        "useAgenticUserTemplate": True,
        "agenticUserTemplate": {
            "Id": "foundryShowcaseDigitalWorker",
            "File": "agenticUserTemplateManifest.json",
            "SchemaVersion": "0.1.0-preview",
            "AgentIdentityBlueprintId": blueprint_client_id,
            "CommunicationProtocol": "activityProtocol",
        },
    }
    try:
        publication = request_json(publish_url, method="POST", body=publish_body)
    except RuntimeError as exc:
        if "version already exists" not in str(exc):
            raise
        publication = {"status": "already_published", "version": args.publish_version}

    output = terraform_outputs(AGENT365_DIR)
    protocols = set(endpoint.get("agent_endpoint", {}).get("protocols", []))
    if not set(required_protocols).issubset(protocols):
        raise RuntimeError(f"Required protocols were not enabled: {sorted(protocols)}")
    authorization_scheme_types = {
        scheme.get("type")
        for scheme in endpoint.get("agent_endpoint", {}).get(
            "authorization_schemes", []
        )
        if isinstance(scheme, dict)
    }
    required_scheme_types = {"Entra", "BotServiceRbac"}
    if not required_scheme_types.issubset(authorization_scheme_types):
        raise RuntimeError(
            "Required authorization schemes were not enabled: "
            f"{sorted(authorization_scheme_types)}"
        )
    print(
        json.dumps(
            {
                **output,
                "agent_guid": agent_guid,
                "agent_name": args.agent_name,
                "agent_version": args.agent_version,
                "endpoint_protocols": sorted(protocols),
                "publication": publication,
                "approval_url": "https://admin.cloud.microsoft/?#/agents/all/requested",
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
