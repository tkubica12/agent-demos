from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from scripts.tf_helpers import APPS_DIR, PLATFORM_DIR, REPO_ROOT, output, run, terraform_output


GENERATED_CONFIG = "a365.generated.config.json"
MANIFEST_DIR = "manifest"
MANIFEST_FILE = "manifest.json"
MANIFEST_PACKAGE = "manifest.zip"
SECRET_KEYS = {
    "agentBlueprintClientSecret",
    "agentBlueprintClientSecretProtected",
    "clientSecret",
    "secret",
}


@dataclass(frozen=True)
class Agent365Branding:
    autopilot_name: str
    runtime_kind: str
    agent_name: str
    manifest_short_name: str
    manifest_full_name: str
    description_short: str
    description_full: str
    developer_name: str = "Autopilots on Azure demo"


def default_branding(runtime_kind: str, autopilot_name: str = "") -> Agent365Branding:
    runtime_kind = runtime_kind.strip().lower()
    if runtime_kind == "hermes":
        autopilot_name = autopilot_name or "hermes"
        return Agent365Branding(
            autopilot_name=autopilot_name,
            runtime_kind=runtime_kind,
            agent_name="Hermes Autopilot",
            manifest_short_name="Hermes Autopilot",
            manifest_full_name="Hermes Autopilot on Azure",
            description_short="Chat with the Hermes autopilot running in ACA Sandboxes.",
            description_full=(
                "Autopilots on Azure exposes Hermes Agent through a governed Agent 365 identity. "
                "It receives Microsoft 365 messages through the bridge /api/messages endpoint, wakes or reuses "
                "the ACA Sandbox Hermes runtime, and returns Hermes responses."
            ),
        )
    if runtime_kind == "openclaw":
        autopilot_name = autopilot_name or "openclaw"
        return Agent365Branding(
            autopilot_name=autopilot_name,
            runtime_kind=runtime_kind,
            agent_name="OpenClaw Autopilot",
            manifest_short_name="OpenClaw Autopilot",
            manifest_full_name="OpenClaw Autopilot on Azure",
            description_short="Chat with the OpenClaw autopilot running in ACA Sandboxes.",
            description_full=(
                "Autopilots on Azure exposes the OpenClaw Gateway through a governed Agent 365 identity. "
                "It receives Microsoft 365 messages through the bridge /api/messages endpoint, wakes or reuses "
                "the ACA Sandbox Gateway, and returns OpenClaw responses."
            ),
        )
    raise ValueError(f"Unsupported runtime kind '{runtime_kind}'.")


def metadata_file_name(autopilot_name: str) -> str:
    return f"{autopilot_name}-agent365-identifiers.json"


def current_tenant_id() -> str:
    return output(["az", "account", "show", "--query", "tenantId", "-o", "tsv"])


def bridge_messaging_endpoint() -> str:
    apps = terraform_output(APPS_DIR)
    bridge_url = str(apps["bridge_url"]).rstrip("/")
    return f"{bridge_url}/api/messages"


def runtime_outputs_path(runtime_kind: str) -> Path:
    return REPO_ROOT / ".local" / runtime_kind / "apps" / "terraform-outputs.json"


def normalize_messaging_endpoint(value: str) -> str:
    endpoint = value.strip().rstrip("/")
    if not endpoint:
        raise ValueError("Messaging endpoint cannot be empty.")
    if endpoint.endswith("/api/messages"):
        return endpoint
    return f"{endpoint}/api/messages"


def messaging_endpoint_from_outputs(path: Path) -> str:
    payload = load_json(path)
    bridge_url = str(payload.get("bridge_url", "")).strip()
    if not bridge_url:
        raise KeyError(f"{path} does not contain bridge_url.")
    return normalize_messaging_endpoint(bridge_url)


def resolve_messaging_endpoint(*, runtime_kind: str, explicit_endpoint: str, outputs_file: str) -> str:
    if explicit_endpoint:
        return normalize_messaging_endpoint(explicit_endpoint)
    path = Path(outputs_file) if outputs_file else runtime_outputs_path(runtime_kind)
    if path.exists():
        return messaging_endpoint_from_outputs(path)
    return bridge_messaging_endpoint()


