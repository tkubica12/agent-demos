"""Create or remove a complete Hermes Worker.

``bootstrap`` creates the Agent 365 blueprint and grants first, deploys the Worker,
and registers the real messaging endpoint last. No blueprint client secret is kept.

``teardown`` previews by default. With ``--execute`` it deletes the Worker's Sandboxes,
volumes and disk images, destroys its Terraform workspace, removes its Agent User,
Agent Identity and blueprint, and deletes its local state.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Iterator

from scripts.deploy_apps_runtime import activate_runtime_tfvars, terraform_workspace_name
from scripts.provision_agent365_instance import GraphClient, GraphError, state_file
from scripts.setup_agent365 import (
    GENERATED_CONFIG,
    TOOLING_MANIFEST,
    agent365_config_payload,
    agent365_workspace,
    load_json,
    messaging_endpoint_from_outputs,
    write_json,
)
from scripts.setup_app_tfvars import runtime_app_tfvars_path, runtime_outputs_path
from scripts.tf_helpers import APPS_DIR, REPO_ROOT, output, resolve_executable, run


RUNTIME = "hermes"
DEFAULT_LICENSE_SKUS = (
    "AGENT_365",
    "Microsoft_Teams_Enterprise_New",
    "Microsoft_365_E3_(no_Teams)",
    "FLOW_FREE",
)
CONFIRMATIONS = "y\n" * 12
BLUEPRINT_SECRET_KEYS = ("agentBlueprintClientSecret",)
STATE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,22}[a-z0-9]$")
SECRET_PATTERN = re.compile(
    r"[A-Za-z0-9_.\-]{3,}~[A-Za-z0-9~_.\-]{20,}|eyJ[\w-]+\.[\w-]+\.[\w-]+"
)
RELEASE_PIN_FLAGS = {
    "hermes_role_blueprint": "--role-blueprint",
    "hermes_role_blueprint_source": "--role-blueprint-source",
    "hermes_role_blueprint_path": "--role-blueprint-path",
    "hermes_role_release": "--role-release",
    "hermes_role_release_commit": "--role-release-commit",
    "runtime_image": "--runtime-image",
    "runtime_disk_source_image": "--runtime-disk-source-image",
    "runtime_disk_image_name": "--runtime-disk-image-name",
    "bridge_image": "--bridge-image",
    "bridge_disk_source_image": "--bridge-disk-source-image",
    "private_mcp_image": "--private-mcp-image",
    "private_mcp_disk_source_image": "--private-mcp-disk-source-image",
    "public_shipments_mcp_image": "--public-shipments-mcp-image",
    "public_shipments_mcp_disk_source_image": "--public-shipments-mcp-disk-source-image",
}


def validate_state_name(value: str) -> str:
    if not STATE_NAME_PATTERN.fullmatch(value):
        raise ValueError(
            "Worker state name must be 3-24 lowercase letters, digits, or hyphens "
            "and start with a letter."
        )
    return value


def worker_display_name(state_name: str) -> str:
    return state_name[:1].upper() + state_name[1:]


def bootstrap_agent365_config(
    *,
    state_name: str,
    agent_name: str,
    tenant_id: str,
    sponsor_upn: str,
    agent_upn: str,
    client_app_id: str,
) -> dict[str, Any]:
    payload = agent365_config_payload(
        autopilot_name=state_name,
        runtime_kind=RUNTIME,
        agent_name=agent_name,
        tenant_id=tenant_id,
        messaging_endpoint="",
        ai_teammate=True,
        manager_email=sponsor_upn,
        agent_user_principal_name=agent_upn,
    )
    payload["clientAppId"] = client_app_id
    return payload


def release_pin_arguments(tfvars: dict[str, Any]) -> list[str]:
    missing = [key for key in RELEASE_PIN_FLAGS if not str(tfvars.get(key) or "").strip()]
    if missing:
        raise ValueError("Release pin source is missing: " + ", ".join(missing))
    arguments: list[str] = []
    for key, flag in RELEASE_PIN_FLAGS.items():
        arguments.extend([flag, str(tfvars[key])])
    return arguments


def inheritance_complete(text: str) -> bool:
    match = re.search(r"Summary:\s*(\d+)\s+of\s+(\d+)\s+resource", text)
    return bool(match) and match.group(1) == match.group(2) and int(match.group(2)) > 0


def endpoint_registration_needed(registered: str, endpoint: str) -> bool:
    current = registered.strip().rstrip("/")
    if not current:
        return True
    if current == endpoint:
        return False
    raise RuntimeError(
        f"The blueprint already has a different messaging endpoint ({current}). "
        "Bootstrap only registers a fresh endpoint; use the documented owner-only "
        "endpoint update for an existing Worker."
    )


def strip_blueprint_secrets(generated: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in generated.items() if key not in BLUEPRINT_SECRET_KEYS}


def redact_secrets(text: str) -> str:
    return SECRET_PATTERN.sub("<redacted>", text)


def owned_sandbox_groups(outputs: dict[str, Any], state_name: str) -> dict[str, str]:
    groups = {
        role: str(group.get("name") or "")
        for role, group in (outputs.get("sandbox_groups") or {}).items()
    }
    foreign = [name for name in groups.values() if f"-{state_name}-" not in name]
    if foreign:
        raise ValueError(
            f"Refusing Sandbox Groups that are not owned by Worker {state_name}: "
            + ", ".join(foreign)
        )
    return groups


def step(number: int | str, message: str) -> None:
    print(f"\n[{time.strftime('%H:%M:%S')}] STEP {number}: {message}", flush=True)


@contextlib.contextmanager
def azure_identity(config_dir: str) -> Iterator[None]:
    """Run Azure CLI calls with another az login cache, then restore the operator."""
    if not config_dir:
        yield
        return
    previous = os.environ.get("AZURE_CONFIG_DIR")
    os.environ["AZURE_CONFIG_DIR"] = config_dir
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("AZURE_CONFIG_DIR", None)
        else:
            os.environ["AZURE_CONFIG_DIR"] = previous


def stream(command: list[str], *, cwd: Path, stdin_text: str | None = None) -> str:
    """Run a command, print its output with client secrets and tokens redacted, and return it."""
    print("+ " + " ".join(command), flush=True)
    process = subprocess.Popen(
        [resolve_executable(command[0]), *command[1:]],
        cwd=str(cwd),
        stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if stdin_text is not None and process.stdin is not None:
        process.stdin.write(stdin_text)
        process.stdin.close()
    lines: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        clean = redact_secrets(line)
        lines.append(clean)
        print(clean, end="", flush=True)
    code = process.wait()
    if code != 0:
        raise subprocess.CalledProcessError(code, command)
    return "".join(lines)


def run_module(module: str, *arguments: str, stdin_text: str | None = None) -> str:
    return stream([sys.executable, "-m", module, *arguments], cwd=REPO_ROOT, stdin_text=stdin_text)


def graph_client() -> GraphClient:
    return GraphClient.from_az_cli()


def application_object(graph: GraphClient, app_id: str) -> dict[str, Any] | None:
    try:
        return graph.request(
            "GET", f"/applications(appId='{app_id}')?$select=id,passwordCredentials"
        )
    except GraphError as exc:
        if exc.status == 404:
            return None
        raise


def initial_domain(graph: GraphClient) -> str:
    organization = graph.request("GET", "/organization?$select=verifiedDomains")["value"][0]
    for domain in organization["verifiedDomains"]:
        if domain.get("isInitial"):
            return str(domain["name"])
    raise RuntimeError("The tenant has no initial domain.")


def agent365_cli_app_id(graph: GraphClient) -> str:
    query = urllib.parse.quote("displayName eq 'Agent 365 CLI'")
    apps = graph.request("GET", f"/applications?$filter={query}&$select=appId")["value"]
    if len(apps) != 1:
        raise RuntimeError(
            "Expected exactly one 'Agent 365 CLI' application in the tenant; pass --client-app-id."
        )
    return str(apps[0]["appId"])


def remove_blueprint_secrets(blueprint_id: str, generated_path: Path) -> None:
    graph = graph_client()
    app = application_object(graph, blueprint_id)
    if app is None:
        raise RuntimeError(f"Blueprint application {blueprint_id} was not found.")
    for credential in app.get("passwordCredentials") or []:
        graph.request(
            "POST",
            f"/applications/{app['id']}/removePassword",
            body={"keyId": credential["keyId"]},
            empty_ok=True,
        )
        print(f"Removed blueprint password credential {credential['keyId']}.", flush=True)
    write_json(generated_path, strip_blueprint_secrets(load_json(generated_path)))
    require_no_blueprint_secrets(blueprint_id)


def require_no_blueprint_secrets(blueprint_id: str, *, attempts: int = 12, delay_seconds: int = 10) -> None:
    graph = graph_client()
    remaining = -1
    for attempt in range(attempts):
        app = application_object(graph, blueprint_id) or {}
        remaining = len(app.get("passwordCredentials") or [])
        if remaining == 0:
            print("Blueprint password credentials: 0", flush=True)
            return
        if attempt < attempts - 1:
            time.sleep(delay_seconds)
    raise RuntimeError(f"Blueprint {blueprint_id} still has {remaining} password credentials.")


def bootstrap(args: argparse.Namespace) -> None:
    state_name = validate_state_name(args.state_name)
    if args.pins_from == state_name:
        raise ValueError("--pins-from must name another, already deployed Worker.")
    pins = release_pin_arguments(load_json(runtime_app_tfvars_path(RUNTIME, args.pins_from)))
    agent_name = args.display_name or worker_display_name(state_name)
    workspace = agent365_workspace(state_name)
    workspace.mkdir(parents=True, exist_ok=True)
    generated_path = workspace / GENERATED_CONFIG
    admin = args.admin_config_dir

    with azure_identity(admin):
        graph = graph_client()
        tenant_id = args.tenant_id or output(["az", "account", "show", "--query", "tenantId", "-o", "tsv"])
        sponsor_upn = args.sponsor_upn or str(
            graph.request("GET", "/me?$select=userPrincipalName")["userPrincipalName"]
        )
        agent_upn = f"{state_name}@{args.upn_domain or initial_domain(graph)}"
        client_app_id = args.client_app_id or agent365_cli_app_id(graph)

    step(1, "Agent 365 configuration without a messaging endpoint")
    config_path = workspace / "a365.config.json"
    if config_path.exists():
        if load_json(config_path).get("tenantId") != tenant_id:
            raise ValueError(f"{config_path} belongs to another tenant.")
        print(f"Keeping {config_path}", flush=True)
    else:
        write_json(
            config_path,
            bootstrap_agent365_config(
                state_name=state_name,
                agent_name=agent_name,
                tenant_id=tenant_id,
                sponsor_upn=sponsor_upn,
                agent_upn=agent_upn,
                client_app_id=client_app_id,
            ),
        )
    shutil.copyfile(TOOLING_MANIFEST, workspace / TOOLING_MANIFEST.name)

    with azure_identity(admin):
        step(2, "Blueprint, inheritable permissions, and admin consent")
        blueprint_id = str(load_json(generated_path).get("agentBlueprintId") or "") if generated_path.exists() else ""
        if blueprint_id and application_object(graph_client(), blueprint_id) is not None:
            print(f"Blueprint {blueprint_id} exists; skipping a365 setup all.", flush=True)
        else:
            stream(["a365", "setup", "all", "--aiteammate", "--m365"], cwd=workspace, stdin_text=CONFIRMATIONS)
            blueprint_id = str(load_json(generated_path)["agentBlueprintId"])
        if not inheritance_complete(stream(["a365", "query-entra", "inheritance"], cwd=workspace)):
            raise RuntimeError("Effective inheritable permissions are incomplete.")

        step(3, "Remove CLI-created blueprint client secrets")
        remove_blueprint_secrets(blueprint_id, generated_path)

        step(4, "Agent Identity, Agent User, and licenses")
        run_module(
            "scripts.provision_agent365_instance", "provision",
            "--state-name", state_name,
            "--agent-upn", agent_upn,
            "--display-name", agent_name,
            "--mail-nickname", state_name,
            "--owner-upn", sponsor_upn,
            "--license-skus", args.license_skus,
            "--usage-location", args.usage_location,
        )

    step(5, f"Worker settings with release pins from {args.pins_from}")
    if runtime_app_tfvars_path(RUNTIME, state_name).exists():
        print("Keeping existing Worker settings.", flush=True)
    else:
        run_module(
            "scripts.setup_app_tfvars",
            "--state-name", state_name,
            "--autopilot-name", state_name,
            "--assignment-scope", args.assignment_scope or f"{state_name}-bootstrap",
            "--no-user-scheduling-enabled",
            "--no-servicebus-dream-enabled",
            "--no-scheduled-learning-enabled",
            *pins,
            "--runtime-only",
        )

    step(6, "Azure infrastructure without services")
    run_module(
        "scripts.deploy_apps_runtime", "--state-name", state_name,
        "--apply", "--auto-approve", "--infrastructure-only", "--capture",
    )

    with azure_identity(admin):
        step(7, "Federation, MCP API roles, and Work IQ permissions")
        run_module("scripts.setup_identity", "--state-name", state_name, stdin_text=CONFIRMATIONS)

    step(8, "Apply reconciled settings and deploy Sandbox services")
    run_module(
        "scripts.deploy_apps_runtime", "--state-name", state_name,
        "--apply", "--auto-approve", "--deploy-services", "--capture",
    )
    run_module("scripts.demo_ops", "status", "--state-name", state_name, "--invoke")

    with azure_identity(admin):
        step(9, "Register the deployed messaging endpoint")
        endpoint = messaging_endpoint_from_outputs(runtime_outputs_path(RUNTIME, state_name))
        registered = str(load_json(generated_path).get("messagingEndpoint") or "")
        if endpoint_registration_needed(registered, endpoint):
            stream(
                ["a365", "setup", "blueprint", "--endpoint-only", "--messaging-endpoint", endpoint],
                cwd=workspace,
            )
        else:
            print("Endpoint is already registered.", flush=True)
        write_json(generated_path, strip_blueprint_secrets(load_json(generated_path)))
        require_no_blueprint_secrets(blueprint_id)

        step(10, "Agent User package")
        run_module(
            "scripts.setup_agent365", "--state-name", state_name,
            "--agent-name", agent_name, "--publish", "--capture",
        )

    print(
        "\nBootstrap complete. Upload "
        f"{workspace / 'manifest' / 'manifest.zip'} in Microsoft 365 admin center -> "
        "Agents -> All agents -> Upload custom agent.",
        flush=True,
    )


def sandbox_inventory(outputs: dict[str, Any], groups: dict[str, str]) -> dict[str, Any]:
    from azure.containerapps.sandbox import SandboxGroupClient
    from azure.identity import DefaultAzureCredential

    from scripts.demo_ops import sandbox_items
    from scripts.sandbox_runtime import endpoint_for_region

    credential = DefaultAzureCredential()
    inventory: dict[str, Any] = {}
    for role, name in groups.items():
        client = SandboxGroupClient(
            endpoint_for_region(str(outputs["sandbox_location"])),
            credential,
            subscription_id=str(outputs["subscription_id"]),
            resource_group=str(outputs["resource_group_name"]),
            sandbox_group=name,
        )
        inventory[role] = {
            "client": client,
            "group": name,
            "sandboxes": [
                str(item.get("id") or "")
                for item in sandbox_items(client._dp_get(f"{client._group_path}/sandboxes"))
            ],
            "volumes": [str(getattr(volume, "name", "")) for volume in client.list_volumes()],
            "diskImages": [
                str(getattr(image, "name", "") or getattr(image, "id", ""))
                for image in client.list_disk_images()
            ],
        }
    return inventory


def terraform_workspace_exists(workspace: str) -> bool:
    listed = run(["terraform", "workspace", "list"], cwd=APPS_DIR, capture=True).stdout
    return workspace in {line.strip(" *") for line in listed.splitlines()}


def teardown(args: argparse.Namespace) -> None:
    state_name = validate_state_name(args.state_name)
    if state_name == args.restore_state:
        raise ValueError("--restore-state must name another Worker.")
    if not runtime_app_tfvars_path(RUNTIME, args.restore_state).exists():
        raise FileNotFoundError(f"Restore Worker {args.restore_state} has no local settings.")
    local_dir = REPO_ROOT / ".local" / state_name
    workspace_dir = agent365_workspace(state_name)
    outputs_path = runtime_outputs_path(RUNTIME, state_name)
    outputs = load_json(outputs_path) if outputs_path.exists() else {}
    groups = owned_sandbox_groups(outputs, state_name)
    tf_workspace = terraform_workspace_name(state_name)
    has_tf_workspace = terraform_workspace_exists(tf_workspace)
    instance_path = state_file(state_name, state_name)
    instance = load_json(instance_path) if instance_path.exists() else {}
    generated_path = workspace_dir / GENERATED_CONFIG
    blueprint_id = str(load_json(generated_path).get("agentBlueprintId") or "") if generated_path.exists() else ""
    inventory = sandbox_inventory(outputs, groups) if groups else {}

    summary = {
        "worker": state_name,
        "sandboxGroups": {
            role: {key: value for key, value in item.items() if key != "client"}
            for role, item in inventory.items()
        },
        "terraformWorkspace": tf_workspace if has_tf_workspace else None,
        "agentUserId": instance.get("agentUserId"),
        "agentIdentityId": instance.get("agentIdentityId"),
        "blueprintId": blueprint_id or None,
        "localState": str(local_dir) if local_dir.exists() else None,
        "restoreWorker": args.restore_state,
        "execute": args.execute,
    }
    print(json.dumps(summary, indent=2), flush=True)
    if not args.execute:
        print("\nPreview only. Rerun with --execute to delete exactly these resources.", flush=True)
        return

    step(1, "Delete Sandboxes, volumes, and disk images")
    for item in inventory.values():
        client = item["client"]
        for sandbox_id in item["sandboxes"]:
            client.begin_delete_sandbox(sandbox_id, polling_timeout=600).result()
        for volume in item["volumes"]:
            client.begin_delete_volume(volume, polling_timeout=600).result()
        for image in item["diskImages"]:
            client.begin_delete_disk_image(image, polling_timeout=600).result()
        print(f"Emptied {item['group']}.", flush=True)

    step(2, f"Destroy Terraform workspace {tf_workspace}")
    if has_tf_workspace:
        activate_runtime_tfvars(RUNTIME, state_name)
        run(["terraform", "workspace", "select", tf_workspace], cwd=APPS_DIR)
        run(["terraform", "destroy", "-auto-approve", "-input=false"], cwd=APPS_DIR)
    else:
        print("No Terraform workspace.", flush=True)

    with azure_identity(args.admin_config_dir):
        step(3, "Delete Agent User and Agent Identity")
        if instance:
            run_module(
                "scripts.provision_agent365_instance", "cleanup",
                "--state-name", state_name, "--mail-nickname", state_name, "--remove-state",
            )
        else:
            print("No Agent 365 instance state.", flush=True)

        step(4, "Delete blueprint")
        if blueprint_id and application_object(graph_client(), blueprint_id) is not None:
            stream(["a365", "cleanup", "blueprint", "--yes"], cwd=workspace_dir)
        else:
            print("No live blueprint.", flush=True)

    step(5, f"Restore active Worker {args.restore_state} and remove local state")
    activate_runtime_tfvars(RUNTIME, args.restore_state)
    run(["terraform", "workspace", "select", terraform_workspace_name(args.restore_state)], cwd=APPS_DIR)
    if has_tf_workspace:
        run(["terraform", "workspace", "delete", tf_workspace], cwd=APPS_DIR)
    if local_dir.exists():
        shutil.rmtree(local_dir)
        print(f"Removed {local_dir}", flush=True)
    print(f"\nTeardown of {state_name} complete.", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)
    admin_help = (
        "Azure CLI config directory of a tenant administrator for Agent 365 and Graph steps. "
        "Terraform keeps the current login. Defaults to the current login."
    )

    create = subparsers.add_parser("bootstrap", help="Create a fresh Worker end to end.")
    create.add_argument("--state-name", required=True, help="New Worker name; also its mail nickname.")
    create.add_argument("--pins-from", default="hermes2", help="Deployed Worker whose release and image pins to copy.")
    create.add_argument("--display-name", default="", help="Agent display name. Defaults to the capitalized state name.")
    create.add_argument("--sponsor-upn", default="", help="Sponsor and manager. Defaults to the signed-in admin.")
    create.add_argument("--upn-domain", default="", help="Agent User domain. Defaults to the tenant initial domain.")
    create.add_argument("--client-app-id", default="", help="Agent 365 CLI app ID. Defaults to a tenant lookup.")
    create.add_argument("--tenant-id", default="")
    create.add_argument("--license-skus", default=",".join(DEFAULT_LICENSE_SKUS))
    create.add_argument("--usage-location", default="CZ")
    create.add_argument("--assignment-scope", default="")
    create.add_argument("--admin-config-dir", default="", help=admin_help)
    create.set_defaults(func=bootstrap)

    remove = subparsers.add_parser("teardown", help="Preview or delete one Worker and its identities.")
    remove.add_argument("--state-name", required=True)
    remove.add_argument("--restore-state", default="hermes2", help="Worker to make active again afterwards.")
    remove.add_argument("--execute", action="store_true", help="Delete instead of previewing.")
    remove.add_argument("--admin-config-dir", default="", help=admin_help)
    remove.set_defaults(func=teardown)

    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
