"""Start the approved stopped AKS cluster exactly once and reconcile its state."""

import argparse
import json

from infra import API
from run_state import CONFIG, PRIVATE, RUN, accounting, check_limits, command, now, read, write, writer


def cluster_url(approval):
    return (
        f"https://management.azure.com/subscriptions/{approval['subscription_id']}"
        f"/resourceGroups/{approval['resource_group']}/providers/Microsoft.ContainerService"
        f"/managedClusters/{approval['cluster_name']}"
    )


def read_cluster(attempt, approval):
    return json.loads(command(f"{attempt}-arm", [
        "az", "rest", "--method", "get", "--url", f"{cluster_url(approval)}?api-version={API}",
    ]))


def start(attempt):
    approval = read(PRIVATE / "approval.json")
    state = read(RUN / "status.json")
    if not approval.get("restart_amendment") or state.get("restart_attempts", 0):
        raise RuntimeError("A unique, approved restart is required; never replay an accepted request.")
    if state["unknown_operations"] or state["terraform_applies"] != 4 or state["model_requests"]:
        raise RuntimeError("Existing run has unresolved side effects or differs from owner approval.")
    if state["deployment_attempts"] >= CONFIG["limits"]["deployment_attempts"]:
        raise RuntimeError("CAPPED: deployment attempts exhausted.")
    if accounting(state)["conservative_cloud_usd"] + 12 + 4 * CONFIG["limits"]["conservative_usd_per_hour"] >= CONFIG["limits"]["cloud_usd"]:
        raise RuntimeError("CAPPED: insufficient conservative remaining cloud allowance for approved window.")
    actual = read_cluster(attempt, approval)
    if actual["properties"]["powerState"]["code"] != "Stopped" or actual["properties"]["provisioningState"] != "Succeeded":
        raise RuntimeError("AKS must be stopped and reconciled before this start operation.")
    if actual["id"].lower() != cluster_url(approval).removeprefix("https://management.azure.com").lower():
        raise RuntimeError("Unexpected cluster identity.")
    if actual["properties"]["networkProfile"]["networkDataplane"] != "cilium":
        raise RuntimeError("Cluster dataplane differs from approved infrastructure.")
    state.update(
        previous_execution_window={"started_at": state["cloud_started_at"], "limit_hours": CONFIG["limits"]["execution_hours"], "closed": "2026-09-22"},
        execution_window_started_at=now(), restart_attempts=1,
        state="RESTART_RESERVED", accounting=accounting(state),
    )
    write(RUN / "status.json", state)
    command(f"{attempt}-start", [
        "az", "rest", "--method", "post", "--url", f"{cluster_url(approval)}/start?api-version={API}",
    ], timeout=3000, mutation=True)
    observe(f"{attempt}-readback")


def observe(attempt):
    approval = read(PRIVATE / "approval.json")
    actual = read_cluster(attempt, approval)
    props = actual["properties"]
    state = read(RUN / "status.json")
    state.update(
        observed_at=now(), native_provisioning_state=props["provisioningState"],
        native_pools=props["agentPoolProfiles"], native_power_state=props["powerState"]["code"],
        accounting=accounting(state),
    )
    if props["provisioningState"] == "Succeeded" and props["powerState"]["code"] == "Running":
        state.update(unknown_operations=0, state="RESTARTED_NOT_QUALIFIED")
    elif props["provisioningState"] in {"Failed", "Canceled"}:
        state.update(unknown_operations=0, state="BLOCKED_RESTART_FAILURE")
    else:
        state["state"] = "RESTART_OUTCOME_PENDING"
    write(RUN / "status.json", state)
    print(json.dumps({
        "state": state["state"], "power": props["powerState"]["code"],
        "pools": [{"name": p["name"], "count": p["count"], "power": p.get("powerState")} for p in props["agentPoolProfiles"]],
        "accounting": state["accounting"],
    }, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "observe"])
    parser.add_argument("--attempt", required=True)
    args = parser.parse_args()
    with writer():
        {"start": start, "observe": observe}[args.action](args.attempt)
