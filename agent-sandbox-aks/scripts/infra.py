"""Plan, apply once, and independently observe the dedicated AKS environment."""

import argparse
import copy
import ipaddress
import json
from datetime import UTC, datetime
from urllib.parse import urlparse

from run_state import CONFIG, PRIVATE, ROOT, RUN, accounting, check_limits, command, now, read, sha, source_hash, write, writer

TF = ROOT / "terraform"
API = "2025-10-01"


def tf(*args):
    return ["terraform", f"-chdir={TF}", *args]


def arm_list(attempt, url):
    expected = urlparse(url)
    items, visited = [], set()
    while url:
        parsed = urlparse(url)
        if (parsed.scheme, parsed.netloc, parsed.path.lower()) != (expected.scheme, expected.netloc, expected.path.lower()):
            raise RuntimeError("Unexpected ARM pagination destination.")
        if url in visited or len(visited) >= 20:
            raise RuntimeError("ARM pagination exceeded its bound or repeated a page.")
        visited.add(url)
        page = json.loads(command(f"{attempt}-page{len(visited)}", ["az", "rest", "--method", "get", "--url", url]))
        items.extend(page["value"])
        url = page.get("nextLink")
    return items


def validate_plan(plan, approved, owned_group_id=None):
    changes = plan.get("resource_changes", [])
    if {c["address"] for c in changes} != {"azapi_resource.group", "azapi_resource.cluster"}:
        raise ValueError("Unexpected Terraform resources.")
    for change in changes:
        allowed_noop = (
            owned_group_id and change["address"] == "azapi_resource.group"
            and change["change"]["actions"] == ["no-op"]
            and change["change"]["after"]["id"] == owned_group_id
        )
        if change["change"]["actions"] != ["create"] and not allowed_noop:
            raise ValueError("Only create-only changes or the verified owned group's no-op are approved.")
    group = next(c["change"]["after"] for c in changes if c["address"] == "azapi_resource.group")
    if group["name"] != approved["resource_group"] or group["parent_id"] != f"/subscriptions/{approved['subscription_id']}":
        raise ValueError("Resource group scope differs from approval.")
    cluster = next(c["change"]["after"] for c in changes if c["address"] == "azapi_resource.cluster")
    if cluster["name"] != approved["cluster_name"] or cluster["location"] != CONFIG["azure"]["location"]:
        raise ValueError("Cluster name/location differs from approval.")
    props = cluster["body"]["properties"]
    if props["nodeResourceGroup"] != approved["resource_group"] + "-nodes":
        raise ValueError("Node resource group differs from approval.")
    pools = props["agentPoolProfiles"]
    if len(pools) != 2 or {p["name"] for p in pools} != {"platform", "kata"}:
        raise ValueError("Exactly two approved pools are required.")
    for pool in pools:
        is_kata = pool["name"] == "kata"
        expected_size = CONFIG["azure"]["kata_size" if is_kata else "platform_size"]
        if pool["count"] != 1 or pool["vmSize"] != expected_size or pool["enableAutoScaling"]:
            raise ValueError("Pool capacity differs from approval.")
        if pool["enableNodePublicIP"] or pool["securityProfile"]["sshAccess"] != "Disabled":
            raise ValueError("Node public IP/SSH must remain disabled.")
        if pool["securityProfile"]["enableSecureBoot"] != (not is_kata) or pool["securityProfile"]["enableVTPM"] != (not is_kata):
            raise ValueError("Trusted Launch separation differs from approval.")
        if pool["workloadRuntime"] != ("KataVmIsolation" if is_kata else "OCIContainer"):
            raise ValueError("Unexpected workload runtime.")
        if is_kata and pool["nodeTaints"] != ["sandbox=kata:NoSchedule"]:
            raise ValueError("Kata scheduling isolation is missing.")
        expected_upgrade = {"maxSurge": "0", "maxUnavailable": "1"} if is_kata else {"maxSurge": "1", "maxUnavailable": "0"}
        if pool["upgradeSettings"] != expected_upgrade or approved["maximum_vcpu"] < 12:
            raise ValueError("Upgrade capacity differs from approval.")
    if not props["disableLocalAccounts"] or not props["aadProfile"]["enableAzureRBAC"]:
        raise ValueError("Entra-only operator access is required.")
    ranges = props["apiServerAccessProfile"]["authorizedIPRanges"]
    if ranges != approved.get("operator_cidrs", [approved["operator_cidr"]]):
        raise ValueError("Unexpected API access range.")
    if props["networkProfile"]["networkDataplane"] != "cilium":
        raise ValueError("Cilium is required.")


