from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from scripts.setup_agent365 import agent365_workspace, load_json, write_json
from scripts.tf_helpers import output


GRAPH_BASE = "https://graph.microsoft.com"
DEFAULT_LICENSE_SKUS = [
    "AGENT_365",
    "Microsoft_365_Copilot",
    "Microsoft_365_E5_(no_Teams)",
    "Microsoft_Teams_Enterprise_New",
    "FLOW_FREE",
]


class GraphError(RuntimeError):
    def __init__(self, method: str, url: str, status: int, body: str):
        super().__init__(f"{method} {url} failed with HTTP {status}: {body}")
        self.method = method
        self.url = url
        self.status = status
        self.body = body


class GraphClient:
    def __init__(self, token: str, *, dry_run: bool = False):
        self.token = token
        self.dry_run = dry_run

    @classmethod
    def from_az_cli(cls, *, dry_run: bool = False) -> "GraphClient":
        token = output([
            "az", "account", "get-access-token", "--resource-type", "ms-graph",
            "--query", "accessToken", "-o", "tsv",
        ])
        return cls(token, dry_run=dry_run)

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        api_version: str = "v1.0",
        empty_ok: bool = False,
    ) -> dict[str, Any]:
        url = path if path.startswith("https://") else f"{GRAPH_BASE}/{api_version}{path}"
        if self.dry_run and method.upper() not in {"GET", "HEAD"}:
            print(f"DRY-RUN {method.upper()} {url}", flush=True)
            if body is not None:
                print(json.dumps(redact(body), indent=2), flush=True)
            return {}

        data = None
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise GraphError(method.upper(), url, exc.code, raw) from exc
        return json.loads(raw) if raw.strip() else {}


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "<redacted>" if "secret" in key.lower() or key.lower() == "password" else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def state_file(state_name: str, mail_nickname: str) -> Path:
    return agent365_workspace(state_name) / f"instance.{mail_nickname}.json"


def load_state(path: Path) -> dict[str, Any]:
    return load_json(path) if path.exists() else {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, state)


def owner_id(graph: GraphClient, *, owner_id_arg: str, owner_upn: str) -> str:
    if owner_id_arg:
        return owner_id_arg
    if not owner_upn:
        raise ValueError("Pass --owner-id or --owner-upn.")
    user = graph.request("GET", f"/users/{urllib.parse.quote(owner_upn)}?$select=id,userPrincipalName")
    return str(user["id"])


def generated_blueprint_id(state_name: str, explicit_blueprint_id: str) -> str:
    if explicit_blueprint_id:
        return explicit_blueprint_id
    generated_path = agent365_workspace(state_name) / "a365.generated.config.json"
    blueprint_id = str(load_json(generated_path).get("agentBlueprintId", "")).strip()
    if not blueprint_id:
        raise KeyError(f"{generated_path} does not contain agentBlueprintId.")
    return blueprint_id


def ensure_agent_identity(
    graph: GraphClient, *, state: dict[str, Any], display_name: str,
    blueprint_id: str, sponsor_user_id: str,
) -> dict[str, Any]:
    existing_id = state.get("agentIdentityId")
    if existing_id:
        identity = graph.request("GET", f"/servicePrincipals/{existing_id}?$select=id,appId,displayName")
        print(f"Using existing agent identity {identity['id']}", flush=True)
        return identity
    identity = graph.request(
        "POST", "/servicePrincipals/microsoft.graph.agentIdentity",
        body={
            "displayName": display_name,
            "agentIdentityBlueprintId": blueprint_id,
            "sponsors@odata.bind": [f"{GRAPH_BASE}/v1.0/users/{sponsor_user_id}"],
        },
    )
    print(f"Created agent identity {identity.get('id')}", flush=True)
    return identity


def ensure_agent_user(
    graph: GraphClient, *, state: dict[str, Any], display_name: str,
    mail_nickname: str, user_principal_name: str, agent_identity_id: str,
) -> dict[str, Any]:
    existing_id = state.get("agentUserId")
    if existing_id:
        user = graph.request(
            "GET",
            f"/users/{existing_id}?$select=id,displayName,userPrincipalName,mail,usageLocation,assignedLicenses",
        )
        print(f"Using existing agent user {user['userPrincipalName']}", flush=True)
        return user
    user = graph.request(
        "POST", "/users/microsoft.graph.agentUser",
        body={
            "accountEnabled": True,
            "displayName": display_name,
            "mailNickname": mail_nickname,
            "userPrincipalName": user_principal_name,
            "identityParentId": agent_identity_id,
        },
    )
    print(f"Created agent user {user.get('userPrincipalName')}", flush=True)
    return user


def update_usage_location(graph: GraphClient, *, user_id: str, usage_location: str) -> None:
    if usage_location:
        graph.request("PATCH", f"/users/{user_id}", body={"usageLocation": usage_location}, empty_ok=True)
        print(f"Set usageLocation={usage_location}", flush=True)


def sku_ids_by_part_number(graph: GraphClient) -> dict[str, str]:
    payload = graph.request("GET", "/subscribedSkus?$select=skuId,skuPartNumber,prepaidUnits,consumedUnits")
    return {str(item["skuPartNumber"]): str(item["skuId"]) for item in payload.get("value", [])}


def missing_license_payload(user: dict[str, Any], sku_ids: dict[str, str], requested_skus: list[str]) -> list[dict[str, Any]]:
    assigned = {str(item["skuId"]).lower() for item in user.get("assignedLicenses", [])}
    missing = []
    for sku in requested_skus:
        sku_id = sku_ids.get(sku)
        if not sku_id:
            raise KeyError(f"Tenant does not expose subscribed SKU '{sku}'.")
        if sku_id.lower() not in assigned:
            missing.append({"skuId": sku_id, "disabledPlans": []})
    return missing


