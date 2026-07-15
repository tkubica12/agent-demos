from __future__ import annotations

import argparse
import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from scripts.deploy_apps_runtime import activate_runtime_tfvars, terraform_workspace_name
from scripts.setup_app_tfvars import runtime_app_tfvars_path, runtime_outputs_path
from scripts.tf_helpers import PLATFORM_DIR, resolve_executable


RUNTIMES = ("openclaw", "hermes")
SMOKE_PROMPTS = {
    "openclaw": "List services from private incidents MCP",
    "hermes": "Reply with exactly: Hermes bridge OK",
}
EXPECTED_MARKERS = {
    "openclaw": [
        "core_banking",
        "card_payments",
        "digital_onboarding",
        "fraud_detection",
        "wealth_portfolio",
    ],
    "hermes": ["Hermes bridge OK"],
}
SANDBOX_RESOURCE = "https://dynamicsessions.io"
SANDBOX_DATA_OWNER_ROLE = "Container Apps SandboxGroup Data Owner"


def runtime_list(value: str) -> list[str]:
    if value == "both":
        return list(RUNTIMES)
    if value not in RUNTIMES:
        raise ValueError(f"Unsupported runtime '{value}'.")
    return [value]


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return payload


def terraform_outputs(directory: Path) -> dict[str, Any]:
    result = subprocess.run(
        [resolve_executable("terraform"), "output", "-json"],
        cwd=str(directory),
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    return {key: value["value"] for key, value in payload.items()}


def az_account_id() -> str:
    result = subprocess.run(
        [resolve_executable("az"), "account", "show", "--query", "id", "-o", "tsv"],
        capture_output=True,
        text=True,
        check=True,
    )
    value = result.stdout.strip()
    if not value:
        raise RuntimeError("Azure account subscription id was empty.")
    return value


def signed_in_user_id() -> str:
    result = subprocess.run(
        [resolve_executable("az"), "ad", "signed-in-user", "show", "--query", "id", "-o", "tsv"],
        capture_output=True,
        text=True,
        check=True,
    )
    value = result.stdout.strip()
    if not value:
        raise RuntimeError("Signed-in Azure user object id was empty.")
    return value


def role_assignment_command(scope: str, assignee_object_id: str) -> list[str]:
    return [
        "az",
        "role",
        "assignment",
        "create",
        "--assignee-object-id",
        assignee_object_id,
        "--assignee-principal-type",
        "User",
        "--role",
        SANDBOX_DATA_OWNER_ROLE,
        "--scope",
        scope,
        "-o",
        "json",
    ]


def bridge_url(outputs: dict[str, Any]) -> str:
    value = str(outputs.get("bridge_url") or "").rstrip("/")
    if not value:
        raise KeyError("bridge_url")
    return value


def invoke_body(runtime: str, *, conversation_id: str = "", message: str = "") -> dict[str, str]:
    return {
        "conversationId": conversation_id or f"{runtime}-operator-smoke",
        "message": message or SMOKE_PROMPTS[runtime],
    }


def response_text(payload: Any) -> str:
    if isinstance(payload, dict):
        value = payload.get("response")
        if isinstance(value, str):
            return value
    return json.dumps(payload, sort_keys=True) if not isinstance(payload, str) else payload


def missing_expected_markers(runtime: str, payload: Any) -> list[str]:
    text = response_text(payload).lower()
    return [marker for marker in EXPECTED_MARKERS[runtime] if marker.lower() not in text]


def sandbox_group_url(platform: dict[str, Any], subscription_id: str) -> str:
    return (
        f"https://management.{platform['sandbox_location']}.azuredevcompute.io"
        f"/subscriptions/{subscription_id}"
        f"/resourceGroups/{platform['resource_group_name']}"
        f"/sandboxGroups/{platform['sandbox_group_name']}"
    )


def runtime_sandbox_selector(runtime: str, outputs: dict[str, Any]) -> dict[str, Any]:
    return {
        "labels": {
            "app": "autopilots-on-azure",
            "kind": runtime,
        },
        "dataVolume": outputs.get("runtime_data_volume_name") or "",
    }


def sandbox_matches(sandbox: dict[str, Any], selector: dict[str, Any]) -> bool:
    labels = sandbox.get("labels") or {}
    if any(labels.get(key) != value for key, value in selector["labels"].items()):
        return False
    if "runtime" in labels or "autopilot" in labels:
        return False
    data_volume = selector.get("dataVolume")
    volumes = sandbox.get("volumes") or []
    return bool(data_volume and any(volume.get("volumeName") == data_volume for volume in volumes))


def http_json(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            payload: Any = json.loads(raw) if raw.strip() else {}
            return {"ok": 200 <= response.status < 300, "statusCode": response.status, "body": payload}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            payload = raw
        return {"ok": False, "statusCode": exc.code, "body": payload}
    except Exception as exc:
        return {"ok": False, "error": exc.__class__.__name__, "message": str(exc)}


def az_rest_json(url: str, *, method: str = "get", resource: str = SANDBOX_RESOURCE, timeout: int = 120) -> dict[str, Any]:
    result = subprocess.run(
        [
            resolve_executable("az"),
            "rest",
            "--resource",
            resource,
            "--method",
            method,
            "--url",
            url,
            "-o",
            "json",
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        return {
            "ok": False,
            "command": f"az rest --resource {resource} --method {method} --url {url}",
            "returnCode": result.returncode,
            "stderr": result.stderr.strip(),
            "stdout": result.stdout.strip(),
        }
    raw = result.stdout.strip()
    return {"ok": True, "body": json.loads(raw) if raw else {}}


def health_check(runtime: str, *, timeout: int = 120) -> dict[str, Any]:
    try:
        outputs = load_json(runtime_outputs_path(runtime))
        url = bridge_url(outputs)
    except Exception as exc:
        return {"runtime": runtime, "check": "health", "ok": False, "error": exc.__class__.__name__, "message": str(exc)}

    result = http_json(f"{url}/health", timeout=timeout)
    return {"runtime": runtime, "check": "health", "url": f"{url}/health", **result}


def invoke_check(runtime: str, *, message: str = "", timeout: int = 120) -> dict[str, Any]:
    try:
        outputs = load_json(runtime_outputs_path(runtime))
        url = bridge_url(outputs)
    except Exception as exc:
        return {"runtime": runtime, "check": "invoke", "ok": False, "error": exc.__class__.__name__, "message": str(exc)}

    body = invoke_body(runtime, message=message)
    result = http_json(f"{url}/invoke", method="POST", body=body, timeout=timeout)
    missing = missing_expected_markers(runtime, result.get("body")) if result.get("ok") and not message else []
    return {
        "runtime": runtime,
        "check": "invoke",
        "url": f"{url}/invoke",
        "expectedMarkers": EXPECTED_MARKERS[runtime] if not message else [],
        "missingExpectedMarkers": missing,
        **result,
        "ok": bool(result.get("ok")) and not missing,
    }


def dream_check(*, focus: str = "", max_records: int = 5, timeout: int = 900) -> dict[str, Any]:
    runtime = "hermes"
    try:
        outputs = load_json(runtime_outputs_path(runtime))
        tfvars = load_json(runtime_app_tfvars_path(runtime))
        url = bridge_url(outputs)
        api_key = str(tfvars.get("api_server_key") or "")
        if not api_key:
            raise KeyError("api_server_key")
    except Exception as exc:
        return {"runtime": runtime, "check": "dream", "ok": False, "error": exc.__class__.__name__, "message": str(exc)}

    result = http_json(
        f"{url}/internal/dream",
        method="POST",
        body={"focus": focus, "maxRecords": max_records},
        headers={"X-Autopilot-Key": api_key},
        timeout=timeout,
    )
    return {"runtime": runtime, "check": "dream", "url": f"{url}/internal/dream", **result}


def diag_check(runtime: str, *, timeout: int = 120) -> dict[str, Any]:
    try:
        outputs = load_json(runtime_outputs_path(runtime))
        url = bridge_url(outputs)
    except Exception as exc:
        return {"runtime": runtime, "check": "diag", "ok": False, "error": exc.__class__.__name__, "message": str(exc)}

    result = http_json(f"{url}/diag/teams", timeout=timeout)
    if result.get("statusCode") == 404:
        return {
            "runtime": runtime,
            "check": "diag",
            "ok": True,
            "statusCode": 404,
            "message": "OPENCLAW_BRIDGE_DEBUG is disabled on the bridge.",
        }
    return {"runtime": runtime, "check": "diag", "url": f"{url}/diag/teams", **result}


def az_log_command(app_name: str, resource_group: str, *, tail: int, follow: bool) -> list[str]:
    command = [
        "az",
        "containerapp",
        "logs",
        "show",
        "--name",
        app_name,
        "--resource-group",
        resource_group,
        "--tail",
        str(tail),
    ]
    if follow:
        command.append("--follow")
    return command


def lookup_resource_group(app_name: str) -> str:
    result = subprocess.run(
        [
            resolve_executable("az"),
            "resource",
            "list",
            "--name",
            app_name,
            "--query",
            "[0].resourceGroup",
            "-o",
            "tsv",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    value = result.stdout.strip()
    if not value:
        raise RuntimeError(f"Could not find resource group for Container App '{app_name}'.")
    return value


def runtime_app_name(runtime: str, app: str) -> str:
    outputs = load_json(runtime_outputs_path(runtime))
    key = "private_mcp_app_name" if app == "private-mcp" else "bridge_app_name"
    value = str(outputs.get(key) or "")
    if not value:
        raise KeyError(key)
    return value


def print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True), flush=True)


def run_status(args: argparse.Namespace) -> int:
    results: list[dict[str, Any]] = []
    for runtime in runtime_list(args.runtime):
        results.append(health_check(runtime, timeout=args.timeout))
        if args.invoke:
            results.append(invoke_check(runtime, timeout=args.timeout))
        if args.diag:
            results.append(diag_check(runtime, timeout=args.timeout))
    print_json(results)
    return 0 if all(result.get("ok") for result in results) else 1


def run_smoke(args: argparse.Namespace) -> int:
    results = [invoke_check(runtime, message=args.message, timeout=args.timeout) for runtime in runtime_list(args.runtime)]
    print_json(results)
    return 0 if all(result.get("ok") for result in results) else 1


def run_dream(args: argparse.Namespace) -> int:
    result = dream_check(focus=args.focus, max_records=args.max_records, timeout=args.timeout)
    print_json(result)
    return 0 if result.get("ok") else 1


def run_activate(args: argparse.Namespace) -> int:
    activate_runtime_tfvars(args.runtime)
    print_json(
        {
            "runtime": args.runtime,
            "terraformWorkspace": terraform_workspace_name(args.runtime),
            "runtimeTfvarsFile": str(runtime_app_tfvars_path(args.runtime)),
            "activeTfvarsFiles": [
                "terraform\\apps\\generated.app.auto.tfvars.json",
                "terraform\\apps\\generated.runtime.auto.tfvars.json",
            ],
            "next": [
                f"terraform -chdir=terraform\\apps workspace select {terraform_workspace_name(args.runtime)}",
                "terraform -chdir=terraform\\apps plan",
            ],
        }
    )
    return 0


def run_logs(args: argparse.Namespace) -> int:
    app_name = runtime_app_name(args.runtime, args.app)
    resource_group = args.resource_group or lookup_resource_group(app_name)
    command = az_log_command(app_name, resource_group, tail=args.tail, follow=args.follow)
    if not args.execute:
        print("+ " + " ".join(command), flush=True)
        return 0
    return subprocess.run([resolve_executable(command[0]), *command[1:]], check=False).returncode


def run_reset_sandbox(args: argparse.Namespace) -> int:
    platform = terraform_outputs(PLATFORM_DIR)
    outputs = load_json(runtime_outputs_path(args.runtime))
    base_url = sandbox_group_url(platform, az_account_id())
    selector = runtime_sandbox_selector(args.runtime, outputs)
    listed = az_rest_json(f"{base_url}/sandboxes", timeout=args.timeout)
    if not listed.get("ok"):
        print_json({"runtime": args.runtime, "found": False, "error": "sandboxListFailed", **listed})
        return 1

    body = listed.get("body")
    sandboxes = body if isinstance(body, list) else body.get("value", []) if isinstance(body, dict) else []
    sandbox = next((item for item in sandboxes if isinstance(item, dict) and sandbox_matches(item, selector)), None)
    if not sandbox:
        print_json(
            {
                "runtime": args.runtime,
                "found": False,
                "message": "No matching sandbox found. The next bridge invoke will create one if the runtime is configured.",
            }
        )
        return 0

    sandbox_id = str(sandbox["id"])
    summary = {
        "runtime": args.runtime,
        "found": True,
        "sandboxId": sandbox_id,
        "state": sandbox.get("state"),
        "labels": sandbox.get("labels", {}),
        "dataVolume": selector["dataVolume"],
        "execute": args.execute,
    }
    if not args.execute:
        summary["next"] = f"Rerun with --execute to delete sandbox {sandbox_id}. The data volume is not deleted."
        print_json(summary)
        return 0

    deleted = az_rest_json(f"{base_url}/sandboxes/{sandbox_id}", method="delete", timeout=args.timeout)
    if not deleted.get("ok"):
        print_json({"runtime": args.runtime, "sandboxId": sandbox_id, "error": "sandboxDeleteFailed", **deleted})
        return 1
    summary["deleted"] = True
    summary["next"] = f"Run `uv run python -m scripts.demo_ops smoke --runtime {args.runtime}` to create a fresh sandbox."
    print_json(summary)
    return 0


def run_grant_sandbox_access(args: argparse.Namespace) -> int:
    platform = terraform_outputs(PLATFORM_DIR)
    scope = str(platform["sandbox_group_id"])
    assignee_object_id = args.assignee_object_id or signed_in_user_id()
    command = role_assignment_command(scope, assignee_object_id)
    summary = {
        "role": SANDBOX_DATA_OWNER_ROLE,
        "scope": scope,
        "assigneeObjectId": assignee_object_id,
        "execute": args.execute,
    }
    if not args.execute:
        summary["next"] = "Rerun with --execute to grant this role."
        summary["command"] = " ".join(command)
        print_json(summary)
        return 0

    result = subprocess.run([resolve_executable(command[0]), *command[1:]], capture_output=True, text=True)
    if result.returncode != 0:
        print_json({**summary, "ok": False, "returnCode": result.returncode, "stdout": result.stdout.strip(), "stderr": result.stderr.strip()})
        return result.returncode
    summary["ok"] = True
    summary["assignment"] = json.loads(result.stdout) if result.stdout.strip() else {}
    print_json(summary)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Operator helper for side-by-side Autopilots demos.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Check bridge health, optionally /invoke and Teams diagnostics.")
    status.add_argument("--runtime", choices=[*RUNTIMES, "both"], default="both")
    status.add_argument("--invoke", action="store_true", help="Also run the runtime-specific direct /invoke smoke prompt.")
    status.add_argument("--diag", action="store_true", help="Also read /diag/teams when bridge debug is enabled.")
    status.add_argument("--timeout", type=int, default=120)
    status.set_defaults(func=run_status)

    smoke = subparsers.add_parser("smoke", help="Run direct /invoke smoke prompts.")
    smoke.add_argument("--runtime", choices=[*RUNTIMES, "both"], default="both")
    smoke.add_argument("--message", default="", help="Override the default runtime smoke prompt. Expected-marker checks are skipped.")
    smoke.add_argument("--timeout", type=int, default=120)
    smoke.set_defaults(func=run_smoke)

    dream = subparsers.add_parser("dream", help="Run a secured local Hermes reflection and return its redacted learning packet.")
    dream.add_argument("--focus", default="", help="Optional reflection focus. Defaults to recent meaningful work.")
    dream.add_argument("--max-records", type=int, choices=range(1, 11), default=5)
    dream.add_argument("--timeout", type=int, default=900)
    dream.set_defaults(func=run_dream)

    activate = subparsers.add_parser("activate", help="Make one runtime's tfvars active for Terraform operations.")
    activate.add_argument("--runtime", choices=RUNTIMES, required=True)
    activate.set_defaults(func=run_activate)

    logs = subparsers.add_parser("logs", help="Print or run the Azure Container Apps log command for a runtime app.")
    logs.add_argument("--runtime", choices=RUNTIMES, required=True)
    logs.add_argument("--app", choices=["bridge", "private-mcp"], default="bridge")
    logs.add_argument("--resource-group", default="", help="Skip Azure lookup and use this resource group.")
    logs.add_argument("--tail", type=int, default=80)
    logs.add_argument("--follow", action="store_true")
    logs.add_argument("--execute", action="store_true", help="Run the log command instead of only printing it.")
    logs.set_defaults(func=run_logs)

    reset = subparsers.add_parser("reset-sandbox", help="Delete one runtime sandbox while keeping its data volume.")
    reset.add_argument("--runtime", choices=RUNTIMES, required=True)
    reset.add_argument("--execute", action="store_true", help="Actually delete the sandbox. Omitted means dry-run.")
    reset.add_argument("--timeout", type=int, default=300)
    reset.set_defaults(func=run_reset_sandbox)

    grant = subparsers.add_parser("grant-sandbox-access", help="Grant SandboxGroup Data Owner to an operator user.")
    grant.add_argument("--assignee-object-id", default="", help="User object id. Defaults to the current az signed-in user.")
    grant.add_argument("--execute", action="store_true", help="Actually create the role assignment. Omitted means dry-run.")
    grant.set_defaults(func=run_grant_sandbox_access)

    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
