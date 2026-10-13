"""Execute actual isolation and credential/egress checks inside native Kata guests."""

import argparse
import json
import time
from pathlib import Path

from gateway import GATEWAY, ORIGIN
from qualify import kubectl
from run_state import PRIVATE, RUN, check_limits, command, now, read, write, writer
from sandbox_probe import NS, apply, get, obj, template


def wait_pod(attempt, name, timeout=240):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        sandbox = get(f"{attempt}-{int(time.monotonic())}", "sandbox", name)
        pods = get(f"{attempt}-{int(time.monotonic())}", "pods")["items"]
        matches = [p for p in pods for owner in p["metadata"].get("ownerReferences", [])
                   if owner["uid"] == sandbox["metadata"]["uid"]]
        if len(matches) == 1 and any(c["type"] == "Ready" and c["status"] == "True"
                                     for c in matches[0].get("status", {}).get("conditions", [])):
            return sandbox, matches[0]
        time.sleep(7)
    raise RuntimeError(f"Native sandbox {name} did not become ready.")


def execute(attempt, pod, script, payload=None):
    result = command(attempt, kubectl("exec", "-i", "-n", NS,
        pod["metadata"]["name"], "--", "python3", "-c", script),
        input_data=json.dumps(payload) if payload is not None else "")
    return json.loads(result)


def tenant(attempt):
    state = read(RUN / "status.json")
    check_limits(state)
    if not state.get("gateway_install") or not state.get("storage_probe"):
        raise RuntimeError("Gateway and first native guest must be verified.")
    pods = get(attempt, "pods")["items"]
    if len([p for p in pods if p["metadata"]["labels"].get("demo.sandbox/role") == "agent"]) != 1:
        raise RuntimeError("Unexpected native guest count; cap is two.")
    spec = template()["spec"]
    apply(attempt, obj("Sandbox", "tenant-b", {"podTemplate": spec["podTemplate"],
        "volumeClaimTemplates": spec["volumeClaimTemplates"]}, "agents.x-k8s.io/v1beta1"))
    sandbox, pod = wait_pod(attempt, "tenant-b")
    if "kata" not in pod["spec"]["nodeName"] or pod["spec"]["runtimeClassName"] != "kata-vm-isolation":
        raise RuntimeError("Second native guest is not scheduled to managed Kata.")
    state = read(RUN / "status.json")
    state["tenant_b"] = {"sandbox_uid": sandbox["metadata"]["uid"], "pod_uid": pod["metadata"]["uid"]}
    state["unknown_operations"] = 0
    write(RUN / "status.json", state)
    print(json.dumps(state["tenant_b"]))


def request(attempt, pod, client, *, path="/allowed", method="GET", host=GATEWAY, default_trust=False):
    script = """
import json,ssl,sys,urllib.request,urllib.error
x=json.load(sys.stdin)
ctx=ssl.create_default_context() if x['default_trust'] else ssl.create_default_context(cadata=x['ca'])
r=urllib.request.Request('https://'+x['server']+':8443'+x['path'],method=x['method'],
 headers={'Authorization':'Bearer '+x['token'],'Host':x['host']})
try:
 with urllib.request.urlopen(r,context=ctx,timeout=8) as f:
  print(json.dumps({'status':f.status,'body':f.read().decode()}))
except urllib.error.HTTPError as e:
 print(json.dumps({'status':e.code,'body':e.read().decode()}))
except urllib.error.URLError as e:
 if isinstance(e.reason,ssl.SSLCertVerificationError):
  print(json.dumps({'tls_verified':False}))
 else: raise
"""
    ca = get(attempt + "-ca", "configmap", "lab-ca")["data"]["ca.crt"]
    return execute(attempt, pod, script, {"server": GATEWAY, "host": host,
        "path": path, "method": method, "token": client, "ca": ca, "default_trust": default_trust})


def isolation(attempt, pod, owner):
    script = """
import json,os
from pathlib import Path
p=Path('/workspace/owner')
existing=p.read_text() if p.exists() else None
if existing is None: p.write_text(%r)
print(json.dumps({'existing':existing,'owner':p.read_text(),'kernel':os.uname().release,
 'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
 'api_token':Path('/var/run/secrets/kubernetes.io/serviceaccount/token').exists(),
 'upstream_token':Path('/secrets/token').exists(),
 'host_mount':Path('/host/etc/shadow').exists(),
 'shadow_readable':os.access('/etc/shadow',os.R_OK)}))
""" % owner
    return execute(attempt, pod, script)


def socket_probe(attempt, pod, host, port, protocol="TCP"):
    script = r"""
import json,socket,sys
x=json.load(sys.stdin)
s=socket.socket(socket.AF_INET,socket.SOCK_STREAM if x['protocol']=='TCP' else socket.SOCK_DGRAM)
s.settimeout(3)
try:
 if x['protocol']=='TCP': s.connect((x['host'],x['port']))
 else:
  query=(b'\\x12\\x34\\x01\\x00\\x00\\x01\\x00\\x00\\x00\\x00\\x00\\x00'
         b'\\x07example\\x03com\\x00\\x00\\x01\\x00\\x01')
  s.sendto(query,(x['host'],x['port']))
  s.recvfrom(512)
 result='CONNECTED'
except (socket.timeout,TimeoutError):
 result='TIMEOUT'
except OSError as e:
 result='OSERROR:'+str(e.errno)
finally:
 s.close()
print(json.dumps({'result':result}))
"""
    return execute(attempt, pod, script, {"host": host, "port": port, "protocol": protocol})["result"]