def plan(attempt):
    approval = read(PRIVATE / "approval.json")
    if not (RUN / "status.json").exists():
        write(RUN / "status.json", {
            "run_id": RUN.name, "state": "PREFLIGHT", "created_at": now(),
            "cloud_budget_usd": 200, "model_requests": 0,
            "deployment_attempts": 0, "terraform_applies": 0, "repair_cycles": 0,
            "unknown_operations": 0,
            "checks": {f"C0{i}": "NOT_RUN" for i in range(1, 6)},
            "approval_sha256": sha(PRIVATE / "approval.json"),
            "retention": "Retain dedicated infrastructure; no scheduled destruction.",
        })
    state = read(RUN / "status.json")
    check_limits(state)
    repairing = bool(state["terraform_applies"])
    if repairing and state["state"] != "CLUSTER_ABSENT_OWNED_GROUP":
        raise RuntimeError("Existing deployment requires reconciled absence before a scoped creation repair.")
    PRIVATE.mkdir(parents=True, exist_ok=True)
    account = json.loads(command(f"{attempt}-account", [
        "az", "account", "show", "--query", "{subscription:id,tenant:tenantId,state:state}", "-o", "json",
    ]))
    if account != {"subscription": approval["subscription_id"], "tenant": approval["tenant_id"], "state": "Enabled"}:
        raise RuntimeError("Wrong active Azure account.")
    address = ipaddress.ip_network(approval["operator_cidr"])
    if address.version != 4 or address.prefixlen != 32 or address.network_address.is_unspecified:
        raise ValueError("Operator must have a concrete IPv4 /32.")
    for name in (approval["resource_group"], approval["resource_group"] + "-nodes"):
        exists = command(f"{attempt}-{name}-exists", [
            "az", "group", "exists", "--subscription", approval["subscription_id"], "--name", name, "-o", "json",
        ])
        if json.loads(exists) and not (repairing and name == approval["resource_group"] and state.get("owned_group_id")):
            raise RuntimeError(f"Refusing to adopt existing resource group {name}.")
    if repairing:
        state["repair_cycles"] += 1
        state["state"] = "REPAIR_PLANNING"
        write(RUN / "status.json", state)
    inputs = {key: approval[key] for key in ("subscription_id", "tenant_id", "resource_group", "cluster_name")}
    inputs["operator_cidrs"] = approval.get("operator_cidrs", [approval["operator_cidr"]])
    inputs.update(CONFIG["azure"], run_id=RUN.name)
    write(PRIVATE / "inputs.tfvars.json", inputs)
    command(f"{attempt}-init", tf("init", "-input=false", "-no-color",
            f"-backend-config=path={PRIVATE / 'terraform.tfstate'}"), timeout=600)
    command(f"{attempt}-validate", tf("validate", "-no-color"))
    plan_file = PRIVATE / f"{attempt}.tfplan"
    command(f"{attempt}-plan", tf("plan", "-input=false", "-no-color",
            f"-var-file={PRIVATE / 'inputs.tfvars.json'}", f"-out={plan_file}"), timeout=600)
    rendered = json.loads(command(f"{attempt}-render", tf("show", "-json", str(plan_file))))
    validate_plan(rendered, approval, state.get("owned_group_id") if repairing else None)
    state.update(
        state="PLAN_REVIEWED", plan_file=str(plan_file), plan_sha256=sha(plan_file),
        source_sha256=source_hash(), reviewed_at=now(), accounting=accounting(state),
        approval_sha256=sha(PRIVATE / "approval.json"),
    )
    write(RUN / "status.json", state)
    print(json.dumps({"state": state["state"], "plan": str(plan_file), "vcpu": 8}))


