from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from azure.containerapps.sandbox import (
    AddPortRequest,
    SandboxGroupClient,
    SandboxVolume,
    endpoint_for_region,
)
from azure.containerapps.sandbox._models import PortAuthConfig, RegistryCredentials
from azure.core.credentials import TokenCredential
from azure.identity import DefaultAzureCredential


GATEWAY_PORT = 18789


@dataclass(frozen=True)
class GatewaySandboxConfig:
    subscription_id: str
    resource_group: str
    sandbox_group: str
    region: str
    foundry_openai_base_url: str
    model_deployment: str
    image_name: str
    customer_vnet_connection_name: str = ""
    private_incidents_mcp_url: str = ""
    private_incidents_mcp_static_key: str = "demo-static-key"
    registry_username: str = ""
    registry_password: str = ""
    acr_name: str = ""
    disk_image_id: str = ""
    disk_image_name: str = "openclaw-gateway-image-with-private-mcp"
    data_volume_name: str = "openclaw-data"
    data_volume_size: str = "20Gi"
    cpu: str = "2000m"
    memory: str = "2048Mi"
    root_disk_size: str = "20Gi"
    gateway_token: str = ""


@dataclass(frozen=True)
class GatewaySandboxResult:
    sandbox_id: str
    gateway_url: str | None
    gateway_token: str | None
    reused_existing_sandbox: bool
    data_volume: str
    openclaw_home: str
    openclaw_workspace: str
    private_incidents_mcp_url: str | None
    private_incidents_mcp_server: str | None


def run_text(command: list[str]) -> str:
    result = subprocess.run([resolve_executable(command[0]), *command[1:]], check=True, capture_output=True, text=True)
    return result.stdout.strip()


def resolve_executable(name: str) -> str:
    candidates = [name]
    if os.name == "nt" and not name.lower().endswith((".exe", ".cmd", ".bat")):
        candidates.extend([f"{name}.exe", f"{name}.cmd", f"{name}.bat"])
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    if os.name == "nt" and name == "az":
        azure_cli = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft SDKs" / "Azure" / "CLI2" / "wbin" / "az.cmd"
        if azure_cli.exists():
            return str(azure_cli)
    raise FileNotFoundError(f"Could not find executable '{name}' on PATH.")


def get_config(name: str, fallback: str = "") -> str:
    value = os.getenv(name)
    if value:
        return value
    try:
        return run_text(["azd", "env", "get-value", name])
    except (subprocess.CalledProcessError, FileNotFoundError):
        return fallback


def existing_named(items, name: str):
    for item in items:
        labels = getattr(item, "labels", None) or {}
        if getattr(item, "name", None) == name or getattr(item, "id", None) == name:
            return item
        if labels.get("name") == name:
            return item
    return None


def existing_gateway_sandbox(client: SandboxGroupClient, data_volume_name: str) -> dict | None:
    for sandbox in client._dp_get(f"{client._group_path}/sandboxes"):
        labels = sandbox.get("labels", {})
        volumes = sandbox.get("volumes", [])
        if labels.get("app") not in {"autopilots-on-azure", "openclaw-on-azure"}:
            continue
        if any(volume.get("volumeName") == data_volume_name for volume in volumes):
            return sandbox
    return None


def private_incidents_mcp_server_config(*, url: str, static_key: str) -> dict[str, Any]:
    return {
        "url": url,
        "transport": "streamable-http",
        "connectTimeout": 5,
        "timeout": 20,
        "supportsParallelToolCalls": True,
        "headers": {
            "Authorization": f"Bearer {static_key}",
        },
    }


