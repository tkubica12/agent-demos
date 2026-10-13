"""Qualify native Agent Sandbox and Azure Disk PVC on AKS managed Kata."""

import argparse
import json
import time

from qualify import kubectl
from run_state import PRIVATE, RUN, check_limits, command, now, read, write, writer

NS = "aks-agent-sandbox-demo"
TEMPLATE = "kata-workspace"


def obj(kind, name, spec, api="v1"):
    return {"apiVersion": api, "kind": kind, "metadata": {"namespace": NS, "name": name}, "spec": spec}


def template():
    return obj("SandboxTemplate", TEMPLATE, {
        "networkPolicyManagement": "Unmanaged",
        "podTemplate": {"metadata": {"labels": {"demo.sandbox/role": "agent"}},
            "spec": {
                "nodeSelector": {"demo.sandbox/role": "sandbox"},
                "tolerations": [{"key": "sandbox", "operator": "Equal", "value": "kata", "effect": "NoSchedule"}],
                "runtimeClassName": "kata-vm-isolation",
                "automountServiceAccountToken": False,
                "enableServiceLinks": False,
                "securityContext": {"runAsUser": 1000, "runAsGroup": 1000, "fsGroup": 1000,
                                    "runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}},
                "containers": [{"name": "agent", "image": "docker.io/library/python:3.13.7-slim",
                    "command": ["python3", "-u", "-m", "http.server", "8080", "--directory", "/workspace"],
                    "resources": {"requests": {"cpu": "100m", "memory": "128Mi"},
                                  "limits": {"cpu": "1", "memory": "512Mi"}},
                    "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
                    "volumeMounts": [{"name": "workspace", "mountPath": "/workspace"}],
                    "readinessProbe": {"tcpSocket": {"port": 8080}, "periodSeconds": 2}}],
            }},
        "volumeClaimTemplates": [{"metadata": {"name": "workspace"}, "spec": {
            "accessModes": ["ReadWriteOnce"], "storageClassName": "managed-csi",
            "resources": {"requests": {"storage": "1Gi"}}}}],
    }, "extensions.agents.x-k8s.io/v1beta1")


def apply(attempt, resource):
    manifest = PRIVATE / f"{attempt}-manifest.json"
    write(manifest, resource)
    command(f"{attempt}-apply", kubectl("apply", "-f", str(manifest)), mutation=True)


def get(attempt, kind, name=None):
    args = ["get", kind, "-n", NS]
    if name:
        args.append(name)
    return json.loads(command(f"{attempt}-{kind}-{name or 'all'}", kubectl(*args, "-o", "json")))


def prepare(attempt):
    state = read(RUN / "status.json")
    check_limits(state)
    if state.get("platform_install", {}).get("state") != "VERIFIED":
        raise RuntimeError("Native controller must be verified before workloads.")
    if command(f"{attempt}-ns-absence", kubectl("get", "ns", NS, "--ignore-not-found", "-o", "name")).strip():
        raise RuntimeError("Refusing to adopt an existing demo namespace.")
    apply(f"{attempt}-namespace", {"apiVersion": "v1", "kind": "Namespace",
        "metadata": {"name": NS, "labels": {"demo.sandbox/owned": "true"}}})
    namespace = json.loads(command(f"{attempt}-ns-readback", kubectl("get", "ns", NS, "-o", "json")))
    state = read(RUN / "status.json")
    state["demo_resources"] = {"namespace_uid": namespace["metadata"]["uid"], "network_policy_uid": None}
    state["unknown_operations"] = 0
    write(RUN / "status.json", state)
    apply(f"{attempt}-deny", obj("NetworkPolicy", "default-deny",
        {"podSelector": {}, "policyTypes": ["Ingress", "Egress"], "ingress": [], "egress": []},
        "networking.k8s.io/v1"))
    policy = get(attempt, "networkpolicy", "default-deny")
    state = read(RUN / "status.json")
    state["demo_resources"]["network_policy_uid"] = policy["metadata"]["uid"]
    state["unknown_operations"] = 0
    write(RUN / "status.json", state)
    apply(f"{attempt}-template", template())
    created = get(attempt, "sandboxtemplate", TEMPLATE)
    if created["spec"]["podTemplate"]["spec"]["runtimeClassName"] != "kata-vm-isolation":
        raise RuntimeError("Native template did not retain managed Kata.")
    state = read(RUN / "status.json")
    state["demo_resources"]["template_uid"] = created["metadata"]["uid"]
    state["unknown_operations"] = 0
    state["state"] = "DEMO_NAMESPACE_DENIED_TEMPLATE_READY"
    write(RUN / "status.json", state)
    print(json.dumps(state["demo_resources"]))


def probe(attempt):
    state = read(RUN / "status.json")
    check_limits(state)
    if not state.get("demo_resources", {}).get("template_uid"):
        raise RuntimeError("Demo template has not been observed.")
    existing = get(attempt, "sandbox")
    if existing["items"]:
        raise RuntimeError("Do not create an additional sandbox before accounting for existing guests.")
    spec = template()["spec"]
    apply(attempt, obj("Sandbox", "storage-probe", {
        "podTemplate": spec["podTemplate"], "volumeClaimTemplates": spec["volumeClaimTemplates"],
    }, "agents.x-k8s.io/v1beta1"))
    deadline = time.monotonic() + 260
    while time.monotonic() < deadline:
        sandbox = get(f"{attempt}-{int(time.monotonic())}", "sandbox", "storage-probe")
        pods = get(f"{attempt}-{int(time.monotonic())}", "pods")["items"]
        owned = [p for p in pods for o in p["metadata"].get("ownerReferences", [])
                 if o["uid"] == sandbox["metadata"]["uid"]]
        if owned and any(c["type"] == "Ready" and c["status"] == "True"
                         for c in owned[0].get("status", {}).get("conditions", [])):
            pod = owned[0]
            if pod["spec"]["runtimeClassName"] != "kata-vm-isolation" or "kata" not in pod["spec"]["nodeName"]:
                raise RuntimeError("Sandbox guest placed outside the approved Kata node.")
            claims = get(attempt, "pvc")["items"]
            if len(claims) != 1 or claims[0]["status"]["phase"] != "Bound":
                raise RuntimeError("Native sandbox PVC did not bind.")
            output = command(f"{attempt}-guest-kernel", kubectl("exec", "-n", NS, pod["metadata"]["name"], "--",
                "python3", "-c", "import os;print(os.uname().release)")).strip()
            if output == state["kernel_execution"]["platform"]:
                raise RuntimeError("Sandbox runs platform kernel, not Kata guest kernel.")
            state = read(RUN / "status.json")
            state["storage_probe"] = {"sandbox_uid": sandbox["metadata"]["uid"],
                "pod_uid": pod["metadata"]["uid"], "pvc_uid": claims[0]["metadata"]["uid"],
                "guest_kernel": output, "at": now()}
            state["unknown_operations"] = 0
            state["state"] = "NATIVE_KATA_PVC_VERIFIED_DEMO_INCOMPLETE"
            write(RUN / "status.json", state)
            print(json.dumps(state["storage_probe"]))
            return
        time.sleep(10)
    raise RuntimeError("Native sandbox/PVC was not Ready; inspect saved identities and Pod events.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "probe"])
    parser.add_argument("--attempt", required=True)
    args = parser.parse_args()
    with writer():
        {"prepare": prepare, "probe": probe}[args.action](args.attempt)
