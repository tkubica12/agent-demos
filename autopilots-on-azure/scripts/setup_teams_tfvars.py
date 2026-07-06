from __future__ import annotations

import argparse
import json

from scripts.tf_helpers import APPS_DIR, PLATFORM_DIR, output, terraform_output, write_tfvars


def find_app_id(display_name: str) -> str:
    return output(
        [
            "az",
            "ad",
            "app",
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


def ensure_app_registration(display_name: str) -> str:
    existing_app_id = find_app_id(display_name)
    if existing_app_id:
        print(f"Reusing app registration {display_name}.", flush=True)
        return existing_app_id

    print(f"Creating app registration {display_name}.", flush=True)
    raw = output(
        [
            "az",
            "ad",
            "app",
            "create",
            "--display-name",
            display_name,
            "--sign-in-audience",
            "AzureADMyOrg",
            "-o",
            "json",
        ]
    )
    payload = json.loads(raw)
    return payload["appId"]


def reset_app_secret(app_id: str) -> str:
    raw = output(
        [
            "az",
            "ad",
            "app",
            "credential",
            "reset",
            "--id",
            app_id,
            "--display-name",
            "autopilot-teams",
            "--years",
            "1",
            "-o",
            "json",
        ]
    )
    payload = json.loads(raw)
    secret = payload.get("password")
    if not secret:
        raise RuntimeError("Azure CLI did not return an app registration secret.")
    return secret


def ensure_service_principal(app_id: str) -> None:
    existing_object_id = output(["az", "ad", "sp", "show", "--id", app_id, "--query", "id", "-o", "tsv"], check=False).strip()
    if existing_object_id:
        print(f"Reusing service principal for Teams bot app {app_id}.", flush=True)
        return

    print(f"Creating service principal for Teams bot app {app_id}.", flush=True)
    output(["az", "ad", "sp", "create", "--id", app_id, "-o", "json"])


def current_tenant_id() -> str:
    return output(["az", "account", "show", "--query", "tenantId", "-o", "tsv"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare Teams bot credentials and write apps generated Teams tfvars.")
    parser.add_argument("--app-name", default="")
    parser.add_argument("--tenant-id", default="")
    args = parser.parse_args()

    platform = terraform_output(PLATFORM_DIR)
    suffix = platform["suffix"]
    app_name = args.app_name or f"openclaw-autopilot-teams-{suffix}"
    tenant_id = args.tenant_id or current_tenant_id()

    app_id = ensure_app_registration(app_name)
    ensure_service_principal(app_id)
    secret = reset_app_secret(app_id)
    tfvars = {
        "teams_bot_app_id": app_id,
        "teams_bot_app_secret": secret,
        "teams_bot_tenant_id": tenant_id,
        "teams_bot_app_type": "SingleTenant",
    }
    write_tfvars(APPS_DIR / "generated.teams.auto.tfvars.json", tfvars)
    print(
        json.dumps(
            {
                "appRegistrationName": app_name,
                "teamsBotAppId": app_id,
                "teamsBotTenantId": tenant_id,
                "next": "Rebuild the bridge image, apply terraform/apps, then package and sideload the Teams app.",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
