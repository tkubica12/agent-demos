"""Deploy outside-guest TLS gateway and HTTPS origin behind default-deny policies."""

import argparse
import base64
import hashlib
import json
import secrets
import subprocess
import tempfile
from pathlib import Path

from qualify import kubectl
from run_state import PRIVATE, ROOT, RUN, check_limits, command, now, read, write, writer
from sandbox_probe import NS, obj

GATEWAY = f"gateway.{NS}.svc.cluster.local"
ORIGIN = f"origin.{NS}.svc.cluster.local"
IMAGE = "docker.io/library/python:3.13.7-slim"
GATEWAY_IMAGE = "ghcr.io/agentgateway/agentgateway:v1.5.0"


def secret(name, values):
    return {"apiVersion": "v1", "kind": "Secret", "metadata": {"namespace": NS, "name": name},
        "type": "Opaque", "data": {key: base64.b64encode(value.encode()).decode()
                                   for key, value in values.items()}}


def config(name, values):
    return {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"namespace": NS, "name": name},
        "data": values}


def volume(name, source, value):
    return {"name": name, source: {"secretName" if source == "secret" else "name": value}}


def policy(role, egress=None, ingress=None):
    return obj("NetworkPolicy", role + "-boundary", {
        "podSelector": {"matchLabels": {"demo.sandbox/role": role}},
        "policyTypes": ["Ingress", "Egress"],
        "ingress": ingress or [], "egress": egress or [],
    }, "networking.k8s.io/v1")


def peer(role):
    return {"podSelector": {"matchLabels": {"demo.sandbox/role": role}}}


def tcp(port):
    return [{"protocol": "TCP", "port": port}]


def policies():
    dns = {"to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
                   "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}}}],
           "ports": [{"protocol": protocol, "port": 53} for protocol in ("TCP", "UDP")]}
    return [
        policy("agent", [dns, {"to": [peer("gateway")], "ports": tcp(8443)}]),
        policy("gateway", [dns, {"to": [peer("origin")], "ports": tcp(8443)}],
               [{"from": [peer("agent")], "ports": tcp(8443)}]),
        policy("origin", [], [{"from": [peer("gateway")], "ports": tcp(8443)}]),
    ]


def certificates():
    with tempfile.TemporaryDirectory(prefix="aks-demo-pki-", dir=PRIVATE) as folder:
        directory = Path(folder)
        def openssl(*args):
            result = subprocess.run(["openssl", *args], text=True, capture_output=True, timeout=60)
            if result.returncode:
                raise RuntimeError(f"OpenSSL failed ({result.returncode}): {result.stderr[-1000:]}")
        ca = directory / "ca"
        openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(ca) + ".key",
                "-out", str(ca) + ".crt", "-days", "7", "-subj", "/CN=aks-sandbox-synthetic-ca",
                "-addext", "basicConstraints=critical,CA:TRUE",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign")
        result = [config("lab-ca", {"ca.crt": Path(str(ca) + ".crt").read_text()})]
        for name, host in (("gateway", GATEWAY), ("origin", ORIGIN)):
            stem = directory / name
            openssl("req", "-newkey", "rsa:2048", "-nodes", "-keyout", str(stem) + ".key",
                    "-out", str(stem) + ".csr", "-subj", f"/CN={host}")
            ext = directory / f"{name}.ext"
            ext.write_text(f"subjectAltName=DNS:{host}\nextendedKeyUsage=serverAuth\n"
                           "basicConstraints=critical,CA:FALSE\n"
                           "keyUsage=critical,digitalSignature,keyEncipherment\n")
            openssl("x509", "-req", "-in", str(stem) + ".csr",
                    "-CA", str(ca) + ".crt", "-CAkey", str(ca) + ".key",
                    "-CAcreateserial", "-out", str(stem) + ".crt", "-days", "7", "-extfile", str(ext))
            result.append(secret(name + "-tls", {
                "tls.crt": Path(str(stem) + ".crt").read_text(),
                "tls.key": Path(str(stem) + ".key").read_text(),
            }))
        return result


def gateway_config(client_hash):
    return {"binds": [{"port": 8443, "listeners": [{"protocol": "HTTPS",
        "tls": {"cert": "/tls/tls.crt", "key": "/tls/tls.key"},
        "routes": [
            {"name": "allow-read", "hostnames": [GATEWAY],
             "matches": [{"path": {"exact": "/allowed"}, "method": "GET"}],
             "policies": {"apiKey": {"mode": "strict", "keys": [{"keyHash": "sha256:" + client_hash}]}},
             "backends": [{"host": ORIGIN + ":8443", "policies": {
                 "backendTLS": {"root": "/ca/ca.crt", "hostname": ORIGIN},
                 "backendAuth": {"key": {"value": {"file": "/secrets/token"},
                                         "location": {"header": {"name": "Authorization", "prefix": ""}}}},
             }}]},
            {"name": "deny-rest", "policies": {"directResponse": {"status": 403,
                "body": "AKS method/path/host denied"}}},
        ]}]}]}


