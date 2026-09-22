"""Create the approved single-VM lab and retain command receipts without replay."""

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / ".local" / "deployment"
RUN = ROOT / ".artifacts" / "mvp-20260921"
TF = ROOT / "terraform"
SUB = "673af34d-6b28-41dc-bc7b-f507418045e6"
TENANT = "6ce4f237-667f-43f5-aafd-cbef954adf97"
OPERATOR = "e011e2a1-33d6-418f-b2ed-35f7d2281f45"
GROUP = "rg-substrate-mvp-20260921"


def now():
    return datetime.now(UTC).isoformat()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def run(name, args, timeout=600, mutation=False):
    receipt = RUN / f"{name}.json"
    if receipt.exists():
        raise RuntimeError(f"Existing operation {name}: inspect its result; never replay blindly.")
    write(receipt, {"time": now(), "state": "STARTED", "mutation": mutation, "command": args})
    completed = subprocess.run(
        [shutil.which(args[0]) or args[0], *args[1:]], cwd=ROOT,
        text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout,
    )
    write(receipt, {
        "time": now(), "state": "COMMAND_RETURNED", "mutation": mutation,
        "command": args, "exit_code": completed.returncode,
        "stdout": completed.stdout, "stderr": completed.stderr,
    })
    if completed.returncode:
        raise RuntimeError(f"{name} failed ({completed.returncode}): {completed.stderr}\n{completed.stdout[-4000:]}")
    return completed.stdout


def tf(*args):
    return ["terraform", f"-chdir={TF}", *args]


def plan():
    PRIVATE.mkdir(parents=True, exist_ok=True)
    if (RUN / "status.json").exists():
        raise RuntimeError("A run already exists. Inspect it before further work.")
    write(RUN / "status.json", {
        "run_id": "mvp-20260921", "state": "PREFLIGHT", "started_at": now(),
        "approval": "Owner approved the exact single-VM scope and additive pilot budget in chat.",
        "cloud_budget_usd": 200, "model_budget_usd": 1000,
        "historical_cloud_allowance_usd": 656.86,
        "prior_combined_cloud_ceiling_superseded_for_new_pilot": True,
        "runtime_gate_hours": 2, "runtime_gate_started_at": None,
        "maximum_vcpu": 8, "maximum_workers": 1, "maximum_actors": 2,
        "terraform_applies": 0, "model_requests": 0,
        "checks": {f"C{i:02}": "NOT_RUN" for i in range(1, 7)},
    })
    account = json.loads(run("account", [
        "az", "account", "show", "--query", "{id:id,tenantId:tenantId,state:state}", "-o", "json",
    ]))
    if account != {"id": SUB, "tenantId": TENANT, "state": "Enabled"}:
        raise RuntimeError("Azure account differs from the approved identity scope.")
    exists = run("group-before", [
        "az", "group", "exists", "--subscription", SUB, "--name", GROUP, "-o", "json",
    ])
    if json.loads(exists):
        raise RuntimeError("The new resource group already exists; do not adopt it.")
    role = json.loads(run("login-role", [
        "az", "role", "definition", "list", "--subscription", SUB,
        "--name", "Virtual Machine Administrator Login", "--query", "[0].id", "-o", "json",
    ]))
    if not isinstance(role, str) or not role.endswith("/1c0163c0-47e6-4577-8991-ea5c82e286e4"):
        raise RuntimeError("Unexpected VM login role.")
    key = PRIVATE / "discarded-bootstrap"
    run("bootstrap-public-key", ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)])
    public_key = key.with_suffix(".pub").read_text().strip()
    key.unlink()
    key.with_suffix(".pub").unlink()
    write(PRIVATE / "inputs.tfvars.json", {
        "subscription_id": SUB, "tenant_id": TENANT, "operator_object_id": OPERATOR,
        "operator_cidr": "178.17.15.151/32", "bootstrap_public_key": public_key,
        "vm_admin_login_role_id": role,
    })
    run("terraform-init", tf("init", "-input=false", "-no-color",
                            f"-backend-config=path={PRIVATE / 'terraform.tfstate'}"), timeout=900)
    run("terraform-validate", tf("validate", "-no-color"))
    run("terraform-plan", tf("plan", "-input=false", "-no-color",
                            f"-var-file={PRIVATE / 'inputs.tfvars.json'}",
                            f"-out={PRIVATE / 'create.tfplan'}"), timeout=900)
    value = json.loads(run("terraform-plan-json", tf("show", "-json", str(PRIVATE / "create.tfplan"))))
    changes = value["resource_changes"]
    expected = {f"azapi_resource.{x}" for x in
                ("group", "nsg", "vnet", "public_ip", "nic", "vm", "entra_ssh", "operator_login")}
    if {x["address"] for x in changes} != expected:
        raise RuntimeError("Unexpected Terraform resource set.")
    if any(x["change"]["actions"] != ["create"] for x in changes):
        raise RuntimeError("Plan is not create-only.")
    state = read(RUN / "status.json")
    state.update({
        "state": "PLAN_REVIEWED", "plan_sha256": hashlib.sha256((PRIVATE / "create.tfplan").read_bytes()).hexdigest(),
        "resource_count": len(changes), "reviewed_at": now(),
    })
    write(RUN / "status.json", state)
    print(json.dumps(state, indent=2))