def configure_existing_sandbox_mcp(
    sandbox_client,
    *,
    private_incidents_mcp_url: str,
    private_incidents_mcp_static_key: str,
) -> None:
    if not private_incidents_mcp_url:
        return
    server = {
        "url": private_incidents_mcp_url,
        "transport": "streamable-http",
        "connectTimeout": 5,
        "timeout": 20,
        "supportsParallelToolCalls": True,
        "headers": {
            "Authorization": f"Bearer {private_incidents_mcp_static_key}",
        },
    }
    server = private_incidents_mcp_server_config(url=private_incidents_mcp_url, static_key=private_incidents_mcp_static_key)
    import json

    command = f"""
python3 - <<'PY'
from pathlib import Path
import json

config_path = Path("/data/home/.openclaw/openclaw.json")
config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {{}}
config.setdefault("mcp", {{}}).setdefault("servers", {{}})["private-incidents"] = json.loads({json.dumps(json.dumps(server))})
tools = config.setdefault("tools", {{}})
tools.setdefault("profile", "coding")
also_allow = tools.setdefault("alsoAllow", [])
if "group:plugins" not in also_allow:
    also_allow.append("group:plugins")
config_path.parent.mkdir(parents=True, exist_ok=True)
config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
PY
"""
    result = sandbox_client.exec(command)
    if result.exit_code != 0:
        raise RuntimeError(f"Failed to configure private MCP in existing sandbox: {result.stderr or result.stdout}")


def create_gateway_sandbox(
    client: SandboxGroupClient,
    *,
    disk_id: str,
    cpu: str,
    memory: str,
    root_disk_size: str,
    token: str,
    foundry_openai_base_url: str,
    model_deployment: str,
    data_volume_name: str,
    customer_vnet_connection_name: str,
    private_incidents_mcp_url: str,
    private_incidents_mcp_static_key: str,
):
    environment = {
        "PERSISTENCE_BACKEND": "file",
        "OPENCLAW_DATA_DIR": "/data",
        "OPENCLAW_HOME_DIR": "/data/home",
        "OPENCLAW_WORKSPACE_DIR": "/data/workspace",
        "OPENCLAW_GATEWAY_TOKEN": token,
        "FOUNDRY_OPENAI_BASE_URL": foundry_openai_base_url,
        "OPENCLAW_MODEL_ID": model_deployment,
        "OPENCLAW_MODEL_PROVIDER_ID": "foundry",
        "OPENCLAW_MODEL_API": "openai-completions",
        "OPENCLAW_CONTROL_UI_DISABLE_DEVICE_AUTH": "true",
        "OPENCLAW_CONTROL_UI_ALLOW_HOST_HEADER_ORIGIN_FALLBACK": "true",
    }
    if private_incidents_mcp_url:
        environment["PRIVATE_INCIDENTS_MCP_URL"] = private_incidents_mcp_url
        environment["PRIVATE_INCIDENTS_MCP_STATIC_KEY"] = private_incidents_mcp_static_key

    body = {
        "sourcesRef": {
            "diskImage": {
                "id": disk_id,
            },
        },
        "resources": {
            "cpu": cpu,
            "memory": memory,
            "disk": root_disk_size,
        },
        "lifecycle": {
            "autoSuspendPolicy": {
                "enabled": True,
                "interval": 1800,
                "mode": "Disk",
            },
        },
        "labels": {
            "app": "autopilots-on-azure",
        },
        "environment": environment,
        "volumes": [
            SandboxVolume(data_volume_name, "/data")._to_dict(),
        ],
        "ports": [
            AddPortRequest(GATEWAY_PORT, protocol="Http", auth=PortAuthConfig(anonymous=True))._to_dict(),
        ],
        # The preview service currently honors command/args, while the Python SDK's
        # entrypoint/cmd fields are accepted but ignored by the live data plane.
        "command": [
            "python3",
        ],
        "args": [
            "-m",
            "openclaw_gateway.start_gateway",
        ],
        "skipEgressProxy": True,
    }
    if customer_vnet_connection_name:
        body["customerVnetConnectionName"] = customer_vnet_connection_name

    created = client._dp_put(f"{client._group_path}/sandboxes", body)
    sandbox_id = created["id"]
    deadline = time.time() + 600
    while time.time() < deadline:
        current = client.get_sandbox(sandbox_id)
        if current.state == "Running":
            return client.get_sandbox_client(sandbox_id), current
        if current.state in {"Failed", "Deleting"}:
            raise RuntimeError(f"Sandbox {sandbox_id} reached state {current.state}.")
        time.sleep(3)
    raise TimeoutError(f"Timed out waiting for sandbox {sandbox_id} to start.")