def check(attempt):
    state = read(RUN / "status.json")
    check_limits(state)
    if not state.get("tenant_b") or state.get("gateway_install", {}).get("state") != "VERIFIED":
        raise RuntimeError("Both tenants and gateway must exist.")
    a, pa = wait_pod(attempt + "-a", "storage-probe")
    b, pb = wait_pod(attempt + "-b", "tenant-b")
    if a["metadata"]["uid"] == b["metadata"]["uid"]:
        raise RuntimeError("Tenants share a native sandbox.")
    for pod in (pa, pb):
        spec = pod["spec"]
        if spec["runtimeClassName"] != "kata-vm-isolation" or "kata" not in spec["nodeName"]:
            raise RuntimeError("Sandbox ran outside Kata.")
        if spec.get("automountServiceAccountToken") is not False or any(
            "secret" in v or "hostPath" in v or "projected" in v for v in spec["volumes"]
        ):
            raise RuntimeError("Guest has forbidden credentials or host mounts.")
        if spec.get("hostNetwork") or spec.get("hostPID") or spec.get("hostIPC"):
            raise RuntimeError("Guest has host namespace access.")
    ia, ib = isolation(attempt + "-isolation-a", pa, "tenant-a"), isolation(attempt + "-isolation-b", pb, "tenant-b")
    for actual, owner in ((ia, "tenant-a"), (ib, "tenant-b")):
        if actual["owner"] != owner or any(actual[k] for k in
            ("api_token", "upstream_token", "host_mount", "shadow_readable")):
            raise RuntimeError(f"Tenant {owner} isolation check failed.")
        if actual["kernel"] != state["kernel_execution"]["kata"]:
            raise RuntimeError("Guest kernel differs from qualified Kata guest.")
    if ia["boot_id"] == ib["boot_id"] or ia["existing"] not in (None, "tenant-a") or ib["existing"] not in (None, "tenant-b"):
        raise RuntimeError("Separate tenants did not get separate fresh guests/files.")
    clients = read(Path(state["gateway_install"]["client_file"]))
    allowed = request(attempt + "-allowed", pa, clients["client-a"])
    if allowed["status"] != 200 or not json.loads(allowed["body"])["authorized"]:
        raise RuntimeError("Synthetic origin did not verify outside-guest upstream credential.")
    denials = {
        "path": request(attempt + "-path", pa, clients["client-a"], path="/denied"),
        "method": request(attempt + "-method", pa, clients["client-a"], method="POST"),
        "host": request(attempt + "-host", pa, clients["client-a"], host=ORIGIN),
        "client_b": request(attempt + "-clientb", pb, clients["client-b"]),
    }
    if any(x.get("status") not in (401, 403) for x in denials.values()):
        raise RuntimeError(f"Host/method/path/client denial failed: {denials}")
    untrusted = request(attempt + "-untrusted-ca", pa, clients["client-a"], default_trust=True)
    if untrusted != {"tls_verified": False}:
        raise RuntimeError("Untrusted downstream certificate was accepted.")
    origin = [p for p in get(attempt + "-platform-pods", "pods")["items"]
              if p["metadata"]["labels"].get("demo.sandbox/role") == "origin"]
    if len(origin) != 1:
        raise RuntimeError("Expected one platform origin.")
    targets = {
        "lateral": (pb["status"]["podIP"], 8080, "TCP"),
        "direct_origin": (origin[0]["status"]["podIP"], 8443, "TCP"),
        "public_ip": ("1.1.1.1", 443, "TCP"),
        "metadata": ("169.254.169.254", 80, "TCP"),
        "tcp53": ("1.1.1.1", 53, "TCP"),
        "udp53": ("1.1.1.1", 53, "UDP"),
    }
    denied = {}
    for label, (host, port, proto) in targets.items():
        result = socket_probe(attempt + "-" + label.replace("_", "-"), pa, host, port, proto)
        denied[label] = {"destination": host, "port": port, "protocol": proto, "result": result}
        if result != "TIMEOUT":
            raise RuntimeError(f"Network bypass/ambiguous drop on {label}: {result}")
    gateway_config = json.loads(get(attempt + "-gateway-config", "configmap", "gateway-config")["data"]["config.json"])
    backend = gateway_config["binds"][0]["listeners"][0]["routes"][0]["backends"][0]["policies"]
    if backend["backendTLS"] != {"root": "/ca/ca.crt", "hostname": ORIGIN}:
        raise RuntimeError("Upstream TLS trust/hostname configuration differs.")
    result = {"at": now(), "guest_uids": [pa["metadata"]["uid"], pb["metadata"]["uid"]],
        "guest_boot_ids": [ia["boot_id"], ib["boot_id"]], "allowed": 200,
        "denied_http": {key: value["status"] for key, value in denials.items()},
        "untrusted_tls_rejected": True, "denied_network": denied,
        "identity_limit": "Possession-based synthetic bearer; not attested identity"}
    state = read(RUN / "status.json")
    state["checks"]["C02"] = {"status": "PASS", "at": now(), "evidence": attempt}
    state["checks"]["C03"] = {"status": "NOT_RUN", "at": now(), "evidence": attempt,
        "remaining": "Negative upstream TLS substitution, proxy bypass and origin audit"}
    state["isolation_observation"] = result
    state["state"] = "ISOLATION_AND_GATEWAY_CHECKS_PASSED_LIFECYCLE_PENDING"
    write(RUN / "status.json", state)
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["tenant", "check"])
    parser.add_argument("--attempt", required=True)
    args = parser.parse_args()
    with writer():
        {"tenant": tenant, "check": check}[args.action](args.attempt)