def apply(attempt):
    state = read(RUN / "status.json")
    check_limits(state)
    if state["state"] != "PLAN_REVIEWED":
        raise RuntimeError("A fresh reviewed plan is required; do not replay an accepted apply.")
    if (RUN / f"{attempt}-apply.json").exists():
        raise RuntimeError("Apply attempt is already consumed.")
    if sha(PRIVATE / "approval.json") != state["approval_sha256"]:
        raise RuntimeError("Approval changed after plan review.")
    from pathlib import Path
    plan_file = Path(state["plan_file"])
    if sha(plan_file) != state["plan_sha256"] or source_hash() != state["source_sha256"]:
        raise RuntimeError("Reviewed plan or source changed.")
    if (datetime.now(UTC) - datetime.fromisoformat(state["reviewed_at"])).total_seconds() > 900:
        raise RuntimeError("Plan review expired after 15 minutes.")
    if state["terraform_applies"] >= CONFIG["limits"]["terraform_applies"]:
        raise RuntimeError("CAPPED: Terraform attempts exhausted.")
    state["terraform_applies"] += 1
    state["last_apply_receipt"] = f"{attempt}-apply.json"
    write(RUN / "status.json", state)
    command(f"{attempt}-apply", tf("apply", "-input=false", "-no-color", "-parallelism=1", str(plan_file)),
            timeout=3000, mutation=True)
    observe(f"{attempt}-readback")


def validate_api_plan(rendered, approval):
    changes = {r["address"]: r["change"] for r in rendered["resource_changes"]}
    if set(changes) != {"azapi_resource.cluster", "azapi_resource.group"}:
        raise ValueError("Unexpected resources in API-access repair.")
    if changes["azapi_resource.group"]["actions"] != ["no-op"] or changes["azapi_resource.cluster"]["actions"] != ["update"]:
        raise ValueError("API-access repair must update only the existing cluster.")
    cluster = changes["azapi_resource.cluster"]
    before, after = copy.deepcopy(cluster["before"]), copy.deepcopy(cluster["after"])
    expected_id = f"/subscriptions/{approval['subscription_id']}/resourceGroups/{approval['resource_group']}/providers/Microsoft.ContainerService/managedClusters/{approval['cluster_name']}"
    if before["id"].lower() != expected_id.lower() or after["id"].lower() != expected_id.lower():
        raise ValueError("Unexpected target cluster.")
    old_ranges = before["body"]["properties"]["apiServerAccessProfile"].pop("authorizedIPRanges")
    new_ranges = after["body"]["properties"]["apiServerAccessProfile"].pop("authorizedIPRanges")
    if new_ranges != approval["operator_cidrs"] or not set(old_ranges) < set(new_ranges):
        raise ValueError("Only additive, approved operator addresses may be added.")
    for value in new_ranges:
        address = ipaddress.ip_network(value)
        if address.version != 4 or address.prefixlen != 32 or not address.network_address.is_global:
            raise ValueError("Operator addresses must be public IPv4 /32.")
    before.pop("output", None)
    after.pop("output", None)
    if before != after:
        raise ValueError("API repair would change unrelated cluster settings.")


def plan_api(attempt):
    approval = read(PRIVATE / "approval.json")
    state = read(RUN / "status.json")
    check_limits(state)
    if state["terraform_applies"] >= CONFIG["limits"]["terraform_applies"] or not approval.get("api_access_amendment"):
        raise RuntimeError("No approved Terraform/API-access repair allowance.")
    observed = json.loads(command(f"{attempt}-egress", [
        "curl.exe", "--silent", "--show-error", "--connect-timeout", "10",
        "--max-time", "20", "https://api.ipify.org?format=json",
    ]))["ip"] + "/32"
    if observed not in approval["operator_cidrs"]:
        raise RuntimeError("Current operator address differs from the proposed repair.")
    inputs = read(PRIVATE / "inputs.tfvars.json")
    inputs.pop("operator_cidr", None)
    inputs["operator_cidrs"] = approval["operator_cidrs"]
    write(PRIVATE / "inputs.tfvars.json", inputs)
    state["repair_cycles"] += 1
    write(RUN / "status.json", state)
    plan_file = PRIVATE / f"{attempt}.tfplan"
    command(f"{attempt}-validate", tf("validate", "-no-color"))
    command(f"{attempt}-plan", tf("plan", "-input=false", "-no-color",
        f"-var-file={PRIVATE / 'inputs.tfvars.json'}", f"-out={plan_file}"), timeout=600)
    rendered = json.loads(command(f"{attempt}-render", tf("show", "-json", str(plan_file))))
    validate_api_plan(rendered, approval)
    state.update(
        state="PLAN_REVIEWED", plan_file=str(plan_file), plan_sha256=sha(plan_file),
        source_sha256=source_hash(), reviewed_at=now(), accounting=accounting(state),
        approval_sha256=sha(PRIVATE / "approval.json"),
    )
    write(RUN / "status.json", state)
    print(json.dumps({"state": state["state"], "change": "operator /32 API access only"}))