def agent365_workspace(autopilot_name: str) -> Path:
    return REPO_ROOT / ".local" / autopilot_name / "agent365"


def agent365_config_payload(
    *,
    autopilot_name: str,
    runtime_kind: str,
    agent_name: str,
    tenant_id: str,
    messaging_endpoint: str,
    ai_teammate: bool,
    manager_email: str = "",
    agent_user_principal_name: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "agentName": agent_name,
        "autopilotName": autopilot_name,
        "agentRuntime": runtime_kind,
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
        "autopilotName": config.get("autopilotName", ""),
        "agentRuntime": config.get("agentRuntime", ""),
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


def setup_command(
    *,
    agent_name: str,
    tenant_id: str,
    messaging_endpoint: str,
    ai_teammate: bool,
    authmode: str,
    dry_run: bool = False,
    skip_requirements: bool = False,
    skip_sp_provisioning: bool = False,
) -> list[str]:
    if ai_teammate:
        command = [
            "a365",
            "setup",
            "all",
            "--agent-name",
            agent_name,
            "--tenant-id",
            tenant_id,
            "--aiteammate",
            "--m365",
            "--messaging-endpoint",
            messaging_endpoint,
        ]
    else:
        command = [
            "a365",
            "setup",
            "all",
            "--agent-name",
            agent_name,
            "--tenant-id",
            tenant_id,
            "--m365",
            "--messaging-endpoint",
            messaging_endpoint,
            "--authmode",
            authmode,
        ]
    if dry_run:
        command.append("--dry-run")
    if skip_requirements:
        command.append("--skip-requirements")
    if skip_sp_provisioning:
        command.append("--skip-sp-provisioning")
    return command


def publish_command(*, agent_name: str, ai_teammate: bool) -> list[str]:
    if ai_teammate:
        return ["a365", "publish", "--agent-name", agent_name, "--aiteammate"]
    return ["a365", "publish", "--agent-name", agent_name, "--use-blueprint"]


def update_endpoint_command(messaging_endpoint: str) -> list[str]:
    return ["a365", "setup", "blueprint", "--update-endpoint", messaging_endpoint]


def customize_manifest(workspace: Path, branding: Agent365Branding) -> Path:
    manifest_dir = workspace / MANIFEST_DIR
    manifest_path = manifest_dir / MANIFEST_FILE
    package_path = manifest_dir / MANIFEST_PACKAGE
    if not manifest_path.exists():
        raise FileNotFoundError(f"{manifest_path} does not exist. Run `a365 publish` first.")

    manifest = load_json(manifest_path)
    manifest["name"] = {
        "short": branding.manifest_short_name,
        "full": branding.manifest_full_name,
    }
    manifest["description"] = {
        "short": branding.description_short,
        "full": branding.description_full,
    }
    manifest["developer"] = {
        "name": branding.developer_name,
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
        description="Prepare and optionally run Agent 365 registration for an Autopilots on Azure bridge endpoint."
    )
    parser.add_argument("--runtime", choices=["openclaw", "hermes"], default="openclaw")
    parser.add_argument("--autopilot-name", default="")
    parser.add_argument("--agent-name", default="")
    parser.add_argument("--manifest-short-name", default="")
    parser.add_argument("--manifest-full-name", default="")
    parser.add_argument("--description-short", default="")
    parser.add_argument("--description-full", default="")
    parser.add_argument("--tenant-id", default="")
    parser.add_argument(
        "--messaging-endpoint",
        default="",
        help="Explicit Agent 365 messaging endpoint. A bridge base URL is accepted and /api/messages is appended.",
    )
    parser.add_argument(
        "--runtime-outputs-file",
        default="",
        help="Terraform output JSON captured by scripts.deploy_apps_runtime. Defaults to .local/<runtime>/apps/terraform-outputs.json.",
    )
    parser.add_argument("--manager-email", default="", help="Optional manager email for AI teammate setup.")
    parser.add_argument("--agent-user-principal-name", default="", help="Optional desired AI teammate user principal name.")
    parser.add_argument("--authmode", choices=["obo", "s2s", "both"], default="obo")
    parser.add_argument(
        "--blueprint-agent",
        action="store_true",
        help="Use blueprint-agent mode instead of the AI teammate flow. AI teammate is the milestone 4 default.",
    )
    parser.add_argument("--run-setup", action="store_true", help="Run `a365 setup all` after preparing the local workspace.")
    parser.add_argument("--dry-run", action="store_true", help="Pass --dry-run to `a365 setup all`.")
    parser.add_argument("--skip-requirements", action="store_true", help="Pass --skip-requirements to `a365 setup all`.")
    parser.add_argument(
        "--skip-sp-provisioning",
        action="store_true",
        help="Pass --skip-sp-provisioning to `a365 setup all`.",
    )
    parser.add_argument("--update-endpoint", action="store_true", help="Run `a365 setup blueprint --update-endpoint`.")
    parser.add_argument("--publish", action="store_true", help="Run `a365 publish`.")
    parser.add_argument("--capture", action="store_true", help="Write non-secret Agent 365 identifiers from generated config.")
    args = parser.parse_args()

    branding_defaults = default_branding(args.runtime, args.autopilot_name)
    branding = Agent365Branding(
        autopilot_name=branding_defaults.autopilot_name,
        runtime_kind=args.runtime,
        agent_name=args.agent_name or branding_defaults.agent_name,
        manifest_short_name=args.manifest_short_name or branding_defaults.manifest_short_name,
        manifest_full_name=args.manifest_full_name or branding_defaults.manifest_full_name,
        description_short=args.description_short or branding_defaults.description_short,
        description_full=args.description_full or branding_defaults.description_full,
        developer_name=branding_defaults.developer_name,
    )
    workspace = agent365_workspace(branding.autopilot_name)
    workspace.mkdir(parents=True, exist_ok=True)

    tenant_id = args.tenant_id or current_tenant_id()
    messaging_endpoint = resolve_messaging_endpoint(
        runtime_kind=branding.runtime_kind,
        explicit_endpoint=args.messaging_endpoint,
        outputs_file=args.runtime_outputs_file,
    )
    ai_teammate = not args.blueprint_agent
    config = agent365_config_payload(
        autopilot_name=branding.autopilot_name,
        runtime_kind=branding.runtime_kind,
        agent_name=branding.agent_name,
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
        agent_name=branding.agent_name,
        tenant_id=tenant_id,
        messaging_endpoint=messaging_endpoint,
        ai_teammate=ai_teammate,
        authmode=args.authmode,
        dry_run=args.dry_run,
        skip_requirements=args.skip_requirements,
        skip_sp_provisioning=args.skip_sp_provisioning,
    )
    endpoint_update = update_endpoint_command(messaging_endpoint)
    publish = publish_command(agent_name=branding.agent_name, ai_teammate=ai_teammate)

    print_command("Setup command", setup, cwd=workspace)
    print_command("Endpoint update command", endpoint_update, cwd=workspace)
    print_command("Publish command", publish, cwd=workspace)

    maybe_run(setup, cwd=workspace, enabled=args.run_setup)
    maybe_run(endpoint_update, cwd=workspace, enabled=args.update_endpoint)
    maybe_run(publish, cwd=workspace, enabled=args.publish)
    if args.publish:
        customize_manifest(workspace, branding)

    generated_path = workspace / GENERATED_CONFIG
    if args.capture or generated_path.exists():
        if not generated_path.exists():
            raise FileNotFoundError(f"{generated_path} does not exist yet. Run Agent 365 setup first.")
        metadata = build_metadata(config, load_json(generated_path))
        metadata_path = workspace / metadata_file_name(branding.autopilot_name)
        write_json(metadata_path, metadata)
        portal_url = metadata["developerPortalConfigurationUrl"]
        if portal_url:
            print(f"Developer Portal configuration: {portal_url}", flush=True)
        print(f"Agent 365 identifiers: {metadata_path}", flush=True)


if __name__ == "__main__":
    main()