def apply():
    state = read(RUN / "status.json")
    if state["state"] != "PLAN_REVIEWED":
        raise RuntimeError("Apply is not authorized for this state; reconcile instead of repeating.")
    plan_file = PRIVATE / state.get("plan_file", "create.tfplan")
    if hashlib.sha256(plan_file.read_bytes()).hexdigest() != state["plan_sha256"]:
        raise RuntimeError("The reviewed plan changed.")
    attempt = state["terraform_applies"] + 1
    if attempt > 2:
        raise RuntimeError("The two planned infrastructure attempts are exhausted.")
    state.update(state="APPLY_OUTCOME_UNKNOWN", terraform_applies=attempt)
    state.setdefault("cloud_started_at", now())
    write(RUN / "status.json", state)
    receipt = "terraform-apply" if attempt == 1 else "terraform-apply-standard-security-repair"
    run(receipt, tf("apply", "-input=false", "-no-color", "-parallelism=2",
                   str(plan_file)), timeout=2400, mutation=True)
    state.update(state="APPLY_COMPLETED_AWAITING_READBACK")
    write(RUN / "status.json", state)
    outputs = json.loads(run("terraform-outputs", tf("output", "-json")))
    actual = json.loads(run("vm-readback", [
        "az", "rest", "--method", "get", "--url",
        f"https://management.azure.com{outputs['vm_id']['value']}?api-version=2025-11-01",
    ]))
    if actual["properties"]["provisioningState"] != "Succeeded":
        raise RuntimeError("VM provisioning is not independently verified.")
    state.update(state="VM_PROVISIONED_NOT_RUNTIME_QUALIFIED", outputs=outputs, verified_at=now())
    write(RUN / "status.json", state)
    print(json.dumps({"state": state["state"], "outputs": outputs}, indent=2))


def observe():
    state = read(RUN / "status.json")
    accepted = read(RUN / "terraform-apply-standard-security-repair.json")
    if accepted.get("exit_code") != 0:
        raise RuntimeError("No successful apply to observe.")
    outputs = json.loads(read(RUN / "terraform-outputs.json")["stdout"])
    vm_id = outputs["vm_id"]["value"]
    prefix = f"observe-{datetime.now(UTC).strftime('%H%M%S')}"
    actual = json.loads(run(prefix, [
        "az", "rest", "--method", "get", "--url",
        f"https://management.azure.com{vm_id}?api-version=2025-11-01",
    ]))
    verified = actual["properties"]["provisioningState"] == "Succeeded"
    state.update(
        state="VM_PROVISIONED_NOT_RUNTIME_QUALIFIED" if verified else "APPLY_COMPLETED_AWAITING_READBACK",
        outputs=outputs, observed_at=now(), unknown_operations=0,
        native_vm_id=actual["properties"]["vmId"],
        native_provisioning_state=actual["properties"]["provisioningState"],
        corporate_extensions=[x["name"] for x in actual.get("resources", [])
                              if x["name"] != "AADSSHLoginForLinux"],
    )
    write(RUN / "status.json", state)
    print(json.dumps({"state": state["state"], "native_state": state["native_provisioning_state"]}))


def access():
    raise RuntimeError(
        "Direct public SSH is prohibited by owner-confirmed policy. "
        "Use the approved Azure Bastion Standard path once implemented and qualified."
    )