def observe(attempt):
    approval = read(PRIVATE / "approval.json")
    cluster_id = f"/subscriptions/{approval['subscription_id']}/resourceGroups/{approval['resource_group']}/providers/Microsoft.ContainerService/managedClusters/{approval['cluster_name']}"
    clusters = arm_list(f"{attempt}-clusters",
        f"https://management.azure.com/subscriptions/{approval['subscription_id']}/resourceGroups/{approval['resource_group']}/providers/Microsoft.ContainerService/managedClusters?api-version={API}",
    )
    state = read(RUN / "status.json")
    if not clusters:
        group = json.loads(command(f"{attempt}-group", [
            "az", "group", "show", "--subscription", approval["subscription_id"],
            "--name", approval["resource_group"], "-o", "json",
        ]))
        resources = json.loads(command(f"{attempt}-resources", [
            "az", "resource", "list", "--subscription", approval["subscription_id"],
            "--resource-group", approval["resource_group"], "-o", "json",
        ]))
        local_state = json.loads(command(f"{attempt}-terraform-state", tf("show", "-json")))
        owned = [r for r in local_state["values"]["root_module"]["resources"] if r["address"] == "azapi_resource.group"]
        pending_receipt = state.get("last_apply_receipt")
        receipt_paths = [RUN / pending_receipt] if pending_receipt else list(RUN.glob("*-apply.json"))
        if len(receipt_paths) != 1:
            raise RuntimeError("Cannot identify the exact latest apply receipt.")
        receipts = [read(p) for p in receipt_paths]
        rejected = [r for r in receipts if r.get("completed_at") and r.get("exit_code") and "ERROR CODE: InvalidParameter" in r.get("stderr", "")]
        expected_id = f"/subscriptions/{approval['subscription_id']}/resourceGroups/{approval['resource_group']}"
        if (resources or len(owned) != 1 or owned[0]["values"]["id"] != expected_id
                or group["id"] != expected_id or group.get("tags", {}).get("run_id") != RUN.name
                or not rejected):
            raise RuntimeError("Cannot safely reconcile cluster absence/owned group against the rejected request.")
        state.update(
            state="CLUSTER_ABSENT_OWNED_GROUP", owned_group_id=group["id"],
            unknown_operations=0, observed_at=now(), accounting=accounting(state),
            last_reconciliation=attempt, native_cluster_id=None,
        )
        write(RUN / "status.json", state)
        print(json.dumps({"state": state["state"], "group": group["id"], "cluster_count": 0}))
        return
    if len(clusters) != 1 or clusters[0]["id"].lower() != cluster_id.lower():
        raise RuntimeError("Unexpected native cluster inventory.")
    actual = clusters[0]
    props = actual["properties"]
    native_state = props["provisioningState"]
    pools = props["agentPoolProfiles"]
    state.update(
        native_cluster_id=actual["id"], native_provisioning_state=native_state,
        native_pools=pools, observed_at=now(), accounting=accounting(state),
    )
    if native_state in {"Succeeded", "Failed", "Canceled"}:
        state["unknown_operations"] = 0
        state["state"] = "INFRA_CREATED_NOT_QUALIFIED" if native_state == "Succeeded" else "BLOCKED_INFRA_FAILURE"
    write(RUN / "status.json", state)
    print(json.dumps({
        "state": state["state"], "native_state": native_state,
        "pools": [{"name": p["name"], "count": p["count"], "security": p.get("securityProfile"), "runtime": p.get("workloadRuntime")} for p in pools],
        "accounting": state["accounting"],
    }, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["plan", "plan-api", "apply", "observe", "status"])
    parser.add_argument("--attempt")
    args = parser.parse_args()
    if args.action == "status":
        state = read(RUN / "status.json")
        state["accounting"] = accounting(state)
        print(json.dumps(state, indent=2))
    else:
        if not args.attempt:
            parser.error("--attempt is required and must be unique.")
        with writer():
            {"plan": plan, "plan-api": plan_api, "apply": apply, "observe": observe}[args.action](args.attempt)
