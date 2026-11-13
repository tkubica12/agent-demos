from __future__ import annotations

import hashlib
import json
import shlex
import time
from pathlib import Path

import httpx
from azure.containerapps.sandbox import SandboxGroupClient, SandboxVolume
from azure.identity import AzureCliCredential

from scripts.sandbox_services import DISK_IMAGE_AUDIENCE, prepare_disk_image, require_exec_success, require_private_endpoint
from scripts.simulator_channel import simulator_environment


VOLUME = "simulator-data"
CONFIG_PATH = "/tmp/simulator-service.json"
BOOTSTRAP = """
import json, os, pathlib, subprocess, time
log = open("/tmp/simulator-service.log", "a", buffering=1)
os.dup2(log.fileno(), 1)
os.dup2(log.fileno(), 2)
volume = pathlib.Path("/mnt/tsim")
if os.geteuid() != 0:
    raise RuntimeError("Sandbox volume initialization requires the root-bootstrap image")
if volume.stat().st_uid not in (0, 10001):
    raise RuntimeError("Simulator volume has an unexpected owner")
os.chown(volume, 10001, 10001)
os.chmod(volume, 0o700)
os.setgroups([])
os.setgid(10001)
os.setuid(10001)
path = pathlib.Path("/tmp/simulator-service.json")
deadline = time.monotonic() + 1800
while not path.exists():
    if time.monotonic() > deadline:
        raise RuntimeError("Deployment did not supply simulator settings")
    time.sleep(1)
previous = ""
process = None
while True:
    content = path.read_text()
    if content != previous:
        config = json.loads(content)
        if process is not None:
            process.terminate()
            process.wait(timeout=45)
        environment = dict(os.environ)
        environment.update(config["environment"])
        process = subprocess.Popen(config["command"], cwd="/app", env=environment)
        previous = content
    if process.poll() is not None:
        raise RuntimeError("Simulator exited: " + str(process.returncode))
    time.sleep(1)
""".strip()