def create_sandbox_group_client(config: GatewaySandboxConfig, credential: TokenCredential | None = None) -> SandboxGroupClient:
    return SandboxGroupClient(
        endpoint_for_region(config.region),
        credential or DefaultAzureCredential(),
        subscription_id=config.subscription_id,
        resource_group=config.resource_group,
        sandbox_group=config.sandbox_group,
    )


def ensure_gateway_sandbox(
    config: GatewaySandboxConfig,
    *,
    credential: TokenCredential | None = None,
    wait_for_gateway_seconds: int = 20,
) -> GatewaySandboxResult:
    token = config.gateway_token or secrets.token_urlsafe(32)
    registry_username = config.registry_username
    registry_password = config.registry_password

    if not config.resource_group:
        raise ValueError("AZURE_RESOURCE_GROUP was not found. Run azd up first or set it explicitly.")
    if not config.sandbox_group:
        raise ValueError("AZURE_SANDBOX_GROUP was not found. Run azd up first or set it explicitly.")
    if not config.region:
        raise ValueError("AZURE_REGION was not found. Run azd up first or set it explicitly.")
    if not config.foundry_openai_base_url:
        raise ValueError("FOUNDRY_OPENAI_BASE_URL was not found. Run azd up first or pass --foundry-openai-base-url.")

    if not config.disk_image_id and (not registry_username or not registry_password):
        if not config.acr_name:
            raise ValueError("ACR_NAME was not found. Run azd up first or pass registry credentials.")
        registry_username = registry_username or run_text(["az", "acr", "credential", "show", "--name", config.acr_name, "--query", "username", "-o", "tsv"])
        registry_password = registry_password or run_text(["az", "acr", "credential", "show", "--name", config.acr_name, "--query", "passwords[0].value", "-o", "tsv"])

    client = create_sandbox_group_client(config, credential)

    if config.disk_image_id:
        disk_id = config.disk_image_id
    else:
        if not config.image_name or not registry_username or not registry_password:
            raise ValueError("image_name, registry_username, and registry_password are required when disk_image_id is not provided.")
        gateway_entrypoint = ["python3"]
        gateway_cmd = ["-m", "openclaw_gateway.start_gateway"]
        image = existing_named(client.list_disk_images(), config.disk_image_name)
        if image is None:
            image = client.begin_create_disk_image(
                config.image_name,
                name=config.disk_image_name,
                entrypoint=gateway_entrypoint,
                cmd=gateway_cmd,
                registry_credentials=RegistryCredentials(registry_username, registry_password),
                polling_timeout=900,
            ).result()
        disk_id = getattr(image, "id", None) or getattr(image, "name", None) or config.disk_image_name

    volume = existing_named(client.list_volumes(), config.data_volume_name)
    if volume is None:
        client.create_volume(
            config.data_volume_name,
            type="DataDisk",
            size=config.data_volume_size,
            labels={"app": "autopilots-on-azure"},
        )

    existing_sandbox = existing_gateway_sandbox(client, config.data_volume_name)
    reused_existing_sandbox = existing_sandbox is not None
    if existing_sandbox:
        sandbox_id = existing_sandbox["id"]
        sandbox_client = client.get_sandbox_client(sandbox_id)
        sandbox_client.ensure_running(timeout=600)
        configure_existing_sandbox_mcp(
            sandbox_client,
            private_incidents_mcp_url=config.private_incidents_mcp_url,
            private_incidents_mcp_static_key=config.private_incidents_mcp_static_key,
        )
        current = client.get_sandbox(sandbox_id)
    else:
        sandbox_client, current = create_gateway_sandbox(
            client,
            disk_id=disk_id,
            cpu=config.cpu,
            memory=config.memory,
            root_disk_size=config.root_disk_size,
            token=token,
            foundry_openai_base_url=config.foundry_openai_base_url,
            model_deployment=config.model_deployment,
            data_volume_name=config.data_volume_name,
            customer_vnet_connection_name=config.customer_vnet_connection_name,
            private_incidents_mcp_url=config.private_incidents_mcp_url,
            private_incidents_mcp_static_key=config.private_incidents_mcp_static_key,
        )

    sandbox_id = getattr(current, "id")
    if wait_for_gateway_seconds > 0:
        time.sleep(wait_for_gateway_seconds)
    current = sandbox_client.get()
    ports = getattr(current, "ports", []) or []
    gateway_url = None
    for port in ports:
        if getattr(port, "port", None) == GATEWAY_PORT:
            gateway_url = getattr(port, "url", None)
            break

    return GatewaySandboxResult(
        sandbox_id=sandbox_id,
        gateway_url=gateway_url,
        gateway_token=None if reused_existing_sandbox else token,
        reused_existing_sandbox=reused_existing_sandbox,
        data_volume=config.data_volume_name,
        openclaw_home="/data/home",
        openclaw_workspace="/data/workspace",
        private_incidents_mcp_url=config.private_incidents_mcp_url or None,
        private_incidents_mcp_server="private-incidents" if config.private_incidents_mcp_url else None,
    )


