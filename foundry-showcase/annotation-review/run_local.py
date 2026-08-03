"""Resolve configuration from Azure and start the reviewer app locally.

Everything is discovered at run time with the signed-in Azure CLI identity, so no secret,
key, or connection string is ever stored in the repository.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

APP_DIR = Path(__file__).parent
DEFAULT_APP_NAME = "foundry-showcase-annotation-review"


def az(*args: str) -> str:
    result = subprocess.run(
        ["az", *args], capture_output=True, text=True, shell=(os.name == "nt")
    )
    if result.returncode != 0:
        raise SystemExit(f"az {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def resolve_app_insights(resource_group: str, name: str) -> tuple[str, str]:
    payload = json.loads(
        az(
            "resource",
            "show",
            "--resource-group",
            resource_group,
            "--name",
            name,
            "--resource-type",
            "Microsoft.Insights/components",
            "-o",
            "json",
        )
    )
    return payload["id"], payload["properties"]["ConnectionString"]


def resolve_client_id(display_name: str) -> str:
    payload = json.loads(
        az("ad", "app", "list", "--display-name", display_name, "-o", "json")
    )
    if not payload:
        raise SystemExit(
            f"No Entra application named '{display_name}'. "
            f"Run scripts\\setup_entra_app.py first."
        )
    return payload[0]["appId"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resource-group", default="rg-foundry-showcase-si4ons")
    parser.add_argument("--app-insights-name", default="appi-foundry-showcase-vz5kj8")
    parser.add_argument("--entra-app-name", default=DEFAULT_APP_NAME)
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    tenant_id = az("account", "show", "--query", "tenantId", "-o", "tsv")
    resource_id, connection_string = resolve_app_insights(
        args.resource_group, args.app_insights_name
    )
    client_id = resolve_client_id(args.entra_app_name)

    env = os.environ.copy()
    env["REVIEW_TENANT_ID"] = tenant_id
    env["REVIEW_CLIENT_ID"] = client_id
    env["REVIEW_APPINSIGHTS_RESOURCE_ID"] = resource_id
    env["REVIEW_APPINSIGHTS_CONNECTION_STRING"] = connection_string
    env["REVIEW_REDIRECT_URI"] = f"http://localhost:{args.port}/auth/callback"

    print(f"Application Insights : {args.app_insights_name}")
    print(f"Entra client         : {client_id}")
    print(f"Open                 : http://localhost:{args.port}")
    sys.stdout.flush()

    subprocess.run(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.port),
        ],
        cwd=APP_DIR,
        env=env,
        check=False,
    )


if __name__ == "__main__":
    main()
