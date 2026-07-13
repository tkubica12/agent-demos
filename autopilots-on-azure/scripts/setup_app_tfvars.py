from __future__ import annotations

import argparse
import json
import re
import secrets
import subprocess
from pathlib import Path
from typing import Any

from bridge.gateway_client import generate_bridge_device_identity
from scripts.tf_helpers import APPS_DIR, PLATFORM_DIR, REPO_ROOT, terraform_output, write_tfvars


def random_token() -> str:
    return secrets.token_urlsafe(48)


def load_or_create_device(path: Path) -> dict[str, str]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    device = generate_bridge_device_identity()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(device, indent=2), encoding="utf-8")
    return device


def unprotect_windows_secret(protected_value: str) -> str:
    script = (
        "Add-Type -AssemblyName System.Security; "
        f"$p=[Convert]::FromBase64String('{protected_value}'); "
        "$b=[System.Security.Cryptography.ProtectedData]::Unprotect($p,$null,[System.Security.Cryptography.DataProtectionScope]::CurrentUser); "
        "[Console]::Out.Write([Text.Encoding]::UTF8.GetString($b))"
    )
    result = subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, capture_output=True, text=True)
    return result.stdout


def load_agent365_auth(runtime: str) -> dict[str, str]:
    generated_path = REPO_ROOT / ".local" / runtime / "agent365" / "a365.generated.config.json"
    if not generated_path.exists():
        return {}
    generated = json.loads(generated_path.read_text(encoding="utf-8"))
    client_id = str(generated.get("agentBlueprintId", "")).strip()
    protected_secret = str(generated.get("agentBlueprintClientSecret", "")).strip()
    if not client_id or not protected_secret:
        return {}
    secret = unprotect_windows_secret(protected_secret) if generated.get("agentBlueprintClientSecretProtected") else protected_secret
    return {"client_id": client_id, "client_secret": secret}


def runtime_workspace(runtime: str) -> Path:
    return REPO_ROOT / ".local" / runtime / "apps"


def runtime_app_tfvars_path(runtime: str) -> Path:
    return runtime_workspace(runtime) / "generated.app.auto.tfvars.json"


def runtime_outputs_path(runtime: str) -> Path:
    return runtime_workspace(runtime) / "terraform-outputs.json"


def existing_app_tfvars(runtime: str) -> dict[str, Any]:
    runtime_path = runtime_app_tfvars_path(runtime)
    path = APPS_DIR / "generated.app.auto.tfvars.json"
    precedence_path = APPS_DIR / "generated.runtime.auto.tfvars.json"
    legacy_path = APPS_DIR / "generated.bridge.auto.tfvars.json"
    for candidate in (runtime_path, precedence_path, path, legacy_path):
        if not candidate.exists():
            continue
        payload = json.loads(candidate.read_text(encoding="utf-8"))
        if candidate in {path, precedence_path} and payload.get("agent_runtime") not in {None, "", runtime}:
            continue
        return payload
    return {}


def default_autopilot_name(runtime: str) -> str:
    return runtime


def default_data_volume_name(runtime: str, autopilot_name: str = "") -> str:
    if runtime == "hermes":
        if autopilot_name and autopilot_name != "hermes":
            normalized = "".join(character if character.isalnum() else "-" for character in autopilot_name.lower()).strip("-")
            if not normalized:
                raise ValueError("autopilot_name must contain at least one letter or number.")
            return f"hermes-{normalized[:40]}-data"
        return "hermes-data"
    return "openclaw-kind-data"


def reusable_data_volume_name(runtime: str, value: str) -> str:
    if not value:
        return ""
    other_defaults = {default_data_volume_name(candidate) for candidate in ("openclaw", "hermes") if candidate != runtime}
    if value in other_defaults:
        return ""
    return value


def default_device_identity_path(*, runtime: str, suffix: str) -> Path:
    runtime_path = runtime_workspace(runtime) / "openclaw-bridge-device.json"
    legacy_path = REPO_ROOT / ".local" / suffix / "openclaw-bridge-device.json"
    if not runtime_path.exists() and legacy_path.exists():
        return legacy_path
    return runtime_path


