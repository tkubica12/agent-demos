"""Create or converge the Entra application used by the reviewer app.

The application is a public client that uses authorization code with PKCE, so it holds no
client secret. Reviewers sign in with their own account and the app calls Azure Monitor with
their delegated token, which keeps Azure RBAC in force per business user.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

LOG_ANALYTICS_APP_ID = "ca7f3f0b-7d91-482c-8e09-c5d840d0eac5"
LOG_ANALYTICS_SCOPE = "Data.Read"
AZURE_MONITOR_APP_ID = "e933bd07-d2ee-4f1d-933c-3752b819567b"
AZURE_MONITOR_SCOPE = "AMA.Ingest"

READER_ROLE = "Log Analytics Reader"
PUBLISHER_ROLE = "Monitoring Metrics Publisher"


def az(*args: str, allow_failure: bool = False) -> str:
    result = subprocess.run(
        ["az", *args], capture_output=True, text=True, shell=(os.name == "nt")
    )
    if result.returncode != 0:
        if allow_failure:
            return ""
        raise SystemExit(f"az {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def scope_id(resource_app_id: str, value: str) -> str:
    found = az(
        "ad",
        "sp",
        "show",
        "--id",
        resource_app_id,
        "--query",
        f"oauth2PermissionScopes[?value=='{value}'].id | [0]",
        "-o",
        "tsv",
    )
    if not found:
        raise SystemExit(
            f"Could not resolve delegated scope {value} on {resource_app_id}."
        )
    return found


def ensure_application(display_name: str, redirect_uri: str) -> tuple[str, str]:
    existing = json.loads(
        az("ad", "app", "list", "--display-name", display_name, "-o", "json") or "[]"
    )
    if existing:
        app = existing[0]
        az(
            "ad",
            "app",
            "update",
            "--id",
            app["appId"],
            "--is-fallback-public-client",
            "true",
            "--public-client-redirect-uris",
            redirect_uri,
        )
        return app["appId"], app["id"]

    created = json.loads(
        az(
            "ad",
            "app",
            "create",
            "--display-name",
            display_name,
            "--is-fallback-public-client",
            "true",
            "--public-client-redirect-uris",
            redirect_uri,
            "-o",
            "json",
        )
    )
    az("ad", "sp", "create", "--id", created["appId"], allow_failure=True)
    return created["appId"], created["id"]


def set_permissions(object_id: str) -> None:
    body = {
        "requiredResourceAccess": [
            {
                "resourceAppId": LOG_ANALYTICS_APP_ID,
                "resourceAccess": [
                    {
                        "id": scope_id(LOG_ANALYTICS_APP_ID, LOG_ANALYTICS_SCOPE),
                        "type": "Scope",
                    }
                ],
            },
            {
                "resourceAppId": AZURE_MONITOR_APP_ID,
                "resourceAccess": [
                    {
                        "id": scope_id(AZURE_MONITOR_APP_ID, AZURE_MONITOR_SCOPE),
                        "type": "Scope",
                    }
                ],
            },
        ]
    }
    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as handle:
        json.dump(body, handle)
        path = handle.name
    try:
        az(
            "rest",
            "--method",
            "PATCH",
            "--uri",
            f"https://graph.microsoft.com/v1.0/applications/{object_id}",
            "--headers",
            "Content-Type=application/json",
            "--body",
            f"@{path}",
        )
    finally:
        Path(path).unlink(missing_ok=True)


def grant_rbac(resource_id: str, principal_ids: list[str]) -> None:
    for principal_id in principal_ids:
        for role in (READER_ROLE, PUBLISHER_ROLE):
            az(
                "role",
                "assignment",
                "create",
                "--assignee-object-id",
                principal_id,
                "--assignee-principal-type",
                "User",
                "--role",
                role,
                "--scope",
                resource_id,
                allow_failure=True,
            )
            print(f"  {role} -> {principal_id}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--display-name", default="foundry-showcase-annotation-review")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--resource-group", default="rg-foundry-showcase-si4ons")
    parser.add_argument("--app-insights-name", default="appi-foundry-showcase-vz5kj8")
    parser.add_argument(
        "--reviewer",
        action="append",
        default=[],
        help="UPN of a reviewer to grant reader and publisher roles. Repeatable.",
    )
    args = parser.parse_args()

    redirect_uri = f"http://localhost:{args.port}/auth/callback"
    client_id, object_id = ensure_application(args.display_name, redirect_uri)
    set_permissions(object_id)
    print(f"Entra application : {client_id}")
    print(f"Redirect URI      : {redirect_uri}")

    resource_id = az(
        "resource",
        "show",
        "--resource-group",
        args.resource_group,
        "--name",
        args.app_insights_name,
        "--resource-type",
        "Microsoft.Insights/components",
        "--query",
        "id",
        "-o",
        "tsv",
    )

    reviewers = args.reviewer or [
        az("ad", "signed-in-user", "show", "--query", "userPrincipalName", "-o", "tsv")
    ]
    principal_ids = [
        az("ad", "user", "show", "--id", upn, "--query", "id", "-o", "tsv")
        for upn in reviewers
    ]
    print("Granting Azure RBAC on Application Insights:")
    grant_rbac(resource_id, principal_ids)

    print(
        "\nEach reviewer consents to the delegated permissions at first sign-in. "
        "If your tenant blocks user consent, an Entra administrator must run:\n"
        f"  az ad app permission admin-consent --id {client_id}"
    )


if __name__ == "__main__":
    main()