def deployment(name, image, args, volumes, mounts):
    return obj("Deployment", name, {"replicas": 1,
        "selector": {"matchLabels": {"demo.sandbox/role": name}},
        "template": {"metadata": {"labels": {"demo.sandbox/role": name}},
            "spec": {"nodeSelector": {"demo.sandbox/role": "platform"},
                "automountServiceAccountToken": False, "enableServiceLinks": False,
                "securityContext": {"runAsUser": 1000, "runAsGroup": 1000, "runAsNonRoot": True,
                                    "seccompProfile": {"type": "RuntimeDefault"}},
                "containers": [{"name": name, "image": image, "args": args,
                    "securityContext": {"allowPrivilegeEscalation": False,
                                        "capabilities": {"drop": ["ALL"]}},
                    "resources": {"requests": {"cpu": "100m", "memory": "128Mi"},
                                  "limits": {"cpu": "1", "memory": "512Mi"}},
                    "volumeMounts": mounts,
                    "readinessProbe": {"tcpSocket": {"port": 8443}, "periodSeconds": 2}}],
                "volumes": volumes}}},
        "apps/v1")


def service(name):
    return obj("Service", name, {"selector": {"demo.sandbox/role": name},
        "ports": [{"port": 8443, "targetPort": 8443}]})


def apply(attempt, resources, *, private=False):
    path = PRIVATE / f"{attempt}-resources.json"
    write(path, {"apiVersion": "v1", "kind": "List", "items": resources})
    command(f"{attempt}-apply", kubectl("apply", "-f", str(path)), mutation=True,
            private_output=private)


def deploy(attempt):
    state = read(RUN / "status.json")
    check_limits(state)
    if not state.get("storage_probe") or state.get("gateway_install"):
        raise RuntimeError("Require verified native Kata/PVC and no previous gateway install.")
    apply(f"{attempt}-policies", policies())
    observed = json.loads(command(f"{attempt}-policies-readback",
        kubectl("get", "networkpolicies", "-n", NS, "-o", "json")))
    expected = {"default-deny", "agent-boundary", "gateway-boundary", "origin-boundary"}
    if {x["metadata"]["name"] for x in observed["items"]} != expected:
        raise RuntimeError("Network boundary not observed in full.")
    state = read(RUN / "status.json")
    state["unknown_operations"] = 0
    write(RUN / "status.json", state)
    upstream = "Bearer " + secrets.token_urlsafe(32)
    client = secrets.token_urlsafe(32)
    clients = {"client-a": client, "client-b": secrets.token_urlsafe(32)}
    private = PRIVATE / f"{attempt}-synthetic-clients.json"
    write(private, clients)
    resources = certificates() + [
        secret("upstream", {"token": upstream}),
        config("fixture", {"fixture.py": (ROOT / "scripts" / "fixture.py").read_text()}),
        config("gateway-config", {"config.json":
            json.dumps(gateway_config(hashlib.sha256(client.encode()).hexdigest()))}),
    ]
    for name, image, args, extra in (
        ("origin", IMAGE, ["python3", "-u", "/app/fixture.py"], volume("app", "configMap", "fixture")),
        ("gateway", GATEWAY_IMAGE, ["-f", "/config/config.json"],
         volume("config", "configMap", "gateway-config")),
    ):
        volumes = [volume("tls", "secret", name + "-tls"), volume("ca", "configMap", "lab-ca"), extra]
        mounts = [{"name": "tls", "mountPath": "/tls", "readOnly": True},
                  {"name": "ca", "mountPath": "/ca", "readOnly": True},
                  {"name": extra["name"], "mountPath": "/app" if name == "origin" else "/config",
                   "readOnly": True}]
        volumes.append(volume("upstream", "secret", "upstream"))
        mounts.append({"name": "upstream", "mountPath": "/secrets", "readOnly": True})
        resources.extend([deployment(name, image, args, volumes, mounts), service(name)])
    apply(f"{attempt}-components", resources, private=True)
    for name in ("origin", "gateway"):
        command(f"{attempt}-{name}-rollout", kubectl("rollout", "status", "-n", NS,
            f"deployment/{name}", "--timeout=180s"), timeout=210)
    pods = json.loads(command(f"{attempt}-pods", kubectl("get", "pods", "-n", NS, "-o", "json")))
    placements = {}
    for name in ("gateway", "origin"):
        matches = [p for p in pods["items"] if p["metadata"]["labels"].get("demo.sandbox/role") == name]
        if len(matches) != 1 or "platform" not in matches[0]["spec"]["nodeName"]:
            raise RuntimeError(f"{name} did not land on the TL platform pool.")
        placements[name] = matches[0]["metadata"]["uid"]
    state = read(RUN / "status.json")
    state["gateway_install"] = {"state": "VERIFIED", "at": now(), "pod_uids": placements,
        "client_file": str(private), "gateway_image": GATEWAY_IMAGE}
    state["unknown_operations"] = 0
    state["state"] = "EXTERNAL_GATEWAY_READY_NOT_DEMO_QUALIFIED"
    write(RUN / "status.json", state)
    print(json.dumps({"gateway": "READY", "outside_kata": True, "pod_uids": placements}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", required=True)
    args = parser.parse_args()
    with writer():
        deploy(args.attempt)
