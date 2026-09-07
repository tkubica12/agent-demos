from __future__ import annotations

import argparse
import base64
import json
import re
import secrets
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.tf_helpers import APPS_DIR, REPO_ROOT, write_tfvars


def random_token() -> str:
    return secrets.token_urlsafe(48)


def load_or_create_collective_approval_identity(path: Path) -> dict[str, str]:
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"privateKey", "publicKey"}:
            raise ValueError(f"{path} contains an invalid Collective Learning approval identity.")
        return payload
    private_key = Ed25519PrivateKey.generate()
    payload = {
        "privateKey": base64.b64encode(
            private_key.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
        ).decode("ascii"),
        "publicKey": base64.b64encode(
            private_key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        ).decode("ascii"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def load_agent365_auth(runtime: str, state_name: str = "") -> dict[str, str]:
    generated_path = REPO_ROOT / ".local" / (state_name or runtime) / "agent365" / "a365.generated.config.json"
    if not generated_path.exists():
        return {}
    generated = json.loads(generated_path.read_text(encoding="utf-8"))
    client_id = str(generated.get("agentBlueprintId", "")).strip()
    if not client_id:
        return {}
    return {"client_id": client_id}


def runtime_workspace(runtime: str, state_name: str = "") -> Path:
    return REPO_ROOT / ".local" / (state_name or runtime) / "apps"


def runtime_app_tfvars_path(runtime: str, state_name: str = "") -> Path:
    return runtime_workspace(runtime, state_name) / "generated.app.auto.tfvars.json"


def runtime_outputs_path(runtime: str, state_name: str = "") -> Path:
    return runtime_workspace(runtime, state_name) / "terraform-outputs.json"


def existing_app_tfvars(runtime: str, state_name: str = "") -> dict[str, Any]:
    path = runtime_app_tfvars_path(runtime, state_name)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def default_data_volume_name(runtime: str, autopilot_name: str = "") -> str:
    if autopilot_name and autopilot_name != "hermes":
        normalized = "".join(character if character.isalnum() else "-" for character in autopilot_name.lower()).strip("-")
        if not normalized:
            raise ValueError("autopilot_name must contain at least one letter or number.")
        return f"hermes-{normalized[:40]}-data"
    return "hermes-data"


def build_tfvars(
    *,
    runtime: str,
    autopilot_name: str,
    data_volume_name: str,
    previous: dict[str, Any],
    api_server_key: str = "",
    runtime_image: str = "",
    runtime_disk_source_image: str = "",
    runtime_disk_image_name: str = "",
    bridge_image: str = "",
    bridge_disk_source_image: str = "",
    private_mcp_image: str = "",
    private_mcp_disk_source_image: str = "",
    public_shipments_mcp_image: str = "",
    public_shipments_mcp_disk_source_image: str = "",
    agent365_client_id: str = "",
    agent365_tenant_id: str = "",
    role_blueprint: str = "",
    role_blueprint_source: str = "",
    role_blueprint_path: str = "",
    role_release: str = "",
    role_release_commit: str = "",
    assignment_scope: str = "",
    user_scheduling_enabled: bool | None = None,
    user_scheduling_max_concurrent_calls: int | None = None,
    user_scheduling_max_delivery_count: int | None = None,
    user_scheduling_lock_renewal_seconds: int | None = None,
    servicebus_dream_enabled: bool | None = None,
    servicebus_dream_cron_expression: str = "",
    scheduled_learning_enabled: bool | None = None,
    scheduled_learning_initial_delay_seconds: int | None = None,
    scheduled_learning_interval_seconds: int | None = None,
    scheduled_learning_focus: str = "",
    scheduled_learning_max_records: int | None = None,
    scheduled_learning_retry_limit: int | None = None,
    scheduled_learning_retry_backoff_seconds: int | None = None,
    scheduled_learning_prepare_packet: bool | None = None,
    collective_approval_private_key: str = "",
    collective_approval_public_key: str = "",
) -> dict[str, Any]:
    tfvars: dict[str, Any] = dict(previous)
    tfvars.update(
        {
            "autopilot_name": autopilot_name,
            "agent_runtime": runtime,
            "runtime_data_volume_name": data_volume_name,
            "user_scheduling_enabled": (
                user_scheduling_enabled
                if user_scheduling_enabled is not None
                else bool(previous.get("user_scheduling_enabled", False))
            ),
            "user_scheduling_max_concurrent_calls": (
                user_scheduling_max_concurrent_calls
                if user_scheduling_max_concurrent_calls is not None
                else int(previous.get("user_scheduling_max_concurrent_calls", 1))
            ),
            "user_scheduling_max_delivery_count": (
                user_scheduling_max_delivery_count
                if user_scheduling_max_delivery_count is not None
                else int(previous.get("user_scheduling_max_delivery_count", 5))
            ),
            "user_scheduling_lock_renewal_seconds": (
                user_scheduling_lock_renewal_seconds
                if user_scheduling_lock_renewal_seconds is not None
                else int(previous.get("user_scheduling_lock_renewal_seconds", 1_800))
            ),
            "servicebus_dream_enabled": (
                servicebus_dream_enabled
                if servicebus_dream_enabled is not None
                else bool(previous.get("servicebus_dream_enabled", False))
            ),
            "servicebus_dream_cron_expression": (
                servicebus_dream_cron_expression
                or previous.get(
                    "servicebus_dream_cron_expression",
                    "0 2 * * *",
                )
            ),
            "scheduled_learning_enabled": (
                scheduled_learning_enabled
                if scheduled_learning_enabled is not None
                else bool(previous.get("scheduled_learning_enabled", False))
            ),
            "scheduled_learning_initial_delay_seconds": (
                scheduled_learning_initial_delay_seconds
                if scheduled_learning_initial_delay_seconds is not None
                else int(previous.get("scheduled_learning_initial_delay_seconds", 60))
            ),
            "scheduled_learning_interval_seconds": (
                scheduled_learning_interval_seconds
                if scheduled_learning_interval_seconds is not None
                else int(previous.get("scheduled_learning_interval_seconds", 86_400))
            ),
            "scheduled_learning_focus": (
                scheduled_learning_focus
                or previous.get(
                    "scheduled_learning_focus",
                    "Review recent meaningful work for reusable, privacy-safe Role Skill improvements.",
                )
            ),
            "scheduled_learning_max_records": (
                scheduled_learning_max_records
                if scheduled_learning_max_records is not None
                else int(previous.get("scheduled_learning_max_records", 3))
            ),
            "scheduled_learning_retry_limit": (
                scheduled_learning_retry_limit
                if scheduled_learning_retry_limit is not None
                else int(previous.get("scheduled_learning_retry_limit", 3))
            ),
            "scheduled_learning_retry_backoff_seconds": (
                scheduled_learning_retry_backoff_seconds
                if scheduled_learning_retry_backoff_seconds is not None
                else int(previous.get("scheduled_learning_retry_backoff_seconds", 30))
            ),
            "scheduled_learning_prepare_packet": (
                scheduled_learning_prepare_packet
                if scheduled_learning_prepare_packet is not None
                else bool(previous.get("scheduled_learning_prepare_packet", True))
            ),
        }
    )
    existing_api_server_key = previous.get("api_server_key") or ""
    tfvars["api_server_key"] = api_server_key or existing_api_server_key or random_token()
    tfvars["previous_api_server_key"] = (
        existing_api_server_key
        if api_server_key and existing_api_server_key and api_server_key != existing_api_server_key
        else ""
    )
    tfvars["runtime_disk_image_name"] = runtime_disk_image_name or previous.get("runtime_disk_image_name", "hermes-api-server-image")
    tfvars["collective_learning_approval_private_key"] = (
        collective_approval_private_key or previous.get("collective_learning_approval_private_key", "")
    )
    tfvars["collective_learning_approval_public_key"] = (
        collective_approval_public_key or previous.get("collective_learning_approval_public_key", "")
    )
    role_values = {
        "hermes_role_blueprint": role_blueprint or previous.get("hermes_role_blueprint", ""),
        "hermes_role_blueprint_source": role_blueprint_source or previous.get("hermes_role_blueprint_source", ""),
        "hermes_role_blueprint_path": role_blueprint_path or previous.get("hermes_role_blueprint_path", ""),
        "hermes_role_release": role_release or previous.get("hermes_role_release", ""),
        "hermes_role_release_commit": role_release_commit or previous.get("hermes_role_release_commit", ""),
        "worker_assignment_scope": assignment_scope or previous.get("worker_assignment_scope", ""),
    }
    missing = [
        key for key in ("hermes_role_blueprint", "hermes_role_blueprint_source", "hermes_role_release", "hermes_role_release_commit")
        if not role_values[key]
    ]
    if missing:
        raise ValueError(f"Hermes Role Release configuration requires: {', '.join(missing)}.")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", role_values["hermes_role_release_commit"]):
        raise ValueError("hermes_role_release_commit must be a full 40-character Git commit SHA.")
    tfvars.update({key: value for key, value in role_values.items() if value})
    resolved_agent365_client_id = agent365_client_id or previous.get("agent365_client_id", "")
    resolved_agent365_tenant_id = agent365_tenant_id or previous.get("agent365_tenant_id", "")
    if resolved_agent365_client_id:
        tfvars["agent365_client_id"] = resolved_agent365_client_id
    if resolved_agent365_tenant_id:
        tfvars["agent365_tenant_id"] = resolved_agent365_tenant_id
    for prefix, image, source in (
        ("runtime", runtime_image, runtime_disk_source_image),
        ("bridge", bridge_image, bridge_disk_source_image),
        ("private_mcp", private_mcp_image, private_mcp_disk_source_image),
        ("public_shipments_mcp", public_shipments_mcp_image, public_shipments_mcp_disk_source_image),
    ):
        image_key, source_key = f"{prefix}_image", f"{prefix}_disk_source_image"
        old_image, old_source = previous.get(image_key, ""), previous.get(source_key, "")
        if image and image != old_image and old_source and not source:
            raise ValueError(f"A changed {image_key} requires its corresponding {source_key}; refusing a stale disk source.")
        if source and source != old_source and old_image and not image:
            raise ValueError(f"A changed {source_key} requires its corresponding {image_key}.")
        if image or old_image:
            tfvars[image_key] = image or old_image
        if source or old_source:
            tfvars[source_key] = source or old_source
    return tfvars


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare Hermes Worker bootstrap values and write apps generated tfvars.")
    parser.add_argument("--state-name", default="hermes", help="Worker state directory under .local (default: hermes).")
    parser.add_argument("--autopilot-name", default="")
    parser.add_argument("--data-volume-name", default="")
    parser.add_argument("--api-server-key", default="", help="Hermes API_SERVER_KEY. Generated when omitted.")
    parser.add_argument("--role-blueprint", default="", help="Hermes Role Blueprint name.")
    parser.add_argument("--role-blueprint-source", default="", help="Git repository URL containing the Role Blueprint.")
    parser.add_argument("--role-blueprint-path", default="", help="Role Blueprint path relative to the repository root.")
    parser.add_argument("--role-release", default="", help="Expected distribution.yaml Role Release.")
    parser.add_argument("--role-release-commit", default="", help="Full immutable Git commit SHA for the Role Release.")
    parser.add_argument("--assignment-scope", default="", help="Person, team, or workstream assigned to this Worker.")
    parser.add_argument(
        "--user-scheduling-enabled",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable Hermes cron schedules through the per-Worker Service Bus queue.",
    )
    parser.add_argument("--user-scheduling-max-concurrent-calls", type=int, default=None)
    parser.add_argument("--user-scheduling-max-delivery-count", type=int, default=None)
    parser.add_argument("--user-scheduling-lock-renewal-seconds", type=int, default=None)
    parser.add_argument(
        "--servicebus-dream-enabled",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Schedule platform Dreaming through the Worker Service Bus queue.",
    )
    parser.add_argument(
        "--servicebus-dream-cron-expression",
        default="",
    )
    parser.add_argument(
        "--scheduled-learning-enabled",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Keep the Hermes bridge active and run recurring Dreaming.",
    )
    parser.add_argument("--scheduled-learning-initial-delay-seconds", type=int, default=None)
    parser.add_argument("--scheduled-learning-interval-seconds", type=int, default=None)
    parser.add_argument("--scheduled-learning-focus", default="")
    parser.add_argument("--scheduled-learning-max-records", type=int, default=None)
    parser.add_argument("--scheduled-learning-retry-limit", type=int, default=None)
    parser.add_argument("--scheduled-learning-retry-backoff-seconds", type=int, default=None)
    parser.add_argument(
        "--scheduled-learning-prepare-packet",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Prepare a human-approval packet when Dreaming produces transferable records.",
    )
    parser.add_argument("--collective-approval-identity-file", default="", help="Local Ed25519 approval identity file.")
    parser.add_argument("--runtime-image", default="", help="Hermes runtime image digest.")
    parser.add_argument(
        "--runtime-disk-source-image",
        default="",
        help=(
            "Tagged OCI image used only to build the ACA Sandbox disk "
            "image. Runtime deployment remains pinned by --runtime-image."
        ),
    )
    parser.add_argument("--runtime-disk-image-name", default="", help="ACA Sandbox runtime disk image name.")
    parser.add_argument("--bridge-image", default="", help="Bridge image digest. Use to pin runtime deployments to a tested bridge build.")
    parser.add_argument("--bridge-disk-source-image", default="", help="Matching tagged bridge image for Sandbox disk conversion.")
    parser.add_argument("--private-mcp-image", default="", help="Private incidents MCP image digest.")
    parser.add_argument("--private-mcp-disk-source-image", default="", help="Matching tagged private MCP image for Sandbox disk conversion.")
    parser.add_argument("--public-shipments-mcp-image", default="", help="Public shipments MCP image digest.")
    parser.add_argument("--public-shipments-mcp-disk-source-image", default="", help="Matching tagged public MCP image for Sandbox disk conversion.")
    parser.add_argument("--agent365-client-id", default="", help="Agent 365 blueprint app ID for Microsoft Agents SDK auth.")
    parser.add_argument("--agent365-tenant-id", default="", help="Tenant ID for Microsoft Agents SDK auth. Defaults to tenant if omitted by Terraform.")
    parser.add_argument(
        "--agent365-from-generated",
        action="store_true",
        help="Load only the existing Agent 365 blueprint ID; workload authentication uses managed identity federation.",
    )
    parser.add_argument("--runtime-only", action="store_true", help="Only write .local/<state-name>/apps tfvars, not terraform/apps active tfvars.")
    args = parser.parse_args()
    runtime = "hermes"
    state_name = args.state_name
    autopilot_name = args.autopilot_name or state_name

    previous = existing_app_tfvars(runtime, state_name)
    agent365_auth = load_agent365_auth(runtime, state_name) if args.agent365_from_generated else {}
    data_volume_name = (
        args.data_volume_name
        or previous.get("runtime_data_volume_name")
        or default_data_volume_name(runtime, autopilot_name)
    )
    collective_identity_path = (
        Path(args.collective_approval_identity_file)
        if args.collective_approval_identity_file
        else runtime_workspace(runtime, state_name) / "collective-learning-approval.json"
    )
    collective_identity = load_or_create_collective_approval_identity(collective_identity_path)

    tfvars = build_tfvars(
        runtime=runtime,
        autopilot_name=autopilot_name,
        data_volume_name=data_volume_name,
        previous=previous,
        api_server_key=args.api_server_key,
        runtime_image=args.runtime_image,
        runtime_disk_source_image=args.runtime_disk_source_image,
        runtime_disk_image_name=args.runtime_disk_image_name,
        bridge_image=args.bridge_image,
        bridge_disk_source_image=args.bridge_disk_source_image,
        private_mcp_image=args.private_mcp_image,
        private_mcp_disk_source_image=args.private_mcp_disk_source_image,
        public_shipments_mcp_image=args.public_shipments_mcp_image,
        public_shipments_mcp_disk_source_image=args.public_shipments_mcp_disk_source_image,
        agent365_client_id=args.agent365_client_id or agent365_auth.get("client_id", ""),
        agent365_tenant_id=args.agent365_tenant_id,
        role_blueprint=args.role_blueprint,
        role_blueprint_source=args.role_blueprint_source,
        role_blueprint_path=args.role_blueprint_path,
        role_release=args.role_release,
        role_release_commit=args.role_release_commit,
        assignment_scope=args.assignment_scope,
        user_scheduling_enabled=args.user_scheduling_enabled,
        user_scheduling_max_concurrent_calls=args.user_scheduling_max_concurrent_calls,
        user_scheduling_max_delivery_count=args.user_scheduling_max_delivery_count,
        user_scheduling_lock_renewal_seconds=args.user_scheduling_lock_renewal_seconds,
        servicebus_dream_enabled=args.servicebus_dream_enabled,
        servicebus_dream_cron_expression=args.servicebus_dream_cron_expression,
        scheduled_learning_enabled=args.scheduled_learning_enabled,
        scheduled_learning_initial_delay_seconds=args.scheduled_learning_initial_delay_seconds,
        scheduled_learning_interval_seconds=args.scheduled_learning_interval_seconds,
        scheduled_learning_focus=args.scheduled_learning_focus,
        scheduled_learning_max_records=args.scheduled_learning_max_records,
        scheduled_learning_retry_limit=args.scheduled_learning_retry_limit,
        scheduled_learning_retry_backoff_seconds=args.scheduled_learning_retry_backoff_seconds,
        scheduled_learning_prepare_packet=args.scheduled_learning_prepare_packet,
        collective_approval_private_key=collective_identity.get("privateKey", ""),
        collective_approval_public_key=collective_identity.get("publicKey", ""),
    )
    runtime_path = runtime_app_tfvars_path(runtime, state_name)
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
                "stateName": state_name,
                "workerId": autopilot_name,
                "dataVolumeName": data_volume_name,
                "runtimeTfvarsFile": str(runtime_path),
                "activeTfvarsFile": "" if args.runtime_only else str(active_path),
                "activePrecedenceTfvarsFile": "" if args.runtime_only else str(precedence_path),
                "collectiveApprovalIdentityFile": str(collective_identity_path) if collective_identity_path else "",
                "apiServerKeyConfigured": bool(tfvars.get("api_server_key")),
                "next": "Run terraform apply in terraform/apps. The bridge uses a managed identity for Azure API calls.",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
