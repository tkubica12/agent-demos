"""Qualify ARM/VMSS, Entra access and actual managed Kata guest execution."""

import argparse
import base64
import json

from infra import API
from run_state import PRIVATE, RUN, accounting, check_limits, command, now, read, write, writer

KUBECONFIG = PRIVATE / "kubeconfig"
NAMESPACE = "aks-sandbox-qualification"
IMAGE = "mcr.microsoft.com/aks/fundamental/base-ubuntu:v0.0.11"


def kubectl(*args):
    return ["kubectl", "--kubeconfig", str(KUBECONFIG), "--request-timeout=60s", *args]


def inventory(attempt):
    approval = read(PRIVATE / "approval.json")
    group_id = f"/subscriptions/{approval['subscription_id']}/resourceGroups/{approval['resource_group']}"
    cluster_id = f"{group_id}/providers/Microsoft.ContainerService/managedClusters/{approval['cluster_name']}"
    actual = json.loads(command(f"{attempt}-arm", [
        "az", "rest", "--method", "get", "--url",
        f"https://management.azure.com{cluster_id}?api-version={API}",
    ]))
    props = actual["properties"]
    if props["provisioningState"] != "Succeeded" or not props["disableLocalAccounts"]:
        raise RuntimeError("Cluster is not ready with local accounts disabled.")
    if not props["aadProfile"]["enableAzureRBAC"] or props["networkProfile"]["networkDataplane"] != "cilium":
        raise RuntimeError("Required Entra/Cilium settings are missing.")
    pools = {p["name"]: p for p in props["agentPoolProfiles"]}
    if set(pools) != {"platform", "kata"}:
        raise RuntimeError("Unexpected pools.")
    vmss = json.loads(command(f"{attempt}-vmss", [
        "az", "vmss", "list", "--subscription", approval["subscription_id"],
        "--resource-group", approval["resource_group"] + "-nodes", "-o", "json",
    ]))
    validate_vmss(vmss)
    for name, pool in pools.items():
        if pool["count"] != 1 or pool["vmSize"] != "Standard_D4s_v3" or pool.get("enableAutoScaling", False):
            raise RuntimeError("Actual pool capacity differs from approval.")
        trusted = name == "platform"
        security = pool["securityProfile"]
        if security["enableSecureBoot"] != trusted or security["enableVTPM"] != trusted or security["sshAccess"] != "Disabled":
            raise RuntimeError("Actual pool security differs from approval.")
        if pool["workloadRuntime"] != ("OCIContainer" if trusted else "KataVmIsolation"):
            raise RuntimeError("Actual runtime differs from approval.")
    if not KUBECONFIG.exists():
        credentials = json.loads(command(f"{attempt}-userconfig", [
            "az", "rest", "--method", "post", "--url",
            f"https://management.azure.com{cluster_id}/listClusterUserCredential?api-version={API}&format=exec",
        ], private_output=True))
        configs = credentials["kubeconfigs"]
        if len(configs) != 1:
            raise RuntimeError("Expected one user kubeconfig.")
        content = base64.b64decode(configs[0]["value"], validate=True).decode()
        if "exec:" not in content or "client-key-data:" in content or "token:" in content:
            raise RuntimeError("Refusing a non-exec or static-credential kubeconfig.")
        KUBECONFIG.write_text(content, encoding="utf-8")
        command(f"{attempt}-kubelogin", ["kubelogin", "convert-kubeconfig", "-l", "azurecli", "--kubeconfig", str(KUBECONFIG)])
    nodes = json.loads(command(f"{attempt}-nodes", kubectl("get", "nodes", "-o", "json")))
    validate_nodes(nodes["items"])
    command(f"{attempt}-runtime", kubectl("get", "runtimeclass", "kata-vm-isolation", "-o", "json"))
    command(f"{attempt}-systempods", kubectl("get", "pods", "-n", "kube-system", "-o", "json"))
    cilium = json.loads(command(f"{attempt}-cilium", kubectl("get", "daemonset", "cilium", "-n", "kube-system", "-o", "json")))
    if cilium["status"]["desiredNumberScheduled"] != 2 or cilium["status"]["numberReady"] != 2:
        raise RuntimeError("Cilium is not Ready on both nodes.")
    state = read(RUN / "status.json")
    state.update(native_inventory=attempt, native_inventory_at=now(), accounting=accounting(state))
    write(RUN / "status.json", state)
    print(json.dumps({"inventory": "VERIFIED", "nodes": 2, "vcpu": 8, "runtime_execution": "NOT_YET_VERIFIED"}))