def config_from_environment(**overrides: Any) -> GatewaySandboxConfig:
    subscription_id = overrides.get("subscription_id") or get_config("AZURE_SUBSCRIPTION_ID") or run_text(["az", "account", "show", "--query", "id", "-o", "tsv"])
    return GatewaySandboxConfig(
        subscription_id=subscription_id,
        resource_group=overrides.get("resource_group") or get_config("AZURE_RESOURCE_GROUP"),
        sandbox_group=overrides.get("sandbox_group") or get_config("AZURE_SANDBOX_GROUP"),
        region=overrides.get("region") or get_config("AZURE_REGION", get_config("AZURE_LOCATION")),
        foundry_openai_base_url=overrides.get("foundry_openai_base_url") or get_config("FOUNDRY_OPENAI_BASE_URL"),
        model_deployment=overrides.get("model_deployment") or get_config("OPENCLAW_MODEL_ID", "gpt-5-4-mini"),
        image_name=overrides.get("image_name") or get_config("AGENT_RUNTIME_IMAGE", get_config("OPENCLAW_IMAGE")),
        customer_vnet_connection_name=overrides.get("customer_vnet_connection_name") or get_config("SANDBOX_VNET_CONNECTION_NAME"),
        private_incidents_mcp_url=overrides.get("private_incidents_mcp_url") or get_config("PRIVATE_INCIDENTS_MCP_URL"),
        private_incidents_mcp_static_key=overrides.get("private_incidents_mcp_static_key") or get_config("PRIVATE_INCIDENTS_MCP_STATIC_KEY", "demo-static-key"),
        registry_username=overrides.get("registry_username") or "",
        registry_password=overrides.get("registry_password") or "",
        acr_name=overrides.get("acr_name") or get_config("ACR_NAME"),
        disk_image_id=overrides.get("disk_image_id") or get_config("AGENT_RUNTIME_DISK_IMAGE_ID", get_config("OPENCLAW_DISK_IMAGE_ID")),
        disk_image_name=overrides.get("disk_image_name") or get_config("AGENT_RUNTIME_DISK_IMAGE_NAME", get_config("OPENCLAW_DISK_IMAGE_NAME", "openclaw-gateway-image-with-private-mcp")),
        data_volume_name=overrides.get("data_volume_name") or get_config("AGENT_RUNTIME_DATA_VOLUME_NAME", get_config("OPENCLAW_DATA_VOLUME_NAME", "openclaw-data")),
        data_volume_size=overrides.get("data_volume_size") or get_config("AGENT_RUNTIME_DATA_VOLUME_SIZE", get_config("OPENCLAW_DATA_VOLUME_SIZE", "20Gi")),
        cpu=overrides.get("cpu") or get_config("AGENT_RUNTIME_SANDBOX_CPU", get_config("OPENCLAW_SANDBOX_CPU", "2000m")),
        memory=overrides.get("memory") or get_config("AGENT_RUNTIME_SANDBOX_MEMORY", get_config("OPENCLAW_SANDBOX_MEMORY", "2048Mi")),
        root_disk_size=overrides.get("root_disk_size") or get_config("AGENT_RUNTIME_SANDBOX_ROOT_DISK_SIZE", get_config("OPENCLAW_SANDBOX_ROOT_DISK_SIZE", "20Gi")),
        gateway_token=overrides.get("gateway_token") or "",
    )
