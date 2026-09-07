from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

from scripts.tf_helpers import REPO_ROOT, output, run


GENERATED_CONFIG = "a365.generated.config.json"
MANIFEST_DIR = "manifest"
MANIFEST_FILE = "manifest.json"
MANIFEST_PACKAGE = "manifest.zip"
TOOLING_MANIFEST = REPO_ROOT / "agent365" / "ToolingManifest.json"
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


def default_branding(autopilot_name: str = "hermes") -> Agent365Branding:
    return Agent365Branding(
        autopilot_name=autopilot_name,
        runtime_kind="hermes",
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


def metadata_file_name(autopilot_name: str) -> str:
    return f"{autopilot_name}-agent365-identifiers.json"


def current_tenant_id() -> str:
    return output(["az", "account", "show", "--query", "tenantId", "-o", "tsv"])


def runtime_outputs_path(runtime_kind: str) -> Path:
    return REPO_ROOT / ".local" / runtime_kind / "apps" / "terraform-outputs.json"


def normalize_messaging_endpoint(value: str) -> str:
    endpoint = value.strip().rstrip("/")
    if not endpoint:
        raise ValueError("Messaging endpoint cannot be empty.")
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Messaging endpoint must be an absolute HTTPS URL without credentials, query, or fragment.")
    if endpoint.endswith("/api/messages"):
        return endpoint
    return f"{endpoint}/api/messages"


def endpoint_update_config(
    workspace: Path, *, tenant_id: str, messaging_endpoint: str,
) -> dict[str, Any]:
    config = load_json(workspace / "a365.config.json")
    generated = load_json(workspace / GENERATED_CONFIG)
    if not generated.get("agentBlueprintId"):
        raise ValueError("Endpoint updates require an existing Agent 365 blueprint.")
    if config.get("tenantId") != tenant_id:
        raise ValueError("Endpoint update tenant differs from the existing Agent 365 configuration.")
    return {**config, "messagingEndpoint": normalize_messaging_endpoint(messaging_endpoint)}


def require_endpoint_update_owner(workspace: Path) -> None:
    from scripts.provision_agent365_instance import GraphClient, GraphError
    from scripts.setup_identity import blueprint_application_object_id

    blueprint_client_id = str(load_json(workspace / GENERATED_CONFIG)["agentBlueprintId"])
    try:
        graph = GraphClient.from_az_cli()
        user = graph.request("GET", "/me?$select=id,userPrincipalName")
        user_id = str(user.get("id") or "")
        if not user_id:
            raise ValueError("Microsoft Graph did not identify a signed-in user.")
        blueprint_object_id = blueprint_application_object_id(graph, blueprint_client_id)
        path = f"/applications/{blueprint_object_id}/microsoft.graph.agentIdentityBlueprint/owners?$select=id"
        while path:
            owners = graph.request("GET", path)
            if any(str(owner.get("id") or "").lower() == user_id.lower() for owner in owners["value"]):
                return
            path = owners.get("@odata.nextLink")
    except (GraphError, subprocess.SubprocessError, OSError, ValueError, KeyError) as exc:
        raise RuntimeError(
            "Endpoint update blocked: unable to verify direct Agent Blueprint ownership. "
            "Confirm the Azure CLI session is signed in as an existing blueprint owner "
            "and can read Microsoft Graph /me and blueprint owners. "
            "The a365 update was not invoked; no endpoint was changed."
        ) from exc
    raise PermissionError(
        f"Endpoint update blocked: Graph user {user.get('userPrincipalName') or user_id} ({user_id}) "
        f"is not a direct owner of Agent Blueprint {blueprint_client_id}. "
        "The a365 CLI can delete the old endpoint before failing to create its replacement. "
        "Rerun only as an existing blueprint owner in an isolated Azure CLI session; "
        "keep the deployment-operator session for Terraform. No endpoint was changed."
    )


def messaging_endpoint_from_outputs(path: Path) -> str:
    payload = load_json(path)
    bridge_url = str(payload.get("bridge_url", "")).strip()
    if not bridge_url:
        raise KeyError(f"{path} does not contain bridge_url.")
    return normalize_messaging_endpoint(bridge_url)


def resolve_messaging_endpoint(
    *,
    runtime_kind: str,
    explicit_endpoint: str,
    outputs_file: str,
    state_name: str = "",
) -> str:
    if explicit_endpoint:
        return normalize_messaging_endpoint(explicit_endpoint)
    path = (
        Path(outputs_file)
        if outputs_file
        else runtime_outputs_path(state_name or runtime_kind)
    )
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. Deploy this Worker's Sandbox services first, "
            "or pass its real --messaging-endpoint explicitly."
        )
    return messaging_endpoint_from_outputs(path)


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


