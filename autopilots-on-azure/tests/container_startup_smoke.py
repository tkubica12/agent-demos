from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import uuid


PROBE = """
import json, urllib.request
environment = json.load(open('/tmp/service.json'))['environment']
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
request = urllib.request.Request('http://127.0.0.1:PORT/HEALTH')
with opener.open(request, timeout=5) as response:
    assert response.status == 200 and json.load(response)['status'] == 'ok'
if ROLE == 'runtime':
    request = urllib.request.Request(
        'http://127.0.0.1:8642/internal/runtime/status',
        headers={'X-Autopilot-Key': environment['API_SERVER_KEY']},
    )
    with opener.open(request, timeout=5) as response:
        result = json.load(response)
        assert response.status == 200 and type(result['active']) is int
print('container-startup-ready')
"""


def run(*arguments: str, timeout: int = 180) -> str:
    return subprocess.check_output(["docker", *arguments], text=True, stderr=subprocess.STDOUT, timeout=timeout)


def main() -> None:
    parser = argparse.ArgumentParser(description="Start a released container offline; no inference or Azure acceptance.")
    parser.add_argument("--role", required=True, choices=("runtime", "gateway", "simulator"))
    parser.add_argument("--image", required=True)
    options = parser.parse_args()
    role = options.role
    environment = {
        "APPLICATION_RELEASE": "container-startup-smoke",
        "API_SERVER_KEY": secrets.token_urlsafe(32),
        "WORKER_ID": "container-startup-smoke",
        "USE_AGENTIC_AUTH": "false",
        "USER_SCHEDULING_ENABLED": "false",
        "SERVICEBUS_DREAM_ENABLED": "false",
        "FOUNDRY_OPENAI_BASE_URL": "https://container-startup.invalid/openai/v1",
    }
    if role == "simulator":
        environment = {
            "TSIM_AUTH_MODE": "demo", "TSIM_DEMO_ACCESS_CODE": secrets.token_urlsafe(32),
            "TSIM_DEMO_EXPIRES_AT": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "TSIM_DEMO_ACKNOWLEDGE": "test-data-only",
            "TSIM_ALLOWED_ORIGINS": "https://container-startup.invalid",
            "TSIM_REDIRECT_URI": "https://container-startup.invalid",
            "TSIM_SERVICE_URL": "https://container-startup.invalid", "ENV": "production",
        }
    name = "autopilots-startup-" + uuid.uuid4().hex
    run("pull", options.image, timeout=600)
    created = False
    try:
        run("run", "--detach", "--name", name, "--network", "none",
            "--env", "AUTOPILOTS_SERVICE_CONFIG=/tmp/service.json", options.image)
        created = True
        with tempfile.TemporaryDirectory() as temporary:
            configuration = Path(temporary) / "service.json"
            configuration.write_text(json.dumps({"environment": environment}), encoding="utf-8")
            configuration.chmod(0o600)
            run("cp", str(configuration), name + ":/tmp/service.json.next")
            owner = "10001:10001" if role == "simulator" else "0:0"
            run("exec", "--user", "0:0", name, "sh", "-c",
                "chown " + owner + " /tmp/service.json.next && chmod 0600 /tmp/service.json.next"
                " && mv /tmp/service.json.next /tmp/service.json")
        probe = PROBE.replace("PORT", {"runtime": "8642", "gateway": "8001", "simulator": "8000"}[role])
        probe = probe.replace("HEALTH", "api/health" if role == "simulator" else "health").replace("ROLE", repr(role))
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if run("inspect", "--format", "{{.State.Running}}", name).strip() != "true":
                raise RuntimeError("The packaged application exited during startup.")
            try:
                output = run("exec", "--user", "0:0", name, "/app/.venv/bin/python", "-c", probe)
            except subprocess.CalledProcessError:
                time.sleep(2)
                continue
            if output.strip() != "container-startup-ready":
                raise RuntimeError("The container health probe did not attest completion.")
            print(json.dumps({"role": role, "image": options.image, "startup": "passed",
                              "nativeActivityEndpoint": role == "runtime", "inference": "not-run"}))
            return
        raise TimeoutError("The packaged application did not become ready.")
    except (subprocess.SubprocessError, OSError, RuntimeError) as error:
        if created:
            try:
                logs = run("logs", "--tail", "100", name)
                log = "/tmp/simulator-service.log" if role == "simulator" else "/app/.sandbox-service.log"
                with tempfile.TemporaryDirectory() as temporary:
                    target = Path(temporary) / "service.log"
                    run("cp", name + ":" + log, str(target))
                    logs += target.read_text(encoding="utf-8")
                for key in ("API_SERVER_KEY", "TSIM_DEMO_ACCESS_CODE"):
                    if environment.get(key):
                        logs = logs.replace(environment[key], "[redacted]")
                print(logs)
            except (subprocess.SubprocessError, OSError) as diagnostic_error:
                error.add_note("Container startup logs could not be collected: " + type(diagnostic_error).__name__)
        raise
    finally:
        if created:
            run("rm", "--force", name)


if __name__ == "__main__":
    main()
