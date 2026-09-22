"""Exercise real microVM start and memory pause/resume through native APIs."""

import http.client
import json
import socket
import ssl
import subprocess
import time
import uuid
from pathlib import Path

results = {"trials": [], "commands": [], "state": "IN_PROGRESS"}
evidence = Path("lifecycle-results.json")


def save():
    evidence.write_text(json.dumps(results, indent=2) + "\n")


def command(args, json_result=True):
    entry = {"args": args, "state": "OUTCOME_UNKNOWN"}
    results["commands"].append(entry)
    save()
    result = subprocess.run(args, capture_output=True, text=True, timeout=90)
    entry.update(exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr,
                 state="COMMAND_RETURNED")
    save()
    if result.returncode:
        raise RuntimeError(f"Native command failed: {args}: {result.stderr}")
    return json.loads(result.stdout) if json_result else result.stdout


def actor(name):
    return command(["kubectl-ate", "get", "actor", name, "-a", "mvp", "-o", "json"])


def wait_state(name, desired):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        value = actor(name)
        state = value["status"]["state"]
        if state == desired:
            return value
        if state == "ACTOR_STATE_CRASHED":
            raise RuntimeError(f"Actor {name} crashed")
        time.sleep(0.5)
    raise TimeoutError(f"Actor {name} did not reach {desired}")


class RouterConnection(http.client.HTTPSConnection):
    def connect(self):
        raw = socket.create_connection(("127.0.0.1", 18443), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def request(name, path, body=None):
    connection = RouterConnection("atenet-router.ate-system.svc", 18443, context=context, timeout=90)
    try:
        connection.request("POST" if body is not None else "GET", path,
                           body=json.dumps(body) if body is not None else None,
                           headers={"Authorization": f"Bearer {token}",
                                    "ate-target-actor": f"mvp/{name}",
                                    "Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read().decode()
        if response.status != 200:
            raise RuntimeError(f"Router {path}: HTTP {response.status}: {raw[:1000]}")
        return json.loads(raw)
    finally:
        connection.close()


if evidence.exists():
    raise RuntimeError("Lifecycle already attempted; inspect evidence, do not replay trials.")
save()
template = command(["kubectl-ate", "get", "actor-template", "python-microvm", "-a", "mvp", "-o", "json"])
assert template["status"]["goldenSnapshotStatus"]["goldenTag"]["name"]
assert not command(["kubectl-ate", "get", "actors", "-A", "-o", "json"]).get("actors")
bundle = command(["kubectl", "get", "clustertrustbundle",
                  "servicedns.podcert.ate.dev:identity:primary-bundle", "-o", "json"])
context = ssl.create_default_context(cadata=bundle["spec"]["trustBundle"])
token = subprocess.check_output(["kubectl", "-n", "ate-system", "create", "token", "ate-client",
                                 "--audience=api.ate-system.svc", "--duration=30m"], text=True).strip()
with Path("private/router-portforward.log").open("x") as log:
    forward = subprocess.Popen(["kubectl", "-n", "ate-system", "port-forward",
                                "service/atenet-router", "18443:443", "--address=127.0.0.1"],
                               stdout=log, stderr=log)
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
        for index in range(1, 4):
            name = f"trial-{index}"
            trial = {"name": name, "outcome": "IN_PROGRESS"}
            results["trials"].append(trial)
            save()
            start = time.monotonic()
            created = command(["kubectl-ate", "create", "actor", name, "-a", "mvp",
                               "--template=python-microvm", "-o", "json"])
            initial = request(name, "/state")
            assert initial == {"memory": {"marker": None, "counter": 0}, "file": None}, initial
            trial.update(initial_start_seconds=round(time.monotonic() - start, 4),
                         actor_uid=created["metadata"]["uid"], initial=initial)
            before = request(name, "/advance", {"marker": str(uuid.uuid4())})
            native_before = actor(name)
            trial.update(before=before, worker_before=native_before["status"]["workerAssignment"],
                         facts=request(name, "/facts"))
            save()
            pause_start = time.monotonic()
            command(["kubectl-ate", "pause", "actor", name, "-a", "mvp", "-o", "json"])
            paused = wait_state(name, "ACTOR_STATE_PAUSED")
            trial.update(pause_seconds=round(time.monotonic() - pause_start, 4), paused=paused["status"])
            save()
            resume_start = time.monotonic()
            command(["kubectl-ate", "resume", "actor", name, "-a", "mvp", "-o", "json"])
            after = request(name, "/state")
            trial.update(resume_seconds=round(time.monotonic() - resume_start, 4), after=after)
            assert before == after, {"before": before, "after": after}
            assert before["memory"]["marker"] != before["file"]["marker"]
            assert before["memory"]["counter"] == 1
            trial["worker_after"] = actor(name)["status"]["workerAssignment"]
            trial["outcome"] = "PASS"
            save()
            if index != 3:
                command(["kubectl-ate", "delete", "actor", name, "-a", "mvp", "--any-state"], False)
                deadline = time.monotonic() + 30
                while command(["kubectl-ate", "get", "actors", "-A", "-o", "json"]).get("actors"):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Prior actor cleanup did not complete")
                    time.sleep(0.5)
        results["state"] = "PASS"
        save()
        print(json.dumps({"state": results["state"], "trials": results["trials"]}, indent=2))
    except Exception as error:
        results.update(state="FAIL", error=f"{type(error).__name__}: {error}")
        save()
        raise
    finally:
        forward.terminate()
        forward.wait(timeout=10)
