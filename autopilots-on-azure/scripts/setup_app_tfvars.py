from __future__ import annotations

import argparse
import json
import secrets
from pathlib import Path

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


def existing_app_tfvars() -> dict:
    path = APPS_DIR / "generated.app.auto.tfvars.json"
    legacy_path = APPS_DIR / "generated.bridge.auto.tfvars.json"
    if not path.exists():
        if legacy_path.exists():
            return json.loads(legacy_path.read_text(encoding="utf-8"))
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare OpenClaw runtime pairing values and write apps generated tfvars.")
    parser.add_argument("--autopilot-name", default="openclaw")
    parser.add_argument("--bot-display-name", default="OpenClaw Autopilot")
    parser.add_argument("--data-volume-name", default="")
    parser.add_argument("--gateway-token", default="")
    parser.add_argument("--device-identity-file", default="")
    parser.add_argument("--approved-device-token", default="")
    args = parser.parse_args()
    platform = terraform_output(PLATFORM_DIR)
    suffix = platform["suffix"]
    device_path = Path(args.device_identity_file) if args.device_identity_file else REPO_ROOT / ".local" / suffix / "openclaw-bridge-device.json"

    previous = existing_app_tfvars()
    gateway_token = args.gateway_token or previous.get("openclaw_gateway_token") or random_token()
    approved_device_token = args.approved_device_token or previous.get("openclaw_bridge_device_token", "")
    data_volume_name = args.data_volume_name or previous.get("runtime_data_volume_name") or previous.get("openclaw_data_volume_name") or "openclaw-data"
    device = load_or_create_device(device_path)

    tfvars = {
        "autopilot_name": args.autopilot_name,
        "agent_runtime": "openclaw",
        "bot_display_name": args.bot_display_name,
        "openclaw_gateway_token": gateway_token,
        "openclaw_bridge_device_private_key_pem": device["privateKeyPem"],
        "openclaw_bridge_device_token": approved_device_token,
        "runtime_data_volume_name": data_volume_name,
    }
    write_tfvars(APPS_DIR / "generated.app.auto.tfvars.json", tfvars)
    print(
        json.dumps(
            {
                "dataVolumeName": data_volume_name,
                "deviceId": device["deviceId"],
                "deviceIdentityFile": str(device_path),
                "approvedDeviceTokenConfigured": bool(approved_device_token),
                "next": "Run terraform apply in terraform/apps. The bridge uses a managed identity for Azure API calls.",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