def assign_missing_licenses(graph: GraphClient, *, user_id: str, requested_skus: list[str]) -> list[str]:
    user = graph.request("GET", f"/users/{user_id}?$select=id,assignedLicenses")
    sku_ids = sku_ids_by_part_number(graph)
    add_licenses = missing_license_payload(user, sku_ids, requested_skus)
    if not add_licenses:
        print("All requested licenses are already assigned.", flush=True)
        return []
    graph.request("POST", f"/users/{user_id}/assignLicense", body={"addLicenses": add_licenses, "removeLicenses": []})
    assigned = [sku for sku in requested_skus if sku_ids.get(sku) in {item["skuId"] for item in add_licenses}]
    print(f"Assigned licenses: {', '.join(assigned)}", flush=True)
    return assigned


def delete_graph_object(graph: GraphClient, path: str, *, label: str, api_version: str = "v1.0") -> bool:
    try:
        graph.request("DELETE", path, api_version=api_version, empty_ok=True)
    except GraphError as exc:
        if exc.status == 404:
            print(f"{label} not found.", flush=True)
            return False
        raise
    print(f"Deleted {label}.", flush=True)
    return True


def parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def provision_command(args: argparse.Namespace) -> None:
    state_path = Path(args.state_file) if args.state_file else state_file(args.state_name, args.mail_nickname)
    state = load_state(state_path)
    graph = GraphClient.from_az_cli(dry_run=args.dry_run)
    sponsor = owner_id(graph, owner_id_arg=args.owner_id, owner_upn=args.owner_upn)
    blueprint_id = generated_blueprint_id(args.state_name, args.agent_blueprint_id)
    identity = ensure_agent_identity(
        graph, state=state, display_name=args.identity_display_name or f"{args.display_name} Identity",
        blueprint_id=blueprint_id, sponsor_user_id=sponsor,
    )
    if not identity.get("id") and args.dry_run:
        print("Dry-run stopped before dependent Agent User calls; no identity was created.", flush=True)
        return
    state["agentIdentityId"] = identity["id"]
    state["agentIdentityAppId"] = identity.get("appId", identity["id"])
    if not args.dry_run:
        save_state(state_path, state)
    user = ensure_agent_user(
        graph, state=state, display_name=args.display_name, mail_nickname=args.mail_nickname,
        user_principal_name=args.agent_upn, agent_identity_id=state["agentIdentityId"],
    )
    if not user.get("id") and args.dry_run:
        print("Dry-run stopped before dependent license calls; no Agent User was created.", flush=True)
        return
    state["agentUserId"] = user["id"]
    state["agentUserPrincipalName"] = user.get("userPrincipalName", args.agent_upn)
    if not args.dry_run:
        save_state(state_path, state)
    update_usage_location(graph, user_id=state["agentUserId"], usage_location=args.usage_location)
    requested_skus = parse_csv(args.license_skus)
    if requested_skus:
        assign_missing_licenses(graph, user_id=state["agentUserId"], requested_skus=requested_skus)
    print(f"Instance state: {state_path}", flush=True)


def cleanup_command(args: argparse.Namespace) -> None:
    state_path = Path(args.state_file) if args.state_file else state_file(args.state_name, args.mail_nickname)
    state = load_state(state_path)
    if not state.get("agentUserId") and not state.get("agentIdentityId"):
        raise ValueError(f"{state_path} has no provisioned Agent User or Agent Identity to delete.")
    graph = GraphClient.from_az_cli(dry_run=args.dry_run)
    for key, collection in (("agentUserId", "users"), ("agentIdentityId", "servicePrincipals")):
        if state.get(key):
            delete_graph_object(graph, f"/{collection}/{state[key]}", label=f"{key} {state[key]}")
    if args.remove_state and not args.dry_run:
        state_path.unlink()
        print(f"Removed {state_path}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision a Hermes Worker's Agent Identity, Agent User and licenses via Graph.")
    parser.add_argument("--dry-run", action="store_true", help="Print write calls without sending them or saving instance state.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    provision = subparsers.add_parser("provision", help="Create or reuse a Worker's Agent Identity, Agent User and licenses.")
    provision.add_argument("--state-name", default="hermes", help="Worker state directory under .local.")
    provision.add_argument("--owner-id", default="", help="Owner/sponsor user object ID.")
    provision.add_argument("--owner-upn", default="", help="Owner/sponsor UPN. Used when --owner-id is omitted.")
    provision.add_argument("--agent-upn", required=True)
    provision.add_argument("--display-name", required=True)
    provision.add_argument("--mail-nickname", required=True)
    provision.add_argument("--identity-display-name", default="")
    provision.add_argument("--agent-blueprint-id", default="", help="Defaults to .local/<state-name>/agent365 generated config.")
    provision.add_argument("--usage-location", default="CZ")
    provision.add_argument("--license-skus", default=",".join(DEFAULT_LICENSE_SKUS))
    provision.add_argument("--state-file", default="")
    provision.set_defaults(func=provision_command)
    cleanup = subparsers.add_parser("cleanup", help="Delete only the Agent User and Agent Identity recorded in one Worker's instance state.")
    cleanup.add_argument("--state-name", default="hermes", help="Worker state directory under .local.")
    cleanup.add_argument("--mail-nickname", required=True)
    cleanup.add_argument("--state-file", default="")
    cleanup.add_argument("--remove-state", action="store_true")
    cleanup.set_defaults(func=cleanup_command)
    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
