from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from scripts.tf_helpers import APPS_DIR, PLATFORM_DIR, REPO_ROOT, output, run, terraform_output


GENERATED_CONFIG = "a365.generated.config.json"
LOCAL_METADATA = "openclaw-autopilot-agent365-identifiers.json"
MANIFEST_DIR = "manifest"
MANIFEST_FILE = "manifest.json"
MANIFEST_PACKAGE = "manifest.zip"
SECRET_KEYS = {
    "agentBlueprintClientSecret",
    "agentBlueprintClientSecretProtected",
    "clientSecret",
    "secret",
}


def current_tenant_id() -> str:
    return output(["az", "account", "show", "--query", "tenantId", "-o", "tsv"])


def bridge_messaging_endpoint() -> str:
    apps = terraform_output(APPS_DIR)
    bridge_url = str(apps["bridge_url"]).rstrip("/")
    return f"{bridge_url}/api/messages"


def agent365_workspace(suffix: str) -> Path:
    return REPO_ROOT / ".local" / suffix / "agent365"


def agent365_config_payload(
    *,
    agent_name: str,
    tenant_id: str,
    messaging_endpoint: str,
    ai_teammate: bool,
    manager_email: str = "",
    agent_user_principal_name: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "agentName": agent_name,
        "agentIdentityDisplayName": f"{agent_name} Agent",
        "agentBlueprintDisplayName": f"{agent_name} Blueprint",
        "tenantId": tenant_id,
        "messagingEndpoint": messaging_endpoint,
        "needDeployment": False,
        "deploymentProjectPath": ".",
        "aiteammate": ai_teammate,
    }
    if manager_email:
        payload["managerEmail"] = manager_email
    if agent_user_principal_name:
        payload["agentUserPrincipalName"] = agent_user_principal_name
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {path}", flush=True)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def merge_config(existing: dict[str, Any], generated: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    merged.update(generated)
    return merged


def non_secret_generated_fields(generated: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in generated.items() if key not in SECRET_KEYS and "secret" not in key.lower()}


def build_metadata(config: dict[str, Any], generated: dict[str, Any]) -> dict[str, Any]:
    clean_generated = non_secret_generated_fields(generated)
    return {
        "agentName": config.get("agentName", ""),
        "tenantId": config.get("tenantId", ""),
        "messagingEndpoint": generated.get("messagingEndpoint") or config.get("messagingEndpoint", ""),
        "developerPortalConfigurationUrl": developer_portal_url(str(generated.get("agentBlueprintId", ""))),
        "adminCenterAgentsUrl": "https://admin.cloud.microsoft/#/agents/all",
        "adminCenterRequestedAgentsUrl": "https://admin.cloud.microsoft/#/agents/all/requested",
        "generated": clean_generated,
    }


def developer_portal_url(agent_blueprint_id: str) -> str:
    if not agent_blueprint_id:
        return ""
    return f"https://dev.teams.microsoft.com/tools/agent-blueprint/{agent_blueprint_id}/configuration"


def setup_command(*, agent_name: str, messaging_endpoint: str, ai_teammate: bool, authmode: str) -> list[str]:
    if ai_teammate:
        return [
            "a365",
            "setup",
            "all",
            "--agent-name",
            agent_name,
            "--aiteammate",
            "--m365",
            "--messaging-endpoint",
            messaging_endpoint,
        ]
    return [
        "a365",
        "setup",
        "all",
        "--agent-name",
        agent_name,
        "--m365",
        "--messaging-endpoint",
        messaging_endpoint,
        "--authmode",
        authmode,
    ]


def publish_command(*, agent_name: str, ai_teammate: bool) -> list[str]:
    if ai_teammate:
        return ["a365", "publish", "--agent-name", agent_name, "--aiteammate"]
    return ["a365", "publish", "--agent-name", agent_name, "--use-blueprint"]


def update_endpoint_command(messaging_endpoint: str) -> list[str]:
    return ["a365", "setup", "blueprint", "--update-endpoint", messaging_endpoint]


