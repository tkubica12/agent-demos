"""Deploy the real synthetic HTTPS origin and narrow native credential policy."""

import hashlib
import json
import os
import secrets
import socket
import subprocess
import tarfile
import time
import urllib.request
from pathlib import Path

ROOT = Path("/opt/substrate-mvp")
log = {"operations": [], "state": "IN_PROGRESS"}
output = ROOT / "security-setup-results.json"
if output.exists():
    raise RuntimeError("Security setup already attempted; inspect rather than replay.")


def save():
    output.write_text(json.dumps(log, indent=2))


def call(args, payload=None, secret=False):
    entry = {"command": args, "state": "OUTCOME_UNKNOWN"}
    log["operations"].append(entry)
    save()
    result = subprocess.run(args, input=json.dumps(payload) if payload is not None else None,
                            text=True, capture_output=True, timeout=180)
    entry.update(state="COMMAND_RETURNED", exit_code=result.returncode,
                 stdout="[private result omitted]" if secret else result.stdout,
                 stderr=result.stderr)
    save()
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr}")
    return result.stdout


def create(obj, secret=False):
    raw = call(["kubectl", "create", "-f", "-", "-o", "json"], obj, secret)
    value = json.loads(raw)
    if secret:
        log["operations"][-1]["identity"] = {key: value["metadata"][key] for key in ("name", "namespace", "uid")}
        save()
    return value


ORIGIN = '''
import hmac,json,ssl,threading
from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
token=Path("/run/expected/token").read_text()
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def handle_request(self):
        valid=hmac.compare_digest(self.headers.get("Authorization",""),"Bearer "+token)
        value={"credential_valid":valid,"method":self.command,"path":self.path,
               "time":datetime.now(timezone.utc).isoformat()}
        if self.path != "/readyz": print(json.dumps(value),flush=True)
        payload=json.dumps(value).encode()
        self.send_response(200 if valid or self.path=="/readyz" else 401)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
    do_GET=handle_request
    do_POST=handle_request
plain=ThreadingHTTPServer(("0.0.0.0",8053),Handler)
threading.Thread(target=plain.serve_forever,daemon=True).start()
server=ThreadingHTTPServer(("0.0.0.0",8443),Handler)
context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain("/run/tls/bundle.pem")
server.socket=context.wrap_socket(server.socket,server_side=True)
server.serve_forever()
'''

