"""Apply and observe the owner-approved Bastion access change once."""

import argparse
import hashlib
import json
from datetime import UTC, datetime

from infra import GROUP, PRIVATE, ROOT, RUN, SUB, now, read, run, tf, write

APPROVAL = RUN / "bastion-approval.json"
PLAN = PRIVATE / "bastion.tfplan"


def accrue(state):
    hours = (datetime.now(UTC) - datetime.fromisoformat(state["cloud_started_at"])).total_seconds() / 3600
    amount = round(hours * 6, 2)
    state["accounting"].update(
        as_of=now(), cloud_conservative_allowance_usd=amount,
        historical_plus_new_allowance_usd=round(656.86 + amount, 2),
        terraform_applies=state["terraform_applies"],
    )
    write(RUN / "status.json", state)
    if amount + 18 > state["cloud_budget_usd"]:
        raise RuntimeError("Insufficient cloud allowance including three-hour verification reserve.")


def validate_plan(value):
    expected = {"azapi_resource.bastion", "azapi_resource.bastion_ip"}
    creates = set()
    for item in value["resource_changes"]:
        actions = item["change"]["actions"]
        address = item["address"]
        if actions == ["no-op"]:
            continue
        if actions == ["create"] and address in expected:
            creates.add(address)
        elif actions == ["update"] and address in {"azapi_resource.vnet", "azapi_resource.nsg"}:
            continue
        else:
            raise RuntimeError(f"Unapproved action: {address} {actions}")
    if creates != expected:
        raise RuntimeError("Bastion plan does not contain exactly the two approved new resources.")


def plan():
    state = read(RUN / "status.json")
    accrue(state)
    if APPROVAL.exists():
        raise RuntimeError("Bastion workflow already started; inspect its receipts.")
    write(APPROVAL, {
        "approved_at_recorded": now(),
        "authority": "Owner explicitly selected Azure Bastion Standard with native SSH and renewed runtime gate.",
        "direct_public_ssh": "PROHIBITED",
        "new_resources": ["Standard Bastion, two scale units", "Standard public IPv4 for Bastion"],
        "network_change": "Add AzureBastionSubnet /26; preserve existing lab subnet and outbound IP; do not restore external SSH rule.",
        "authentication": "Entra SSH certificate, target VM resource ID, pinned host key",
        "cloud_budget_usd_including_bastion": 200,
        "previous_runtime_gate_started_at": state["runtime_gate_started_at"],
        "previous_runtime_gate_outcome": "CAPPED during owner-decision hold; no Actor ran.",
        "new_runtime_gate": "Two hours from first working Entra SSH through Bastion; prior spend and counters retained.",
        "retail_reference": {"standard_gateway_usd_hour": 0.29, "source": "https://prices.azure.com/api/retail/prices",
                             "region": "northeurope", "excludes": "Public IP, transfer, taxes, discounts; not billed usage"},
        "documentation": [
            "https://learn.microsoft.com/azure/bastion/connect-vm-native-client-linux",
            "https://learn.microsoft.com/azure/templates/microsoft.network/2024-05-01/bastionhosts",
        ],
    })
    state.update(state="BASTION_PREPARATION", runtime_gate_started_at=None,
                 runtime_gate_basis="Owner renewed: first working Entra SSH through Bastion",
                 previous_runtime_gate_started_at=read(APPROVAL)["previous_runtime_gate_started_at"],
                 next_action="Plan approved Bastion access; no direct SSH or corporate policy changes.")
    write(RUN / "status.json", state)
    run("bastion-vm-preflight", [
        "az", "rest", "--method", "get", "--url",
        f"https://management.azure.com{state['outputs']['vm_id']['value']}/instanceView?api-version=2025-11-01",
    ])
    run("bastion-plan", tf("plan", "-input=false", "-no-color",
                         f"-var-file={PRIVATE / 'inputs.tfvars.json'}", f"-out={PLAN}"), timeout=600)
    value = json.loads(run("bastion-plan-json", tf("show", "-json", str(PLAN))))
    validate_plan(value)
    write(RUN / "bastion-plan-review.json", {
        "time": now(), "sha256": hashlib.sha256(PLAN.read_bytes()).hexdigest(),
        "changes": [{"address": x["address"], "actions": x["change"]["actions"]}
                    for x in value["resource_changes"] if x["change"]["actions"] != ["no-op"]],
    })
    print(json.dumps(read(RUN / "bastion-plan-review.json"), indent=2))


def apply():
    state = read(RUN / "status.json")
    accrue(state)
    review = read(RUN / "bastion-plan-review.json")
    if (datetime.now(UTC) - datetime.fromisoformat(review["time"])).total_seconds() > 900:
        raise RuntimeError("Plan review expired; prepare a fresh reviewed plan, not an apply replay.")
    if hashlib.sha256(PLAN.read_bytes()).hexdigest() != review["sha256"]:
        raise RuntimeError("Bastion plan changed.")
    if (RUN / "bastion-apply.json").exists():
        raise RuntimeError("Bastion apply already attempted; observe instead.")
    state.update(state="BASTION_APPLY_OUTCOME_UNKNOWN", terraform_applies=state["terraform_applies"] + 1)
    write(RUN / "status.json", state)
    run("bastion-apply", tf("apply", "-input=false", "-no-color", "-parallelism=2", str(PLAN)),
        timeout=2400, mutation=True)
    state.update(state="BASTION_APPLIED_AWAITING_READBACK")
    write(RUN / "status.json", state)
    observe()


def observe():
    state = read(RUN / "status.json")
    accrue(state)
    suffix = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    value = json.loads(run(f"bastion-observe-{suffix}", [
        "az", "rest", "--method", "get", "--url",
        f"https://management.azure.com/subscriptions/{SUB}/resourceGroups/{GROUP}/providers/Microsoft.Network/bastionHosts/substrate-mvp-bastion?api-version=2024-05-01",
    ]))
    if value["sku"]["name"] != "Standard" or not value["properties"]["enableTunneling"]:
        raise RuntimeError("Native Bastion SKU/tunneling differs from approved configuration.")
    nsg = json.loads(run(f"bastion-nsg-{suffix}", [
        "az", "network", "nsg", "show", "--subscription", SUB,
        "--resource-group", GROUP, "--name", "substrate-mvp-nsg", "-o", "json",
    ]))
    if nsg["securityRules"]:
        raise RuntimeError("Unexpected custom NSG rules; review before access.")
    state.update(
        state="BASTION_READY" if value["properties"]["provisioningState"] == "Succeeded" else "BASTION_PROVISIONING",
        bastion_id=value["id"], bastion_observed_at=now(), unknown_operations=0,
    )
    write(RUN / "status.json", state)
    print(json.dumps({"state": state["state"], "accounting": state["accounting"]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["plan", "apply", "observe"])
    args = parser.parse_args()
    {"plan": plan, "apply": apply, "observe": observe}[args.action]()
