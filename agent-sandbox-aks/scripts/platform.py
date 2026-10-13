"""Install the pinned native Agent Sandbox controller on the TL platform pool."""

import argparse
import json

from qualify import KUBECONFIG, kubectl
from run_state import PRIVATE, RUN, check_limits, command, now, read, sha, write, writer

MANIFEST = PRIVATE / "upstream" / "sandbox-with-extensions.yaml"
DIGEST = "21d8c78ab077b2080aef81bd3842ab0838eae75f8e032d8c75d1be5734fa4896"
NAMESPACE = "agent-sandbox-system"
CRDS = (
    "sandboxes.agents.x-k8s.io",
    "sandboxclaims.extensions.agents.x-k8s.io",
    "sandboxtemplates.extensions.agents.x-k8s.io",
    "sandboxwarmpools.extensions.agents.x-k8s.io",
)


def get(attempt, kind, name, namespace=None):
    scope = ["-n", namespace] if namespace else []
    return json.loads(command(f"{attempt}-{kind.lower()}-{name}".replace(".", "-"),
        kubectl("get", kind, name, *scope, "-o", "json")))


def install(attempt):
    state = read(RUN / "status.json")
    check_limits(state)
    if state["checks"]["C01"]["status"] != "PASS" or state.get("platform_install"):
        raise RuntimeError("C01 must pass and controller installation must not be replayed.")
    if sha(MANIFEST) != DIGEST:
        raise RuntimeError("Agent Sandbox v1.0.3 manifest digest differs from published release.")
    if command(f"{attempt}-namespace-absence", kubectl("get", "namespace", NAMESPACE, "--ignore-not-found", "-o", "name")).strip():
        raise RuntimeError("Refusing to adopt an existing controller namespace.")
    for crd in CRDS:
        if command(f"{attempt}-{crd.replace('.', '-')}-absence", kubectl("get", "crd", crd, "--ignore-not-found", "-o", "name")).strip():
            raise RuntimeError("Refusing to adopt an existing Agent Sandbox CRD.")
    command(f"{attempt}-dryrun", kubectl("apply", "--dry-run=client", "-f", str(MANIFEST)), timeout=300)
    state["platform_install"] = {"at": now(), "manifest_sha256": DIGEST, "state": "RESERVED"}
    write(RUN / "status.json", state)
    command(f"{attempt}-create", kubectl("apply", "-f", str(MANIFEST)), timeout=420, mutation=True)
    command(f"{attempt}-rollout", kubectl("rollout", "status", "-n", NAMESPACE,
        "deployment/agent-sandbox-controller", "--timeout=300s"), timeout=330)
    observe(attempt)


def observe(attempt):
    state = read(RUN / "status.json")
    if state.get("platform_install", {}).get("manifest_sha256") != DIGEST:
        raise RuntimeError("No matching reserved Agent Sandbox installation.")
    items = []
    for kind, names, namespace in (
        ("Namespace", (NAMESPACE,), None),
        ("CustomResourceDefinition", CRDS, None),
        ("ServiceAccount", ("agent-sandbox-controller",), NAMESPACE),
        ("Deployment", ("agent-sandbox-controller",), NAMESPACE),
        ("ClusterRole", ("agent-sandbox-controller", "agent-sandbox-controller-extensions"), None),
        ("ClusterRoleBinding", ("agent-sandbox-controller", "agent-sandbox-controller-extensions"), None),
    ):
        for name in names:
            obj = get(attempt, kind, name, namespace)
            items.append({"kind": kind, "name": name, "uid": obj["metadata"]["uid"]})
            if kind == "Deployment":
                if obj["spec"]["replicas"] != 1 or obj["status"].get("readyReplicas", 0) != 1:
                    raise RuntimeError("Agent Sandbox controller is not Ready.")
                image = obj["spec"]["template"]["spec"]["containers"][0]["image"]
                if image != "registry.k8s.io/agent-sandbox/agent-sandbox-controller:v1.0.3":
                    raise RuntimeError("Controller image differs from pinned upstream version.")
    pods = json.loads(command(f"{attempt}-controller-pods", kubectl("get", "pods", "-n", NAMESPACE, "-o", "json")))
    if len(pods["items"]) != 1:
        raise RuntimeError("Expected exactly one Agent Sandbox controller pod.")
    pod = pods["items"][0]
    owner_replicas = {o["uid"] for o in pod["metadata"].get("ownerReferences", []) if o["kind"] == "ReplicaSet"}
    replicas = json.loads(command(f"{attempt}-controller-replicas", kubectl("get", "replicasets", "-n", NAMESPACE, "-o", "json")))
    if len(owner_replicas) != 1 or not any(
        rs["metadata"]["uid"] in owner_replicas
        and any(o["uid"] == items[6]["uid"] for o in rs["metadata"].get("ownerReferences", []))
        for rs in replicas["items"]
    ):
        raise RuntimeError("Controller pod owner differs from expected deployment hierarchy.")
    node = pod["spec"]["nodeName"]
    if "platform" not in node:
        raise RuntimeError("Controller is not scheduled on the TL platform pool.")
    state["platform_install"].update(state="VERIFIED", at=now(), resources=items, pod_uid=pod["metadata"]["uid"], platform_node=node)
    state.update(unknown_operations=0, state="CONTROLLER_READY_NOT_DEMO_QUALIFIED")
    write(RUN / "status.json", state)
    print(json.dumps({"controller": "READY", "platform_node": node, "resource_count": len(items)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["install", "observe"])
    parser.add_argument("--attempt", required=True)
    args = parser.parse_args()
    with writer():
        {"install": install, "observe": observe}[args.action](args.attempt)