def save(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def group_client(binding: dict, credential: AzureCliCredential) -> SandboxGroupClient:
    return SandboxGroupClient(
        binding["management_endpoint"], credential,
        subscription_id=binding["subscription_id"], resource_group=binding["resource_group_name"],
        sandbox_group=binding["name"], audience=DISK_IMAGE_AUDIENCE, read_timeout=1800,
    )


def deploy_host(config: dict, binding: dict, platform: dict, image: dict, state_path: Path) -> dict:
    labels = {"app": "autopilots-on-azure", "environment": config["environment"], "service": "simulator"}
    fingerprint = hashlib.sha256((image["image"] + BOOTSTRAP).encode()).hexdigest()[:32]
    with AzureCliCredential(subscription=binding["subscription_id"], process_timeout=150) as credential:
        with group_client(binding, credential) as group:
            disk = prepare_disk_image(
                group, role="simulator", image=image["image"], source=image["source"],
                acr_name=platform["acr_name"], subscription_id=binding["subscription_id"],
                identity_client_id=binding["identity_client_id"],
                vnet_connection_name=binding.get("vnet_connection_name", ""),
            )
            volumes = {volume.name: volume for volume in group.list_volumes()}
            if VOLUME not in volumes:
                group.create_volume(VOLUME, type="DataDisk", size="5Gi", labels=labels)
            elif volumes[VOLUME].type != "DataDisk" or any(
                volumes[VOLUME].labels.get(key) != value for key, value in labels.items()
            ):
                raise ValueError("The simulator Data Disk has unexpected ownership or type.")
            existing = list(group._dp_get(group._group_path + "/sandboxes"))
            for item in existing:
                if any(item.get("labels", {}).get(key) != value for key, value in labels.items()):
                    raise ValueError("Refusing to replace an unowned simulator Sandbox.")
            if len(existing) > 1:
                raise ValueError("Multiple simulator writers exist; inspect before continuing.")
            current = existing[0] if existing else None
            if current and current["labels"].get("image") != fingerprint:
                group.get_sandbox_client(current["id"]).begin_delete(polling_timeout=600).result()
                current = None
            if current is None:
                if binding.get("private_network_enabled") and not binding.get("vnet_connection_name"):
                    raise ValueError("A private simulator requires its own native VNet connection.")
                for _ in range(90):
                    if not group.get_volume(VOLUME).is_attached:
                        break
                    time.sleep(2)
                else:
                    raise TimeoutError("Simulator Data Disk did not detach.")
                body = {
                    "sourcesRef": {"diskImage": {"id": disk}},
                    "resources": {"cpu": "1000m", "memory": "2048Mi", "disk": "10Gi"},
                    "lifecycle": {
                        "autoSuspendPolicy": {"enabled": False, "mode": "Disk"},
                        "autoDeletePolicy": {"enabled": False},
                    },
                    "labels": {**labels, "image": fingerprint},
                    "environment": {"AZURE_CLIENT_ID": binding["identity_client_id"], "PYTHONUNBUFFERED": "1"},
                    "entrypoint": ["python", "-c", BOOTSTRAP],
                    "cmd": [],
                    "volumes": [SandboxVolume(VOLUME, "/mnt/tsim")._to_dict()],
                    "ports": [{"port": 8000, "protocol": "Http", "activationMode": "OnDemand",
                               "auth": {"anonymous": True}}],
                    "skipEgressProxy": False,
                }
                if binding.get("vnet_connection_name"):
                    body["customerVnetConnectionName"] = binding["vnet_connection_name"]
                current = group._dp_put(group._group_path + "/sandboxes", body)
            sandbox = group.get_sandbox_client(current["id"])
            sandbox.ensure_running(timeout=600)
            url = next((port.url for port in sandbox.get().ports if port.port == 8000), "")
            if not url.startswith("https://"):
                raise RuntimeError("Simulator did not publish an HTTPS endpoint.")
            require_private_endpoint(url, binding)
            result = {**binding, **image, "sandbox_id": current["id"], "disk_image_id": disk, "url": url.rstrip("/")}
            save(state_path, result)
            return result


def configure_host(config: dict, binding: dict, identities: dict, agents: list[dict]) -> None:
    environment = simulator_environment(config, binding, identities, agents)
    payload = {
        "environment": environment,
        "command": config.get("simulator_command", [
            "/app/.venv/bin/python", "-m", "uvicorn", "backend.app:create_app", "--factory", "--host", "0.0.0.0",
            "--port", "8000", "--no-access-log",
        ]),
    }
    with AzureCliCredential(subscription=binding["subscription_id"], process_timeout=150) as credential:
        with group_client(binding, credential) as group:
            sandbox = group.get_sandbox_client(binding["sandbox_id"])
            sandbox.ensure_running(timeout=600)
            sandbox.write_file(CONFIG_PATH + ".next", json.dumps(payload), mode="0600")
            require_exec_success(sandbox.exec(
                "chown 10001:10001 " + shlex.quote(CONFIG_PATH + ".next")
                + " && mv " + shlex.quote(CONFIG_PATH + ".next") + " " + shlex.quote(CONFIG_PATH)),
                                 "Activating simulator settings")
    wait_health(binding["url"], "/api/health")


def wait_health(origin: str, path: str = "/health") -> dict:
    deadline = time.monotonic() + 300
    detail = ""
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get(origin.rstrip("/") + path)
                if response.status_code == 200:
                    return response.json()
                detail = f"HTTP {response.status_code}"
                if response.status_code not in {502, 503, 504}:
                    raise RuntimeError(f"Health failed: {origin}{path}: {detail}")
            except httpx.TransportError as error:
                detail = str(error)
            time.sleep(3)
    raise TimeoutError(f"Health did not become ready: {origin}{path}: {detail}")