def validate_vmss(items):
    if len(items) != 2:
        raise RuntimeError("Expected two VM scale sets.")
    seen = set()
    for item in items:
        pool = item.get("tags", {}).get("aks-managed-poolName")
        if pool not in {"platform", "kata"} or pool in seen:
            raise RuntimeError("Unexpected VMSS pool identity.")
        seen.add(pool)
        if item["sku"]["capacity"] != 1 or item["sku"]["name"] != "Standard_D4s_v3":
            raise RuntimeError("Unexpected VMSS capacity.")
        profile = item["virtualMachineProfile"]
        security = profile.get("securityProfile") or {}
        uefi = security.get("uefiSettings") or {}
        trusted = pool == "platform"
        if bool(uefi.get("secureBootEnabled")) != trusted or bool(uefi.get("vTpmEnabled")) != trusted:
            raise RuntimeError("VMSS Trusted Launch readback mismatch.")
        if trusted and security.get("securityType") != "TrustedLaunch":
            raise RuntimeError("Platform VMSS is not Trusted Launch.")
        for nic in profile["networkProfile"]["networkInterfaceConfigurations"]:
            if any(ip.get("publicIPAddressConfiguration") for ip in nic["ipConfigurations"]):
                raise RuntimeError("Node public IP configuration found.")


def validate_nodes(nodes):
    if len(nodes) != 2:
        raise RuntimeError("Expected exactly two nodes.")
    seen = set()
    for node in nodes:
        labels = node["metadata"]["labels"]
        pool = labels.get("kubernetes.azure.com/agentpool")
        if pool not in {"platform", "kata"} or pool in seen:
            raise RuntimeError("Unexpected node pool identity.")
        seen.add(pool)
        if labels.get("demo.sandbox/role") != ("platform" if pool == "platform" else "sandbox"):
            raise RuntimeError("Node placement label mismatch.")
        if node["status"]["capacity"]["cpu"] != "4":
            raise RuntimeError("Node CPU capacity mismatch.")
        if not any(c["type"] == "Ready" and c["status"] == "True" for c in node["status"]["conditions"]):
            raise RuntimeError("Node is not Ready.")
        if pool == "kata" and not any(
            t["key"] == "sandbox" and t.get("value") == "kata" and t["effect"] == "NoSchedule"
            for t in node["spec"].get("taints", [])
        ):
            raise RuntimeError("Kata node lacks the dedicated taint.")


def probe_manifest():
    labels = {"demo.sandbox/run": RUN.name}
    objects = [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NAMESPACE, "labels": {
            **labels, "pod-security.kubernetes.io/enforce": "restricted",
            "pod-security.kubernetes.io/enforce-version": "v1.36",
        }}},
        {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": {
            "name": "default-deny", "namespace": NAMESPACE, "labels": labels,
        }, "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"], "ingress": [], "egress": []}},
    ]
    for pool in ("platform", "kata"):
        spec = {
            "restartPolicy": "Never", "automountServiceAccountToken": False,
            "nodeSelector": {"kubernetes.azure.com/agentpool": pool},
            "securityContext": {"runAsNonRoot": True, "runAsUser": 1000, "seccompProfile": {"type": "RuntimeDefault"}},
            "containers": [{
                "name": "kernel", "image": IMAGE,
                "command": ["/bin/sh", "-ec", "uname -r; test ! -e /var/run/secrets/kubernetes.io/serviceaccount/token; id -u"],
                "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}},
                "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"cpu": "1", "memory": "512Mi"}},
            }],
        }
        if pool == "kata":
            spec.update(runtimeClassName="kata-vm-isolation", tolerations=[{"key": "sandbox", "operator": "Equal", "value": "kata", "effect": "NoSchedule"}])
        objects.append({"apiVersion": "v1", "kind": "Pod", "metadata": {
            "name": f"kernel-{pool}", "namespace": NAMESPACE, "labels": labels,
        }, "spec": spec})
    return {"apiVersion": "v1", "kind": "List", "items": objects}