def missing_tooling_permissions(
    workspace: Path,
    tooling_manifest: Path | None = None,
) -> list[str]:
    generated_path = workspace / GENERATED_CONFIG
    if not generated_path.exists():
        return ["Agent 365 generated configuration"]
    generated = load_json(generated_path)
    consents = generated.get("resourceConsents", [])
    manifest = load_json(tooling_manifest or TOOLING_MANIFEST)
    missing = []
    for server in manifest.get("mcpServers", []):
        audience = str(server.get("audience") or "").strip()
        scope = str(server.get("scope") or "").strip()
        if any(
            consent.get("resourceAppId") == audience
            and consent.get("consentGranted") is True
            and consent.get("inheritablePermissionsConfigured") is True
            and scope in consent.get("scopes", [])
            for consent in consents
        ):
            continue
        missing.append(
            str(
                server.get("mcpServerName")
                or server.get("mcpServerUniqueName")
                or audience
            )
        )
    return missing


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


def bump_manifest_patch_version(manifest: dict[str, Any]) -> None:
    version = str(manifest.get("version") or "")
    parts = version.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError(
            "Agent 365 manifest version must be numeric semantic versioning."
        )
    major, minor, patch = (int(part) for part in parts)
    manifest["version"] = f"{major}.{minor}.{patch + 1}"


def remove_unsupported_agentic_bot_capability(
    manifest: dict[str, Any],
) -> None:
    if manifest.get("agenticUserTemplates"):
        manifest.pop("bots", None)


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
    remove_unsupported_agentic_bot_capability(manifest)
    bump_manifest_patch_version(manifest)
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
    parser.add_argument("--state-name", default="hermes", help="Worker state directory under .local (default: hermes).")
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
        help="Terraform output JSON captured by scripts.deploy_apps_runtime. Defaults to .local/<state-name>/apps/terraform-outputs.json.",
    )
    parser.add_argument("--manager-email", default="", help="Optional manager email for AI teammate setup.")
    parser.add_argument("--agent-user-principal-name", default="", help="Optional desired AI teammate user principal name.")
    parser.add_argument("--authmode", choices=["obo", "s2s", "both"], default="obo")
    parser.add_argument(
        "--blueprint-agent",
        action="store_true",
        help="Use blueprint-agent mode instead of the AI teammate flow. AI teammate is the default.",
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
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Run `a365 publish` to create the local package ZIP. Upload to Microsoft 365 admin center is still manual.",
    )
    parser.add_argument("--capture", action="store_true", help="Write non-secret Agent 365 identifiers from generated config.")
    args = parser.parse_args()
    if args.update_endpoint and args.run_setup:
        parser.error("Update the existing endpoint separately from --run-setup.")
    if args.dry_run and (args.update_endpoint or args.publish):
        parser.error("--dry-run applies only to setup; omit --update-endpoint and --publish to preview their commands.")

    branding_defaults = default_branding(args.autopilot_name or args.state_name)
    branding = Agent365Branding(
        autopilot_name=branding_defaults.autopilot_name,
        runtime_kind="hermes",
        agent_name=args.agent_name or branding_defaults.agent_name,
        manifest_short_name=args.manifest_short_name or branding_defaults.manifest_short_name,
        manifest_full_name=args.manifest_full_name or branding_defaults.manifest_full_name,
        description_short=args.description_short or branding_defaults.description_short,
        description_full=args.description_full or branding_defaults.description_full,
        developer_name=branding_defaults.developer_name,
    )
    workspace = agent365_workspace(args.state_name)
    workspace.mkdir(parents=True, exist_ok=True)

    tenant_id = args.tenant_id or current_tenant_id()
    messaging_endpoint = resolve_messaging_endpoint(
        runtime_kind=branding.runtime_kind,
        explicit_endpoint=args.messaging_endpoint,
        outputs_file=args.runtime_outputs_file,
        state_name=args.state_name,
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
    if args.update_endpoint:
        config = endpoint_update_config(workspace, tenant_id=tenant_id, messaging_endpoint=messaging_endpoint)
        require_endpoint_update_owner(workspace)
    elif config_path.exists():
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
    if args.publish:
        missing_permissions = missing_tooling_permissions(workspace)
        if missing_permissions:
            raise RuntimeError(
                "Agent 365 permissions are behind ToolingManifest.json: "
                + ", ".join(missing_permissions)
                + ". Run scripts.setup_identity for this Worker before "
                "publishing its package."
            )
    maybe_run(publish, cwd=workspace, enabled=args.publish)
    if args.publish:
        package_path = customize_manifest(workspace, branding)
        print(
            "Upload remains manual: use Microsoft 365 admin center -> Agents -> All agents -> Upload custom agent "
            f"with {package_path}.",
            flush=True,
        )

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