def build_tfvars(
    *,
    runtime: str,
    autopilot_name: str,
    data_volume_name: str,
    previous: dict[str, Any],
    device: dict[str, str] | None = None,
    gateway_token: str = "",
    approved_device_token: str = "",
    api_server_key: str = "",
    runtime_image: str = "",
    runtime_disk_image_name: str = "",
    bridge_image: str = "",
    private_mcp_image: str = "",
    agent365_client_id: str = "",
    agent365_client_secret: str = "",
    agent365_tenant_id: str = "",
    blueprint_name: str = "",
    blueprint_source: str = "",
    blueprint_path: str = "",
    blueprint_version: str = "",
    blueprint_commit: str = "",
    assignee_scope: str = "",
) -> dict[str, Any]:
    tfvars: dict[str, Any] = {
        "autopilot_name": autopilot_name,
        "agent_runtime": runtime,
        "runtime_data_volume_name": data_volume_name,
    }
    if runtime == "openclaw":
        if not device:
            raise ValueError("OpenClaw app tfvars require a bridge device identity.")
        tfvars.update(
            {
                "openclaw_gateway_token": gateway_token or previous.get("openclaw_gateway_token") or random_token(),
                "openclaw_bridge_device_private_key_pem": device["privateKeyPem"],
                "openclaw_bridge_device_token": approved_device_token or previous.get("openclaw_bridge_device_token", ""),
            }
        )
        if runtime_image:
            tfvars["runtime_image"] = runtime_image
        if runtime_disk_image_name:
            tfvars["runtime_disk_image_name"] = runtime_disk_image_name
    elif runtime == "hermes":
        tfvars["api_server_key"] = api_server_key or previous.get("api_server_key") or previous.get("hermes_api_server_key") or random_token()
        tfvars["runtime_image"] = runtime_image or previous.get("runtime_image", "")
        tfvars["runtime_disk_image_name"] = runtime_disk_image_name or previous.get("runtime_disk_image_name", "hermes-api-server-image")
        blueprint_values = {
            "hermes_blueprint_name": blueprint_name or previous.get("hermes_blueprint_name", ""),
            "hermes_blueprint_source": blueprint_source or previous.get("hermes_blueprint_source", ""),
            "hermes_blueprint_path": blueprint_path or previous.get("hermes_blueprint_path", ""),
            "hermes_blueprint_version": blueprint_version or previous.get("hermes_blueprint_version", ""),
            "hermes_blueprint_commit": blueprint_commit or previous.get("hermes_blueprint_commit", ""),
            "hermes_assignee_scope": assignee_scope or previous.get("hermes_assignee_scope", ""),
        }
        if any(blueprint_values.values()):
            required_blueprint_values = {
                key: blueprint_values[key]
                for key in (
                    "hermes_blueprint_name",
                    "hermes_blueprint_source",
                    "hermes_blueprint_version",
                    "hermes_blueprint_commit",
                )
            }
            missing = [key for key, value in required_blueprint_values.items() if not value]
            if missing:
                raise ValueError(f"Hermes blueprint configuration requires: {', '.join(missing)}.")
            if not re.fullmatch(r"[0-9a-fA-F]{40}", blueprint_values["hermes_blueprint_commit"]):
                raise ValueError("hermes_blueprint_commit must be a full 40-character Git commit SHA.")
        tfvars.update({key: value for key, value in blueprint_values.items() if value})
    else:
        raise ValueError(f"Unsupported runtime '{runtime}'.")
    resolved_agent365_client_id = agent365_client_id or previous.get("agent365_client_id", "")
    resolved_agent365_client_secret = agent365_client_secret or previous.get("agent365_client_secret", "")
    resolved_agent365_tenant_id = agent365_tenant_id or previous.get("agent365_tenant_id", "")
    if resolved_agent365_client_id:
        tfvars["agent365_client_id"] = resolved_agent365_client_id
    if resolved_agent365_client_secret:
        tfvars["agent365_client_secret"] = resolved_agent365_client_secret
    if resolved_agent365_tenant_id:
        tfvars["agent365_tenant_id"] = resolved_agent365_tenant_id
    resolved_bridge_image = bridge_image or previous.get("bridge_image", "")
    resolved_private_mcp_image = private_mcp_image or previous.get("private_mcp_image", "")
    if resolved_bridge_image:
        tfvars["bridge_image"] = resolved_bridge_image
    if resolved_private_mcp_image:
        tfvars["private_mcp_image"] = resolved_private_mcp_image
    return tfvars


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare runtime-specific app bootstrap values and write apps generated tfvars.")
    parser.add_argument("--runtime", choices=["openclaw", "hermes"], default="openclaw")
    parser.add_argument("--autopilot-name", default="")
    parser.add_argument("--data-volume-name", default="")
    parser.add_argument("--gateway-token", default="")
    parser.add_argument("--device-identity-file", default="")
    parser.add_argument("--approved-device-token", default="")
    parser.add_argument("--api-server-key", default="", help="Hermes API_SERVER_KEY. Generated when omitted.")
    parser.add_argument("--blueprint-name", default="", help="Hermes profile distribution name.")
    parser.add_argument("--blueprint-source", default="", help="Git repository URL containing the Hermes distribution.")
    parser.add_argument("--blueprint-path", default="", help="Distribution path relative to the repository root.")
    parser.add_argument("--blueprint-version", default="", help="Expected distribution.yaml version.")
    parser.add_argument("--blueprint-commit", default="", help="Full Git commit SHA to install.")
    parser.add_argument("--assignee-scope", default="", help="Person, team, or workstream assigned to this worker.")
    parser.add_argument("--runtime-image", default="", help="Runtime image digest. Recommended for Hermes to avoid reusing an OpenClaw image tfvars value.")
    parser.add_argument("--runtime-disk-image-name", default="", help="ACA Sandbox runtime disk image name.")
    parser.add_argument("--bridge-image", default="", help="Bridge image digest. Use to pin runtime deployments to a tested bridge build.")
    parser.add_argument("--private-mcp-image", default="", help="Private incidents MCP image digest.")
    parser.add_argument("--agent365-client-id", default="", help="Agent 365 blueprint app ID for Microsoft Agents SDK auth.")
    parser.add_argument("--agent365-client-secret", default="", help="Agent 365 blueprint client secret for Microsoft Agents SDK auth.")
    parser.add_argument("--agent365-tenant-id", default="", help="Tenant ID for Microsoft Agents SDK auth. Defaults to tenant if omitted by Terraform.")
    parser.add_argument(
        "--agent365-from-generated",
        action="store_true",
        help="Load Agent 365 blueprint ID and protected client secret from .local/<runtime>/agent365/a365.generated.config.json.",
    )
    parser.add_argument("--runtime-only", action="store_true", help="Only write .local/<runtime>/apps tfvars, not terraform/apps active tfvars.")
    args = parser.parse_args()
    platform = terraform_output(PLATFORM_DIR)
    suffix = platform["suffix"]
    runtime = args.runtime
    autopilot_name = args.autopilot_name or default_autopilot_name(runtime)

    previous = existing_app_tfvars(runtime)
    agent365_auth = load_agent365_auth(runtime) if args.agent365_from_generated else {}
    data_volume_name = (
        args.data_volume_name
        or reusable_data_volume_name(runtime, previous.get("runtime_data_volume_name") or "")
        or default_data_volume_name(runtime, autopilot_name)
    )
    device: dict[str, str] | None = None
    device_path: Path | None = None
    if runtime == "openclaw":
        device_path = Path(args.device_identity_file) if args.device_identity_file else default_device_identity_path(runtime=runtime, suffix=suffix)
        device = load_or_create_device(device_path)

    tfvars = build_tfvars(
        runtime=runtime,
        autopilot_name=autopilot_name,
        data_volume_name=data_volume_name,
        previous=previous,
        device=device,
        gateway_token=args.gateway_token,
        approved_device_token=args.approved_device_token,
        api_server_key=args.api_server_key,
        runtime_image=args.runtime_image,
        runtime_disk_image_name=args.runtime_disk_image_name,
        bridge_image=args.bridge_image,
        private_mcp_image=args.private_mcp_image,
        agent365_client_id=args.agent365_client_id or agent365_auth.get("client_id", ""),
        agent365_client_secret=args.agent365_client_secret or agent365_auth.get("client_secret", ""),
        agent365_tenant_id=args.agent365_tenant_id,
        blueprint_name=args.blueprint_name,
        blueprint_source=args.blueprint_source,
        blueprint_path=args.blueprint_path,
        blueprint_version=args.blueprint_version,
        blueprint_commit=args.blueprint_commit,
        assignee_scope=args.assignee_scope,
    )
    runtime_path = runtime_app_tfvars_path(runtime)
    write_tfvars(runtime_path, tfvars)
    active_path = APPS_DIR / "generated.app.auto.tfvars.json"
    precedence_path = APPS_DIR / "generated.runtime.auto.tfvars.json"
    if not args.runtime_only:
        write_tfvars(active_path, tfvars)
        write_tfvars(precedence_path, tfvars)
    print(
        json.dumps(
            {
                "runtime": runtime,
                "autopilotName": autopilot_name,
                "dataVolumeName": data_volume_name,
                "runtimeTfvarsFile": str(runtime_path),
                "activeTfvarsFile": "" if args.runtime_only else str(active_path),
                "activePrecedenceTfvarsFile": "" if args.runtime_only else str(precedence_path),
                "deviceId": device["deviceId"] if device else "",
                "deviceIdentityFile": str(device_path) if device_path else "",
                "approvedDeviceTokenConfigured": bool(tfvars.get("openclaw_bridge_device_token")),
                "apiServerKeyConfigured": bool(tfvars.get("api_server_key")),
                "next": "Run terraform apply in terraform/apps. The bridge uses a managed identity for Azure API calls.",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
