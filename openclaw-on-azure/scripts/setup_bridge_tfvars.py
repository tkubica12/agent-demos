from __future__ import annotations

import argparse
import json
import os
import secrets
from pathlib import Path

from bridge.gateway_client import generate_bridge_device_identity
from scripts.tf_helpers import APPS_DIR, PLATFORM_DIR, REPO_ROOT, output, run, terraform_output, write_tfvars


def random_token() -> str:
    return secrets.token_urlsafe(48)


def load_or_create_device(path: Path) -> dict[str, str]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    device = generate_bridge_device_identity()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(device, indent=2), encoding="utf-8")
    return device


def find_service_principal_app_id(display_name: str) -> str:
    return output(
        [
            "az",
            "ad",
            "sp",
            "list",
            "--display-name",
            display_name,
            "--query",
            "[0].appId",
            "-o",
            "tsv",
        ],
        check=False,
    ).strip()


def ensure_service_principal(display_name: str) -> tuple[str, str, str]:
    existing_app_id = find_service_principal_app_id(display_name)
    if existing_app_id:
        print(f"Reusing service principal {display_name}. Rotating secret.", flush=True)
        raw = output(
            [
                "az",
                "ad",
                "sp",
                "credential",
                "reset",
                "--id",
                existing_app_id,
                "--display-name",
                "openclaw-bridge",
                "--years",
                "1",
                "-o",
                "json",
            ]
        )
    else:
        print(f"Creating service principal {display_name}.", flush=True)
        raw = output(["az", "ad", "sp", "create-for-rbac", "--name", display_name, "--skip-assignment", "-o", "json"])
    payload = json.loads(raw)
    client_id = payload["appId"]
    secret = payload.get("password") or payload.get("secretText")
    if not secret:
        raise RuntimeError("Azure CLI did not return a service principal secret.")
    object_id = output(["az", "ad", "sp", "show", "--id", client_id, "--query", "id", "-o", "tsv"])
    return client_id, object_id, secret


def existing_bridge_tfvars() -> dict:
    path = APPS_DIR / "generated.bridge.auto.tfvars.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare bridge credentials and write apps generated bridge tfvars.")
    parser.add_argument("--service-principal-name", default="")
    parser.add_argument("--data-volume-name", default="openclaw-bridge-e2e")
    parser.add_argument("--gateway-token", default="")
    parser.add_argument("--device-identity-file", default="")
    parser.add_argument("--approved-device-token", default="")
    args = parser.parse_args()

    platform = terraform_output(PLATFORM_DIR)
    suffix = platform["suffix"]
    sp_name = args.service_principal_name or f"openclaw-bridge-{suffix}"
    device_path = Path(args.device_identity_file) if args.device_identity_file else REPO_ROOT / ".local" / suffix / "openclaw-bridge-device.json"

    previous = existing_bridge_tfvars()
    gateway_token = args.gateway_token or previous.get("openclaw_gateway_token") or random_token()
    approved_device_token = args.approved_device_token or previous.get("openclaw_bridge_device_token", "")
    device = load_or_create_device(device_path)
    client_id, object_id, client_secret = ensure_service_principal(sp_name)

    tfvars = {
        "bridge_azure_client_id": client_id,
        "bridge_azure_client_object_id": object_id,
        "bridge_azure_client_secret": client_secret,
        "openclaw_gateway_token": gateway_token,
        "openclaw_bridge_device_private_key_pem": device["privateKeyPem"],
        "openclaw_bridge_device_token": approved_device_token,
        "openclaw_data_volume_name": args.data_volume_name,
    }
    write_tfvars(APPS_DIR / "generated.bridge.auto.tfvars.json", tfvars)
    print(
        json.dumps(
            {
                "servicePrincipalName": sp_name,
                "clientId": client_id,
                "clientObjectId": object_id,
                "dataVolumeName": args.data_volume_name,
                "deviceId": device["deviceId"],
                "deviceIdentityFile": str(device_path),
                "approvedDeviceTokenConfigured": bool(approved_device_token),
                "next": "Run terraform apply in terraform/apps. Then call /invoke and approve this deviceId in OpenClaw if requested.",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
