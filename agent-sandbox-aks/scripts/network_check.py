"""Record read-only API reachability without widening access or changing networking."""

import argparse
import ipaddress
import json
import socket

from infra import API
from run_state import PRIVATE, RUN, accounting, command, now, read, source_hash, write, writer


def diagnose(attempt):
    receipt = RUN / f"{attempt}-network.json"
    if receipt.exists():
        raise RuntimeError("Diagnostic attempt already exists.")
    approval = read(PRIVATE / "approval.json")
    cluster_id = f"/subscriptions/{approval['subscription_id']}/resourceGroups/{approval['resource_group']}/providers/Microsoft.ContainerService/managedClusters/{approval['cluster_name']}"
    actual = json.loads(command(f"{attempt}-arm", [
        "az", "rest", "--method", "get", "--url",
        f"https://management.azure.com{cluster_id}?api-version={API}",
    ]))
    host = actual["properties"]["fqdn"]
    ranges = actual["properties"]["apiServerAccessProfile"]["authorizedIPRanges"]
    egress = []
    for index, url in enumerate(("https://api.ipify.org?format=json", "https://checkip.amazonaws.com")):
        result = command(f"{attempt}-egress{index}", [
            "curl.exe", "--silent", "--show-error", "--connect-timeout", "10", "--max-time", "20", url,
        ]).strip()
        address = json.loads(result)["ip"] if index == 0 else result
        ipaddress.ip_address(address)
        egress.append({"source": url, "observed_ip": address, "allowlisted": f"{address}/32" in ranges})
    try:
        with socket.create_connection((host, 443), timeout=10) as connection:
            tcp = {"status": "CONNECTED", "remote_ip": connection.getpeername()[0]}
    except OSError as error:
        tcp = {"status": "FAILED", "error_type": type(error).__name__, "error": str(error)}
    evidence = {
        "at": now(), "cluster_id": cluster_id,
        "native_state": actual["properties"]["provisioningState"],
        "api_fqdn": host, "authorized_ip_ranges": ranges, "observed_egress": egress,
        "tcp": tcp, "source_sha256": source_hash(),
        "controls_changed": False, "inference": (
            "TCP failed and the current observed operator IP is not allowlisted. "
            "The destination-specific source address remains unverified."
            if tcp["status"] == "FAILED" and not all(e["allowlisted"] for e in egress)
            else "TCP remains unavailable with observed IPs allowed. The failing network hop and "
                 "destination-specific NAT address are not established; no corporate setting was changed."
            if tcp["status"] == "FAILED"
            else "TCP reachability alone does not prove Kubernetes authorization."
        ),
    }
    write(receipt, evidence)
    state = read(RUN / "status.json")
    state["network_diagnostic"] = receipt.name
    state["accounting"] = accounting(state)
    if not state["unknown_operations"]:
        if tcp["status"] == "FAILED":
            if state.get("blocker"):
                state.setdefault("blocker_history", []).append(state["blocker"])
            state.update(state="BLOCKED_NETWORK_PATH", blocker={
                "at": now(), "evidence": receipt.name, "reason": evidence["inference"],
                "next_action": "Owner/network support must restore or identify the permitted operator-to-AKS API path, then rerun this read-only check and qualification.",
            })
        else:
            state["state"] = "NETWORK_REACHABLE_NOT_QUALIFIED"
    write(RUN / "status.json", state)
    print(json.dumps(evidence, indent=2))
    return 0 if tcp["status"] == "CONNECTED" else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", required=True)
    args = parser.parse_args()
    with writer():
        code = diagnose(args.attempt)
    raise SystemExit(code)
