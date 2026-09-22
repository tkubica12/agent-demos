"""Probe real credential receipt and HTTPS L7 enforcement without replaying setup."""

import base64
import http.client
import json
import os
import socket
import ssl
import subprocess
import time
from pathlib import Path

stage = os.environ["SUBSTRATE_STAGE"]
evidence = Path(f"{stage}-results.json")
if evidence.exists():
    raise RuntimeError("Check already attempted; use its saved evidence.")
results = {"state": "IN_PROGRESS", "probes": {}}


def save():
    evidence.write_text(json.dumps(results, indent=2))


def native(*args):
    return json.loads(subprocess.check_output(args, text=True, timeout=60))


class RouterConnection(http.client.HTTPSConnection):
    def connect(self):
        raw = socket.create_connection(("127.0.0.1", 18443), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def request(path, body=None):
    connection = RouterConnection("atenet-router.ate-system.svc", 18443, context=context, timeout=45)
    try:
        connection.request("POST" if body is not None else "GET", path,
                           body=json.dumps(body) if body is not None else None,
                           headers={"Authorization": f"Bearer {token}",
                                    "ate-target-actor": "mvp/trial-3", "Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read().decode()
        if response.status != 200:
            raise RuntimeError(f"Router {path}: HTTP {response.status}: {raw[:500]}")
        return json.loads(raw)
    finally:
        connection.close()


save()
ca = native("kubectl", "get", "clustertrustbundle",
            "servicedns.podcert.ate.dev:identity:primary-bundle", "-o", "json")
context = ssl.create_default_context(cadata=ca["spec"]["trustBundle"])
token = subprocess.check_output(["kubectl", "-n", "ate-system", "create", "token", "ate-client",
                                 "--audience=api.ate-system.svc", "--duration=10m"], text=True).strip()
credential = base64.b64decode(native("kubectl", "-n", "mvp-secrets", "get", "secret",
                                     "demo-api", "-o", "json")["data"]["token"]).decode()
origin = native("kubectl", "-n", "mvp-upstream", "get", "service", "origin", "-o", "json")
ip = origin["spec"]["clusterIP"]
with Path(f"private/{stage}-router.log").open("x") as log:
    forward = subprocess.Popen(["kubectl", "-n", "ate-system", "port-forward", "service/atenet-router",
                                "18443:443", "--address=127.0.0.1"], stdout=log, stderr=log)
    try:
        deadline = time.monotonic() + 20
        while True:
            if forward.poll() is not None:
                raise RuntimeError("Router port-forward exited")
            try:
                with socket.create_connection(("127.0.0.1", 18443), 1):
                    break
            except ConnectionRefusedError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)
        results["actor"] = native("kubectl-ate", "get", "actor", "trial-3", "-a", "mvp", "-o", "json")
        results["facts"] = request("/facts")
        cases = {
            "allowed": ("https://origin.mvp-upstream.svc/allowed", "GET"),
            "denied_path": ("https://origin.mvp-upstream.svc/forbidden", "GET"),
            "denied_method": ("https://origin.mvp-upstream.svc/allowed", "POST"),
            "denied_host": ("https://example.com/allowed", "GET"),
            "direct_ip": (f"https://{ip}/allowed", "GET"),
            "plaintext_http": ("http://origin.mvp-upstream.svc/allowed", "GET"),
            "metadata": ("http://169.254.169.254/metadata/instance?api-version=2021-02-01", "GET"),
            "dns_port_bypass": (f"http://{ip}:53/forbidden", "GET"),
        }
        for name, (url, method) in cases.items():
            result = request("/probe", {"url": url, "method": method})
            if credential in json.dumps(result):
                results["probes"][name] = {"credential_exposed": True}
                raise RuntimeError("Credential returned to guest; raw response withheld")
            results["probes"][name] = result
            save()
        env = request("/probe", {"url": "file:///proc/self/environ"})
        results["guest_environment"] = {"read_succeeded": "body" in env,
                                         "credential_present": credential in env.get("body", "")}
        results["private_files"] = {}
        for path in ("/run/expected/token", "/run/secrets/token", "/opt/substrate-mvp/private",
                     "/var/run/secrets/kubernetes.io/serviceaccount/token"):
            response = request("/probe", {"url": "file://" + path})
            results["private_files"][path] = {
                "read_succeeded": "body" in response,
                "credential_present": credential in response.get("body", ""),
                "error": response.get("error")}
        results["origin_receipts"] = subprocess.check_output(
            ["kubectl", "-n", "mvp-upstream", "logs", "deployment/origin"], text=True, timeout=30)
        allowed = results["probes"]["allowed"]
        results["credential_injection"] = (
            allowed.get("status") == 200 and json.loads(allowed.get("body", "{}")).get("credential_valid") is True)
        results["l7"] = all(results["probes"][name].get("status") == 403
                            for name in ("denied_path", "denied_method", "denied_host"))
        results["direct_bypass_blocked"] = results["probes"]["dns_port_bypass"].get("status") is None
        results["state"] = "PASS" if all(results[key] for key in
                                         ("credential_injection", "l7", "direct_bypass_blocked")) else "FAIL"
        save()
        print(json.dumps(results, indent=2))
    except Exception as error:
        results.update(state="FAIL", error=f"{type(error).__name__}: {error}")
        save()
        raise
    finally:
        forward.terminate()
        forward.wait(timeout=10)