try:
    for namespace in ("mvp-secrets", "mvp-upstream"):
        create({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}})
    credential = secrets.token_urlsafe(48)
    for namespace, name in (("mvp-secrets", "demo-api"), ("mvp-upstream", "expected-token")):
        create({"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name, "namespace": namespace},
                "type": "Opaque", "stringData": {"token": credential}}, secret=True)
    del credential
    call(["kubectl", "delete", "clusterrolebinding", "k8s-credential-provider-secret-reader"])
    create({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role",
            "metadata": {"name": "demo-api-reader", "namespace": "mvp-secrets"},
            "rules": [{"apiGroups": [""], "resources": ["secrets"], "resourceNames": ["demo-api"], "verbs": ["get"]}]})
    create({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding",
            "metadata": {"name": "demo-api-reader", "namespace": "mvp-secrets"},
            "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "demo-api-reader"},
            "subjects": [{"kind": "ServiceAccount", "name": "k8s-credential-provider", "namespace": "ate-system"}]})
    create({"apiVersion": "v1", "kind": "Service",
            "metadata": {"name": "origin", "namespace": "mvp-upstream"},
            "spec": {"selector": {"app": "mvp-origin"},
                     "ports": [{"name": "https", "port": 443, "targetPort": 8443},
                               {"name": "dns-bypass-probe", "port": 53, "targetPort": 8053}]}})
    create({"apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": "origin", "namespace": "mvp-upstream"},
            "spec": {"replicas": 1, "selector": {"matchLabels": {"app": "mvp-origin"}},
                     "template": {"metadata": {"labels": {"app": "mvp-origin"}},
                                  "spec": {"automountServiceAccountToken": False,
                                           "securityContext": {"runAsNonRoot": True, "runAsUser": 10000, "fsGroup": 10000},
                                           "containers": [{
                                               "name": "origin",
                                               "image": "docker.io/library/python@sha256:3e2de9c40ca4e3d73240059f9d48baff27908f10293e985a2f382a0378e6df4a",
                                               "command": ["python", "-u", "-c", ORIGIN],
                                               "resources": {"requests": {"cpu": "50m", "memory": "64Mi"},
                                                             "limits": {"cpu": "250m", "memory": "128Mi"}},
                                               "securityContext": {"allowPrivilegeEscalation": False,
                                                                   "readOnlyRootFilesystem": True,
                                                                   "capabilities": {"drop": ["ALL"]}},
                                               "readinessProbe": {"tcpSocket": {"port": 8443}, "periodSeconds": 2},
                                               "volumeMounts": [{"name": "tls", "mountPath": "/run/tls", "readOnly": True},
                                                                {"name": "expected", "mountPath": "/run/expected", "readOnly": True}],
                                           }],
                                           "volumes": [
                                               {"name": "expected", "secret": {"secretName": "expected-token"}},
                                               {"name": "tls", "projected": {"sources": [{
                                                   "podCertificate": {"signerName": "servicedns.podcert.ate.dev/identity",
                                                                      "keyType": "ECDSAP256",
                                                                      "credentialBundlePath": "bundle.pem"}}]}}],
                                           }}}})
    config = json.loads(call(["kubectl", "-n", "ate-system", "get", "configmap",
                              "atenet-egress-agentgateway-config", "-o", "json"]))
    text = config["data"]["config.yaml"]
    original_hash = hashlib.sha256(text.encode()).hexdigest()
    needle = "      policies:\n        substrateEgress:"
    if text.count(needle) != 2:
        raise RuntimeError("Published gateway config differs from inspected route layout")
    text = text.replace(needle, """      policies:
        authorization:
          rules:
          - 'request.method == "GET" && request.path == "/allowed"'
        substrateEgress:""", 1)
    text = text.replace(needle, """      policies:
        authorization:
          rules: ['false']
        substrateEgress:""", 1)
    if text.count("backendTLS: {}") != 1:
        raise RuntimeError("Unexpected backend TLS layout")
    text = text.replace("backendTLS: {}", "backendTLS:\n            root: /run/servicedns.podcert.ate.dev/trust-bundle.pem")
    tcp = """  - protocol: TCP
    tcpRoutes:
    - backends:
      - dynamic:
          target: source.connectHeaders["host"]
"""
    if text.count(tcp) != 1:
        raise RuntimeError("Unexpected opaque TCP route layout")
    text = text.replace(tcp, "  - protocol: TCP\n    tcpRoutes: []\n")
    config["data"]["config.yaml"] = text
    call(["kubectl", "replace", "-f", "-"], config)
    log["gateway_config"] = {"before_sha256": original_hash,
                             "after_sha256": hashlib.sha256(text.encode()).hexdigest(),
                             "policy": "GET /allowed only, native per-Actor hostname allow and credential injection; opaque TCP disabled",
                             "upstream_tls": "Cluster service DNS CA, no certificate verification bypass"}
    call(["kubectl", "-n", "ate-system", "rollout", "restart", "deployment/atenet-egress"])
    call(["kubectl", "-n", "ate-system", "rollout", "status", "deployment/atenet-egress", "--timeout=120s"])
    call(["kubectl", "-n", "mvp-upstream", "rollout", "status", "deployment/origin", "--timeout=120s"])
    url = "https://github.com/fullstorydev/grpcurl/releases/download/v1.9.3/grpcurl_1.9.3_linux_x86_64.tar.gz"
    archive = ROOT / "downloads" / "grpcurl.tar.gz"
    with urllib.request.urlopen(url, timeout=60) as response, archive.open("xb") as target:
        target.write(response.read())
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == "a926b62a85787ccf73ef8736b3ae554f1242e39d92bb8767a79d6dd23b11d1d5"
    with tarfile.open(archive) as bundle:
        bundle.extract("grpcurl", ROOT / "bin", filter="data")
    os.chmod(ROOT / "bin" / "grpcurl", 0o755)
    authority = json.loads(call(["kubectl", "get", "clustertrustbundle",
                                 "servicedns.podcert.ate.dev:identity:primary-bundle", "-o", "json"]))
    (ROOT / "private" / "service-ca.pem").write_text(authority["spec"]["trustBundle"])
    token = call(["kubectl", "-n", "ate-system", "create", "token", "ate-client",
                  "--audience=api.ate-system.svc", "--duration=10m"], secret=True).strip()
    os.environ["ATE_TOKEN"] = token
    with (ROOT / "private" / "api-portforward.log").open("x") as stream:
        forward = subprocess.Popen(["kubectl", "-n", "ate-system", "port-forward",
                                    "service/api", "19443:443", "--address=127.0.0.1"],
                                   stdout=stream, stderr=stream)
        try:
            deadline = time.monotonic() + 20
            while True:
                if forward.poll() is not None:
                    raise RuntimeError("API port-forward exited")
                try:
                    with socket.create_connection(("127.0.0.1", 19443), 1):
                        break
                except ConnectionRefusedError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.2)
            policy = {"actor": {"atespace": "mvp", "name": "trial-3"},
                      "egressPolicy": {"metadata": {"atespace": "mvp", "name": "default"},
                                       "rules": [{"hostnames": {"patterns": ["origin.mvp-upstream.svc"],
                                                               "effects": {"injectStaticHeaders": [{
                                                                   "header": "authorization", "prefix": "Bearer ",
                                                                   "credentialUri": "ate-secret://kubernetes.io/mvp-secrets/demo-api/token"}]}}}]}}
            call(["grpcurl", "-cacert", "private/service-ca.pem", "-authority", "api.ate-system.svc",
                  "-expand-headers", "-H", "Authorization: Bearer ${ATE_TOKEN}", "-d", "@",
                  "127.0.0.1:19443", "ateapi.Control/CreateActorEgressPolicy"], policy)
        finally:
            forward.terminate()
            forward.wait(timeout=10)
            del os.environ["ATE_TOKEN"]
    log["state"] = "SETUP_COMPLETE_PROBES_PENDING"
    save()
    print(json.dumps({"state": log["state"], "gateway_config": log["gateway_config"]}, indent=2))
except Exception as error:
    log.update(state="FAIL", error=f"{type(error).__name__}: {error}")
    save()
    raise