def record_network_block():
    state = read(RUN / "status.json")
    prefix = f"network-block-{datetime.now(UTC).strftime('%H%M%S')}"
    nsg = json.loads(run(f"{prefix}-nsg", [
        "az", "network", "nsg", "show", "--subscription", SUB,
        "--resource-group", GROUP, "--name", "substrate-mvp-nsg", "-o", "json",
    ]))
    activity = json.loads(run(f"{prefix}-activity", [
        "az", "monitor", "activity-log", "list", "--subscription", SUB,
        "--resource-id", nsg["id"], "--offset", "2h", "--max-events", "100", "-o", "json",
    ]))
    resources = json.loads(run(f"{prefix}-resources", [
        "az", "resource", "list", "--subscription", SUB,
        "--resource-group", GROUP, "-o", "json",
    ]))
    if nsg["securityRules"]:
        raise RuntimeError("NSG changed; reassess current access before recording the old blocker.")
    elapsed_hours = (datetime.now(UTC) - datetime.fromisoformat(state["cloud_started_at"])).total_seconds() / 3600
    allowance = round(elapsed_hours * 6, 2)
    host_receipt = read(RUN / "native-bootstrap-facts-file.json")
    successful_writes = [
        {"time": item["eventTimestamp"], "caller": item.get("caller"), "resource_id": item["resourceId"]}
        for item in activity
        if item.get("operationName", {}).get("value") == "Microsoft.Network/networkSecurityGroups/write"
        and item.get("status", {}).get("value") == "Succeeded"
    ]
    state.update(
        state="BLOCKED", stopped_at=now(), unknown_operations=0,
        blocker="The approved SSH /32 rule disappeared after an external-principal NSG write. Do not restore it or route around it without owner resolution of that policy/automation.",
        native_kernel="6.17.0-1022-azure", cgroup_mode="cgroup2fs", kvm_create_vm="PASS",
        entra_ssh="FAIL_CONNECTION_TIMEOUT", runtime_gate_started_at=host_receipt["time"],
        runtime_gate_basis="First verified remote KVM execution; clock is preserved across this owner-decision hold.",
        nsg_successful_writes=successful_writes,
        owned_resource_ids=[x["id"] for x in resources if (x.get("tags") or {}).get("run_id") == "mvp-20260921"],
        other_group_resource_ids=[x["id"] for x in resources if (x.get("tags") or {}).get("run_id") != "mvp-20260921"],
        accounting={
            "as_of": now(), "billed_usage_usd": None,
            "cloud_conservative_allowance_usd": allowance,
            "conservative_hourly_allowance_usd": 6,
            "basis": "Conservative elapsed-time reservation, not a retail quote or billed-usage measurement.",
            "historical_plus_new_allowance_usd": round(656.86 + allowance, 2),
            "cloud_budget_usd": 200, "model_requests": 0,
            "terraform_applies": 2, "azure_run_command_invocations": 2,
            "image_builds": 0, "workers": 0, "actors": 0,
        },
        retention="VM, disk, network and identity retained as approved; no cleanup or automation scheduled.",
        next_action="Owner resolves the external NSG policy/automation. Re-read network state and limits before further execution; never replay accepted deployment or host-fact commands.",
        evidence_prefix=prefix,
    )
    state["checks"]["C01"] = "BLOCKED"
    write(RUN / "status.json", state)
    print(json.dumps({
        "state": state["state"], "kvm_create_vm": "PASS",
        "blocker": state["blocker"], "accounting": state["accounting"],
        "nsg_successful_writes": successful_writes,
    }, indent=2))


def repair_plan():
    state = read(RUN / "status.json")
    failed = read(RUN / "terraform-apply.json")
    prefix = f"repair-{datetime.now(UTC).strftime('%H%M%S')}"
    if (state["terraform_applies"] != 1 or failed.get("exit_code") != 1
            or "UseStandardSecurityType" not in failed.get("stderr", "")):
        raise RuntimeError("This repair only applies to the observed, rejected securityType request.")
    actual = json.loads(run(f"{prefix}-resource-readback", [
        "az", "resource", "list", "--subscription", SUB, "--resource-group", GROUP, "-o", "json",
    ]))
    expected_resources = {
        ("Microsoft.Network/networkSecurityGroups", "substrate-mvp-nsg"),
        ("Microsoft.Network/publicIPAddresses", "substrate-mvp-ip"),
        ("Microsoft.Network/virtualNetworks", "substrate-mvp-vnet"),
        ("Microsoft.Network/networkInterfaces", "substrate-mvp-nic"),
    }
    if {(x["type"], x["name"]) for x in actual} != expected_resources:
        raise RuntimeError("Reconcile unexpected compute resources before proceeding.")
    plan_file = PRIVATE / "standard-security-repair.tfplan"
    run(f"{prefix}-validate", tf("validate", "-no-color"))
    run(f"{prefix}-plan", tf("plan", "-input=false", "-no-color",
                         f"-var-file={PRIVATE / 'inputs.tfvars.json'}", f"-out={plan_file}"))
    planned = json.loads(run(f"{prefix}-plan-json", tf("show", "-json", str(plan_file))))
    changes = {item["address"]: item["change"]["actions"] for item in planned["resource_changes"]}
    expected = {f"azapi_resource.{name}": ["create"] for name in ("vm", "entra_ssh", "operator_login")}
    expected.update({f"azapi_resource.{name}": ["no-op"] for name in ("group", "nsg", "vnet", "public_ip", "nic")})
    if changes != expected:
        raise RuntimeError(f"Unexpected repair plan: {changes}")
    state.update(
        state="PLAN_REVIEWED", plan_file=plan_file.name,
        plan_sha256=hashlib.sha256(plan_file.read_bytes()).hexdigest(),
        reviewed_at=now(), repair_cycles=1, unknown_operations=0,
        first_apply_outcome="Five owned foundation resources created; VM rejected and independently absent.",
    )
    write(RUN / "status.json", state)
    print(json.dumps({"state": state["state"], "repair_plan": changes}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["plan", "repair-plan", "apply", "observe", "access", "record-network-block"])
    arguments = parser.parse_args()
    if arguments.action == "plan":
        plan()
    elif arguments.action == "repair-plan":
        repair_plan()
    elif arguments.action == "observe":
        observe()
    elif arguments.action == "access":
        access()
    elif arguments.action == "record-network-block":
        record_network_block()
    else:
        apply()
