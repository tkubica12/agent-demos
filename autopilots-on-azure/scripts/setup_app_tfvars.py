from __future__ import annotations

import argparse
import json
import secrets
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


def runtime_workspace(runtime: str) -> Path:
    return REPO_ROOT / ".local" / runtime / "apps"


def runtime_app_tfvars_path(runtime: str) -> Path:
    return runtime_workspace(runtime) / "generated.app.auto.tfvars.json"


def existing_app_tfvars(runtime: str) -> dict[str, Any]:
    runtime_path = runtime_app_tfvars_path(runtime)
    path = APPS_DIR / "generated.app.auto.tfvars.json"
    precedence_path = APPS_DIR / "generated.runtime.auto.tfvars.json"
    legacy_path = APPS_DIR / "generated.bridge.auto.tfvars.json"
    for candidate in (runtime_path, precedence_path, path, legacy_path):
        if not candidate.exists():
            continue
        payload = json.loads(candidate.read_text(encoding="utf-8"))
        if candidate == path and payload.get("agent_runtime") not in {None, "", runtime}:
            continue
        return payload
    return {}


def default_autopilot_name(runtime: str) -> str:
    return runtime


def default_bot_display_name(runtime: str) -> str:
    if runtime == "hermes":
        return "Hermes Autopilot"
    return "OpenClaw Autopilot"


def default_data_volume_name(runtime: str) -> str:
    if runtime == "hermes":
        return "hermes-data"
    return "openclaw-kind-data"


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
    bot_display_name: str,
    data_volume_name: str,
    previous: dict[str, Any],
    device: dict[str, str] | None = None,
    gateway_token: str = "",
    approved_device_token: str = "",
    api_server_key: str = "",
    runtime_image: str = "",
    runtime_disk_image_name: str = "",
) -> dict[str, Any]:
    tfvars: dict[str, Any] = {
        "autopilot_name": autopilot_name,
        "agent_runtime": runtime,
        "bot_display_name": bot_display_name,
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
    else:
        raise ValueError(f"Unsupported runtime '{runtime}'.")
    return tfvars


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare runtime-specific app bootstrap values and write apps generated tfvars.")
    parser.add_argument("--runtime", choices=["openclaw", "hermes"], default="openclaw")
    parser.add_argument("--autopilot-name", default="")
    parser.add_argument("--bot-display-name", default="")
    parser.add_argument("--data-volume-name", default="")
    parser.add_argument("--gateway-token", default="")
    parser.add_argument("--device-identity-file", default="")
    parser.add_argument("--approved-device-token", default="")
    parser.add_argument("--api-server-key", default="", help="Hermes API_SERVER_KEY. Generated when omitted.")
    parser.add_argument("--runtime-image", default="", help="Runtime image digest. Recommended for Hermes to avoid reusing an OpenClaw image tfvars value.")
    parser.add_argument("--runtime-disk-image-name", default="", help="ACA Sandbox runtime disk image name.")
    parser.add_argument("--runtime-only", action="store_true", help="Only write .local/<runtime>/apps tfvars, not terraform/apps active tfvars.")
    args = parser.parse_args()
    platform = terraform_output(PLATFORM_DIR)
    suffix = platform["suffix"]
    runtime = args.runtime
    autopilot_name = args.autopilot_name or default_autopilot_name(runtime)
    bot_display_name = args.bot_display_name or default_bot_display_name(runtime)

    previous = existing_app_tfvars(runtime)
    data_volume_name = args.data_volume_name or previous.get("runtime_data_volume_name") or default_data_volume_name(runtime)
    device: dict[str, str] | None = None
    device_path: Path | None = None
    if runtime == "openclaw":
        device_path = Path(args.device_identity_file) if args.device_identity_file else default_device_identity_path(runtime=runtime, suffix=suffix)
        device = load_or_create_device(device_path)

    tfvars = build_tfvars(
        runtime=runtime,
        autopilot_name=autopilot_name,
        bot_display_name=bot_display_name,
        data_volume_name=data_volume_name,
        previous=previous,
        device=device,
        gateway_token=args.gateway_token,
        approved_device_token=args.approved_device_token,
        api_server_key=args.api_server_key,
        runtime_image=args.runtime_image,
        runtime_disk_image_name=args.runtime_disk_image_name,
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
