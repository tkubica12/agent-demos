from __future__ import annotations

import argparse
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

from scripts.provision_agent365_instance import GraphClient, load_state, save_state
from scripts.setup_agent365 import load_json
from scripts.setup_app_tfvars import runtime_app_tfvars_path, runtime_outputs_path
from scripts.setup_identity import ensure_app_role_assignment, first_value
from scripts.tf_helpers import REPO_ROOT, write_tfvars


SCHEDULER_API_STATE = REPO_ROOT / ".local" / "scheduled-learning-api.json"
SCHEDULER_APP_ROLE = "ScheduledLearning.Run.All"


def application_by_display_name(
    graph: GraphClient,
    display_name: str,
) -> dict[str, Any] | None:
    encoded_filter = urllib.parse.quote(f"displayName eq '{display_name}'", safe="'")
    return first_value(
        graph.request(
            "GET",
            f"/applications?$filter={encoded_filter}&$select=id,appId,displayName,appRoles",
        )
    )


def ensure_scheduler_api(
    graph: GraphClient,
    state: dict[str, Any],
    *,
    display_name: str,
) -> dict[str, str]:
    app_object_id = str(state.get("applicationObjectId", ""))
    if not app_object_id:
        existing = application_by_display_name(graph, display_name)
        app_object_id = str(existing["id"]) if existing else ""
    if app_object_id:
        app = graph.request(
            "GET",
            f"/applications/{app_object_id}?$select=id,appId,displayName,appRoles",
        )
    else:
        app_role_id = str(uuid.uuid4())
        app = graph.request(
            "POST",
            "/applications",
            body={
                "displayName": display_name,
                "signInAudience": "AzureADMyOrg",
                "api": {"requestedAccessTokenVersion": 2},
                "appRoles": [
                    {
                        "allowedMemberTypes": ["Application"],
                        "description": "Run scheduled Dreaming and Learning Packet preparation.",
                        "displayName": "Run scheduled learning",
                        "id": app_role_id,
                        "isEnabled": True,
                        "value": SCHEDULER_APP_ROLE,
                    }
                ],
            },
        )
        if graph.dry_run and not app:
            app = {
                "id": "dry-run-scheduled-learning-app-object-id",
                "appId": "00000000-0000-0000-0000-000000000011",
                "appRoles": [{"id": app_role_id, "value": SCHEDULER_APP_ROLE}],
            }
        graph.request(
            "PATCH",
            f"/applications/{app['id']}",
            body={"identifierUris": [f"api://{app['appId']}"]},
            empty_ok=True,
        )

    app_id = str(app["appId"])
    service_principal_id = str(state.get("servicePrincipalObjectId", ""))
    if not service_principal_id:
        encoded_filter = urllib.parse.quote(f"appId eq '{app_id}'", safe="'")
        existing = first_value(
            graph.request(
                "GET",
                f"/servicePrincipals?$filter={encoded_filter}&$select=id,appId,displayName",
            )
        )
        service_principal = existing or graph.request(
            "POST",
            "/servicePrincipals",
            body={"appId": app_id},
        )
        if graph.dry_run and not service_principal:
            service_principal = {
                "id": "dry-run-scheduled-learning-service-principal-id"
            }
        service_principal_id = str(service_principal["id"])

    role = next(
        (
            item
            for item in app.get("appRoles", [])
            if item.get("value") == SCHEDULER_APP_ROLE
        ),
        None,
    )
    if role is None:
        refreshed = graph.request(
            "GET",
            f"/applications/{app['id']}?$select=appRoles",
        )
        role = next(
            item
            for item in refreshed.get("appRoles", [])
            if item.get("value") == SCHEDULER_APP_ROLE
        )
    return {
        "applicationObjectId": str(app["id"]),
        "applicationClientId": app_id,
        "servicePrincipalObjectId": service_principal_id,
        "audience": f"api://{app_id}",
        "appRoleId": str(role["id"]),
        "appRole": SCHEDULER_APP_ROLE,
    }


def configure_worker(
    graph: GraphClient,
    *,
    state_name: str,
    api_state: dict[str, str],
) -> None:
    outputs_path = runtime_outputs_path("hermes", state_name)
    tfvars_path = runtime_app_tfvars_path("hermes", state_name)
    outputs = load_json(outputs_path)
    tfvars = load_json(tfvars_path)
    client_id = str(outputs.get("bridge_identity_client_id") or "")
    principal_id = str(outputs.get("bridge_identity_principal_id") or "")
    if not client_id or not principal_id:
        raise KeyError(
            f"{outputs_path} requires bridge_identity_client_id and bridge_identity_principal_id."
        )
    ensure_app_role_assignment(
        graph,
        principal_id=principal_id,
        resource_id=api_state["servicePrincipalObjectId"],
        app_role_id=api_state["appRoleId"],
    )
    tfvars.update(
        {
            "scheduled_learning_audience": api_state["audience"],
            "scheduled_learning_allowed_client_ids": client_id,
            "scheduled_learning_allowed_object_ids": principal_id,
        }
    )
    write_tfvars(tfvars_path, tfvars)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Configure managed identity access for scheduled learning Jobs."
    )
    parser.add_argument(
        "--state-name",
        action="append",
        required=True,
        help="Hermes Worker local state name. Repeat for multiple Workers.",
    )
    parser.add_argument(
        "--api-state-file",
        type=Path,
        default=SCHEDULER_API_STATE,
    )
    parser.add_argument(
        "--display-name",
        default="Autopilots Scheduled Learning Bridge",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    graph = GraphClient.from_az_cli(dry_run=args.dry_run)
    api_state = ensure_scheduler_api(
        graph,
        load_state(args.api_state_file),
        display_name=args.display_name,
    )
    if not args.dry_run:
        save_state(args.api_state_file, api_state)
    for state_name in args.state_name:
        configure_worker(
            graph,
            state_name=state_name,
            api_state=api_state,
        )
    print(f"Scheduled learning audience: {api_state['audience']}")


if __name__ == "__main__":
    main()