def customize_manifest(workspace: Path) -> Path:
    manifest_dir = workspace / MANIFEST_DIR
    manifest_path = manifest_dir / MANIFEST_FILE
    package_path = manifest_dir / MANIFEST_PACKAGE
    if not manifest_path.exists():
        raise FileNotFoundError(f"{manifest_path} does not exist. Run `a365 publish` first.")

    manifest = load_json(manifest_path)
    manifest["name"] = {
        "short": "OpenClaw Autopilot",
        "full": "OpenClaw Autopilot on Azure",
    }
    manifest["description"] = {
        "short": "Chat with the OpenClaw autopilot running in ACA Sandboxes.",
        "full": (
            "Autopilots on Azure exposes the OpenClaw Gateway through a governed Agent 365 identity. "
            "It receives Microsoft 365 messages through the bridge /api/messages endpoint, wakes or reuses "
            "the ACA Sandbox Gateway, and returns OpenClaw responses."
        ),
    }
    manifest["developer"] = {
        "name": "Autopilots on Azure demo",
        "mpnId": "",
        "websiteUrl": "https://github.com/tkubica12/agent-demos",
        "privacyUrl": "https://github.com/tkubica12/agent-demos",
        "termsOfUseUrl": "https://github.com/tkubica12/agent-demos",
    }
    write_json(manifest_path, manifest)

    with ZipFile(package_path, "w", compression=ZIP_DEFLATED) as archive:
        for path in manifest_dir.iterdir():
            if path.is_file() and path.name != MANIFEST_PACKAGE:
                archive.write(path, path.name)
    print(f"Packaged {package_path}", flush=True)
    return package_path


def print_command(label: str, command: list[str], *, cwd: Path) -> None:
    print(f"{label}:", flush=True)
    print(f"  cd {cwd}", flush=True)
    print(f"  {' '.join(command)}", flush=True)


def maybe_run(command: list[str], *, cwd: Path, enabled: bool) -> None:
    if enabled:
        run(command, cwd=cwd)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare and optionally run Agent 365 registration for the deployed OpenClaw Autopilot bridge endpoint."
    )
    parser.add_argument("--agent-name", default="OpenClaw Autopilot")
    parser.add_argument("--tenant-id", default="")
    parser.add_argument("--manager-email", default="", help="Optional manager email for AI teammate setup.")
    parser.add_argument("--agent-user-principal-name", default="", help="Optional desired AI teammate user principal name.")
    parser.add_argument("--authmode", choices=["obo", "s2s", "both"], default="obo")
    parser.add_argument(
        "--blueprint-agent",
        action="store_true",
        help="Use blueprint-agent mode instead of the AI teammate flow. AI teammate is the milestone 4 default.",
    )
    parser.add_argument("--run-setup", action="store_true", help="Run `a365 setup all` after preparing the local workspace.")
    parser.add_argument("--update-endpoint", action="store_true", help="Run `a365 setup blueprint --update-endpoint`.")
    parser.add_argument("--publish", action="store_true", help="Run `a365 publish`.")
    parser.add_argument("--capture", action="store_true", help="Write non-secret Agent 365 identifiers from generated config.")
    args = parser.parse_args()

    platform = terraform_output(PLATFORM_DIR)
    suffix = str(platform["suffix"])
    workspace = agent365_workspace(suffix)
    workspace.mkdir(parents=True, exist_ok=True)

    tenant_id = args.tenant_id or current_tenant_id()
    messaging_endpoint = bridge_messaging_endpoint()
    ai_teammate = not args.blueprint_agent
    config = agent365_config_payload(
        agent_name=args.agent_name,
        tenant_id=tenant_id,
        messaging_endpoint=messaging_endpoint,
        ai_teammate=ai_teammate,
        manager_email=args.manager_email,
        agent_user_principal_name=args.agent_user_principal_name,
    )
    config_path = workspace / "a365.config.json"
    if config_path.exists():
        config = merge_config(load_json(config_path), config)
    write_json(config_path, config)

    setup = setup_command(
        agent_name=args.agent_name,
        messaging_endpoint=messaging_endpoint,
        ai_teammate=ai_teammate,
        authmode=args.authmode,
    )
    endpoint_update = update_endpoint_command(messaging_endpoint)
    publish = publish_command(agent_name=args.agent_name, ai_teammate=ai_teammate)

    print_command("Setup command", setup, cwd=workspace)
    print_command("Endpoint update command", endpoint_update, cwd=workspace)
    print_command("Publish command", publish, cwd=workspace)

    maybe_run(setup, cwd=workspace, enabled=args.run_setup)
    maybe_run(endpoint_update, cwd=workspace, enabled=args.update_endpoint)
    maybe_run(publish, cwd=workspace, enabled=args.publish)
    if args.publish:
        customize_manifest(workspace)

    generated_path = workspace / GENERATED_CONFIG
    if args.capture or generated_path.exists():
        if not generated_path.exists():
            raise FileNotFoundError(f"{generated_path} does not exist yet. Run Agent 365 setup first.")
        metadata = build_metadata(config, load_json(generated_path))
        metadata_path = workspace / LOCAL_METADATA
        write_json(metadata_path, metadata)
        portal_url = metadata["developerPortalConfigurationUrl"]
        if portal_url:
            print(f"Developer Portal configuration: {portal_url}", flush=True)
        print(f"Agent 365 identifiers: {metadata_path}", flush=True)


if __name__ == "__main__":
    main()
