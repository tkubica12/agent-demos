from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.setup_agent365 import agent365_workspace, load_json
from scripts.setup_app_tfvars import runtime_outputs_path
from scripts.tf_helpers import REPO_ROOT, resolve_executable


SNAPSHOT_ROOT = REPO_ROOT / ".local" / "snapshots"
GRAPH_BASE = "https://graph.microsoft.com"
SECRET_KEY_PARTS = ("secret", "password", "token", "credential", "privatekey", "private_key", "apikey", "api_key")


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")


def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in value)


def redact(value: Any, key: str = "") -> Any:
    key_lower = key.lower()
    if any(part in key_lower for part in SECRET_KEY_PARTS):
        if value in (None, "", [], {}):
            return value
        return "<redacted>"
    if isinstance(value, dict):
        return {item_key: redact(item_value, item_key) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(redact(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {path}", flush=True)


def run_json(command: list[str], *, cwd: Path | None = None, check: bool = False) -> dict[str, Any] | list[Any] | str:
    resolved = [resolve_executable(command[0]), *command[1:]]
    result = subprocess.run(resolved, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    if result.returncode != 0:
        if check:
            raise subprocess.CalledProcessError(result.returncode, resolved, result.stdout, result.stderr)
        return {
            "command": command,
            "returnCode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    output = result.stdout.strip()
    if not output:
        return {}
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return output


def az_rest(url: str, *, method: str = "GET") -> Any:
    return run_json(["az", "rest", "--method", method, "--url", url, "-o", "json"])


def graph_url(path: str, *, beta: bool = False) -> str:
    version = "beta" if beta else "v1.0"
    return f"{GRAPH_BASE}/{version}{path}"


def app_only_graph_get(path: str, auth_file: Path, *, beta: bool = False) -> Any:
    if not auth_file.exists():
        return {"skipped": f"{auth_file} does not exist."}
    auth = load_json(auth_file)
    form = urllib.parse.urlencode(
        {
            "client_id": auth["clientId"],
            "client_secret": auth["clientSecret"],
            "grant_type": "client_credentials",
            "scope": f"{GRAPH_BASE}/.default",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"https://login.microsoftonline.com/{auth['tenantId']}/oauth2/v2.0/token",
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            token = json.loads(response.read().decode("utf-8"))["access_token"]
    except Exception as exc:
        return {"error": exc.__class__.__name__, "message": str(exc)}

    url = graph_url(path, beta=beta)
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "body": exc.read().decode("utf-8", errors="replace")}
    return json.loads(raw) if raw.strip() else {}


def read_local_json(path: Path) -> Any:
    if not path.exists():
        return {"missing": str(path)}
    return load_json(path)


def capture_local_state(root: Path, runtimes: list[str]) -> None:
    for runtime in runtimes:
        runtime_dir = root / "local" / runtime
        write_json(runtime_dir / "apps.terraform-outputs.json", read_local_json(runtime_outputs_path(runtime)))
        workspace = agent365_workspace(runtime)
        for name in (
            "a365.config.json",
            "a365.generated.config.json",
            f"{runtime}-agent365-identifiers.json",
        ):
            write_json(runtime_dir / name, read_local_json(workspace / name))
        for instance_path in sorted(workspace.glob("instance.*.json")):
            write_json(runtime_dir / instance_path.name, read_local_json(instance_path))

    for name in (
        "agent365-registration-app.json",
        "agent365-package-cleanup.json",
        "agent365-catalog-cleanup.json",
        "agent365-registration-cleanup.json",
    ):
        path = REPO_ROOT / ".local" / name
        if path.exists():
            write_json(root / "local" / name, read_local_json(path))


def capture_azure(root: Path, runtimes: list[str]) -> None:
    azure_dir = root / "azure"
    account = run_json(["az", "account", "show", "-o", "json"])
    write_json(azure_dir / "account.json", account)
    write_json(azure_dir / "resource-groups.json", run_json(["az", "group", "list", "-o", "json"]))

    for runtime in runtimes:
        outputs = read_local_json(runtime_outputs_path(runtime))
        if not isinstance(outputs, dict):
            continue
        for app_key in ("bridge_app_name", "private_mcp_app_name"):
            app_name = outputs.get(app_key)
            if not app_name:
                continue
            resources = run_json(["az", "resource", "list", "--name", str(app_name), "-o", "json"])
            resource_group = ""
            if isinstance(resources, list) and resources:
                resource_group = str(resources[0].get("resourceGroup", ""))
            app_dir = azure_dir / runtime / safe_name(str(app_name))
            write_json(app_dir / "resource.lookup.json", resources)
            if not resource_group:
                write_json(app_dir / "containerapp.lookup-error.json", {"error": "resource group not found", "appName": app_name})
                continue
            write_json(
                app_dir / "containerapp.show.json",
                run_json(["az", "containerapp", "show", "--name", str(app_name), "--resource-group", resource_group, "-o", "json"]),
            )
            write_json(
                app_dir / "containerapp.revisions.json",
                run_json(["az", "containerapp", "revision", "list", "--name", str(app_name), "--resource-group", resource_group, "-o", "json"]),
            )


def capture_graph_runtime(root: Path, runtime: str) -> None:
    graph_dir = root / "graph" / runtime
    workspace = agent365_workspace(runtime)
    generated = read_local_json(workspace / "a365.generated.config.json")
    metadata = read_local_json(workspace / f"{runtime}-agent365-identifiers.json")
    state_files = sorted(workspace.glob("instance.*.json"))

    blueprint_id = generated.get("agentBlueprintObjectId") if isinstance(generated, dict) else ""
    blueprint_app_id = generated.get("agentBlueprintId") if isinstance(generated, dict) else ""
    blueprint_sp_id = generated.get("agentBlueprintServicePrincipalObjectId") if isinstance(generated, dict) else ""
    if blueprint_id:
        write_json(graph_dir / "blueprint.application.json", az_rest(graph_url(f"/applications/{blueprint_id}?$select=id,appId,displayName,identifierUris,signInAudience,api,requiredResourceAccess,passwordCredentials,keyCredentials")))
    if blueprint_sp_id:
        write_json(graph_dir / "blueprint.service-principal.json", az_rest(graph_url(f"/servicePrincipals/{blueprint_sp_id}?$select=id,appId,displayName,servicePrincipalNames,appRoles,oauth2PermissionScopes,appRoleAssignments")))
    if blueprint_app_id:
        agent_name = str(metadata.get("agentName", runtime)).replace("'", "''") if isinstance(metadata, dict) else runtime
        encoded = urllib.parse.quote(f"displayName eq '{agent_name}'", safe="")
        write_json(graph_dir / "applications-by-agent-name.json", az_rest(graph_url(f"/applications?$filter={encoded}&$select=id,appId,displayName,deletedDateTime")))

    for state_path in state_files:
        state = read_local_json(state_path)
        state_dir = graph_dir / state_path.stem
        if state.get("agentIdentityId"):
            write_json(
                state_dir / "agent-identity.service-principal.json",
                az_rest(graph_url(f"/servicePrincipals/{state['agentIdentityId']}?$select=id,appId,displayName,servicePrincipalNames,appOwnerOrganizationId")),
            )
        if state.get("agentUserId"):
            write_json(
                state_dir / "agent-user.json",
                az_rest(graph_url(f"/users/{state['agentUserId']}?$select=id,displayName,userPrincipalName,mail,usageLocation,accountEnabled,assignedLicenses,identityParentId,createdDateTime")),
            )
        if state.get("agentRegistrationId"):
            auth_file = REPO_ROOT / ".local" / "agent365-registration-app.json"
            write_json(
                state_dir / "agent-registration.json",
                app_only_graph_get(f"/copilot/agentRegistrations/{state['agentRegistrationId']}", auth_file, beta=True),
            )


def capture_graph(root: Path, runtimes: list[str]) -> None:
    graph_dir = root / "graph"
    write_json(graph_dir / "domains.json", az_rest(graph_url("/domains?$select=id,isDefault,isInitial,isVerified,supportedServices")))
    write_json(graph_dir / "subscribed-skus.json", az_rest(graph_url("/subscribedSkus?$select=skuId,skuPartNumber,prepaidUnits,consumedUnits,capabilityStatus")))
    for runtime in runtimes:
        capture_graph_runtime(root, runtime)


def capture_summary(root: Path, runtimes: list[str]) -> None:
    summary: dict[str, Any] = {
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "repository": str(REPO_ROOT),
        "runtimes": runtimes,
        "redaction": "Values whose key names contain secret/password/token/credential/privateKey/apiKey are replaced with <redacted>.",
        "notes": [
            "Snapshot is diagnostic only and is not sufficient to recreate secrets.",
            "Stored under .local/snapshots, which is ignored by git.",
            "Use JSON diffs against later snapshots to compare Azure, Entra, Graph, and Agent 365 state.",
        ],
    }
    write_json(root / "summary.json", summary)


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture a redacted diagnostic snapshot of Autopilots Azure/Graph/Agent 365 state.")
    parser.add_argument("--runtime", choices=["openclaw", "hermes"], action="append", help="Runtime to capture. Repeatable. Defaults to both.")
    parser.add_argument("--output-dir", default="", help="Snapshot output directory. Defaults to .local/snapshots/<utc timestamp>.")
    parser.add_argument("--skip-azure", action="store_true")
    parser.add_argument("--skip-graph", action="store_true")
    args = parser.parse_args()

    runtimes = args.runtime or ["openclaw", "hermes"]
    root = Path(args.output_dir) if args.output_dir else SNAPSHOT_ROOT / now_stamp()
    capture_summary(root, runtimes)
    capture_local_state(root, runtimes)
    if not args.skip_azure:
        capture_azure(root, runtimes)
    if not args.skip_graph:
        capture_graph(root, runtimes)
    print(json.dumps({"snapshotDir": str(root)}, indent=2), flush=True)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(exc.stderr, file=sys.stderr)
        raise SystemExit(exc.returncode) from exc