def create(attempt):
    state = read(RUN / "status.json")
    check_limits(state)
    if not state.get("native_inventory"):
        raise RuntimeError("Native infrastructure inventory is required.")
    existing = json.loads(command(f"{attempt}-absence", kubectl("get", "namespace", NAMESPACE, "--ignore-not-found", "-o", "json")) or "{}")
    if existing:
        raise RuntimeError("Qualification namespace already exists; observe, never replay creation.")
    manifest = PRIVATE / "qualification.json"
    write(manifest, probe_manifest())
    command(f"{attempt}-create", kubectl("create", "-f", str(manifest), "-o", "json"), mutation=True)
    observe(attempt)


def observe(attempt):
    resources = []
    for item in probe_manifest()["items"]:
        namespace = ["-n", item["metadata"]["namespace"]] if "namespace" in item["metadata"] else []
        actual = json.loads(command(f"{attempt}-read-{item['kind'].lower()}-{item['metadata']['name']}",
            kubectl("get", item["kind"], item["metadata"]["name"], *namespace, "-o", "json")))
        if actual["metadata"].get("labels", {}).get("demo.sandbox/run") != RUN.name:
            raise RuntimeError("Qualification resource ownership mismatch.")
        if item["kind"] == "NetworkPolicy":
            if actual["spec"].get("podSelector") != {} or actual["spec"].get("egress") or set(actual["spec"]["policyTypes"]) != {"Ingress", "Egress"}:
                raise RuntimeError("Qualification default-deny policy mismatch.")
        if item["kind"] == "Pod":
            expected = item["spec"]
            if (actual["spec"].get("runtimeClassName") != expected.get("runtimeClassName")
                    or any(actual["spec"]["nodeSelector"].get(key) != value for key, value in expected["nodeSelector"].items())
                    or actual["spec"].get("automountServiceAccountToken") is not False):
                raise RuntimeError("Admitted probe runtime, placement or identity differs from the requested boundary.")
        resources.append({"kind": item["kind"], "name": item["metadata"]["name"], "uid": actual["metadata"]["uid"]})
    state = read(RUN / "status.json")
    state.update(unknown_operations=0, qualification_resources=resources, state="GUEST_PROBES_CREATED")
    write(RUN / "status.json", state)
    kernels = {}
    for pool in ("platform", "kata"):
        command(f"{attempt}-wait-{pool}", kubectl("wait", "-n", NAMESPACE, f"pod/kernel-{pool}", "--for=jsonpath={.status.phase}=Succeeded", "--timeout=300s"), timeout=330)
        result = command(f"{attempt}-kernel-{pool}", kubectl("logs", "-n", NAMESPACE, f"kernel-{pool}")).strip().splitlines()
        if len(result) != 2 or result[1] != "1000":
            raise RuntimeError("Unexpected kernel execution output.")
        kernels[pool] = result[0]
    if kernels["kata"] == kernels["platform"] or "mshv" not in kernels["kata"]:
        raise RuntimeError("Distinct managed Kata guest kernel was not demonstrated.")
    state.update(state="C01_PASSED", kernel_execution=kernels, accounting=accounting(state))
    state["checks"]["C01"] = {"status": "PASS", "at": now(), "inventory": state["native_inventory"], "guest_evidence": attempt}
    write(RUN / "status.json", state)
    print(json.dumps({"C01": "PASS", "kernels": kernels, "resource_uids": resources}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["inventory", "create", "observe"])
    parser.add_argument("--attempt", required=True)
    args = parser.parse_args()
    with writer():
        {"inventory": inventory, "create": create, "observe": observe}[args.action](args.attempt)
