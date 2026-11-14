from __future__ import annotations

import hashlib
import json
import shlex
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from azure.containerapps.sandbox import (
    AddPortRequest,
    SandboxGroupClient,
    SandboxVolume,
    endpoint_for_region,
)
from azure.containerapps.sandbox._models import PortAuthConfig
from azure.core.credentials import TokenCredential
from azure.identity import AzureCliCredential


HERMES_API_PORT = 8642
RUNTIME_IDLE_SECONDS = 300
RUNTIME_ACTIVATION_MODE = "OnDemand"
RUNTIME_READY_TIMEOUT_SECONDS = 300
RUNTIME_SUSPEND_MODE = "Memory"
RUNTIME_HOME = "/data/hermes"
RUNTIME_BACKUP_VOLUME = "hermes-backups"
RUNTIME_BACKUP_DIR = "/backup"
WORKER_REFRESH_PREFLIGHT_TIMEOUT_SECONDS = 300
WORKER_REFRESH_RETRY_SECONDS = 3
WORKER_REFRESH_TRANSIENT_STATUS_CODES = {502, 503, 504}
AGENT_MCP_PROXY_PORT = 18081
M365_COLLABORATION_MCP_PORT = 18082
PRIVATE_MCP_LOCAL_URL = f"http://127.0.0.1:{AGENT_MCP_PROXY_PORT}/servers/private-incidents"
PUBLIC_SHIPMENTS_LOCAL_URL = f"http://127.0.0.1:{AGENT_MCP_PROXY_PORT}/servers/public-shipments"
WORKIQ_MAIL_LOCAL_URL = f"http://127.0.0.1:{AGENT_MCP_PROXY_PORT}/servers/workiq-mail"
WORKIQ_WORD_LOCAL_URL = f"http://127.0.0.1:{AGENT_MCP_PROXY_PORT}/servers/workiq-word"
ADDITIONAL_WORKIQ_SERVERS = {
    "workiq-teams": "WORKIQ_TEAMS_MCP_URL",
    "workiq-calendar": "WORKIQ_CALENDAR_MCP_URL",
    "workiq-onedrive": "WORKIQ_ONEDRIVE_MCP_URL",
    "workiq-sharepoint": "WORKIQ_SHAREPOINT_MCP_URL",
    "workiq-excel": "WORKIQ_EXCEL_MCP_URL",
    "workiq-copilot": "WORKIQ_COPILOT_MCP_URL",
}


@dataclass(frozen=True)
class AgentSandboxConfig:
    subscription_id: str
    resource_group: str
    sandbox_group: str
    region: str
    image_name: str
    runtime_kind: str = "hermes"
    port: int = HERMES_API_PORT
    health_path: str = "/health"
    command: tuple[str, ...] = ("/app/.venv/bin/python",)
    args: tuple[str, ...] = ("/app/start_hermes.py",)
    environment: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    foundry_openai_base_url: str = ""
    model_deployment: str = ""
    customer_vnet_connection_name: str = ""
    private_incidents_mcp_url: str = ""
    private_incidents_mcp_scope: str = ""
    public_shipments_mcp_url: str = ""
    public_shipments_mcp_scope: str = ""
    workiq_mail_mcp_url: str = ""
    workiq_mail_mcp_scope: str = ""
    workiq_word_mcp_url: str = ""
    workiq_word_mcp_scope: str = ""
    additional_agent_user_mcp_servers: dict[str, dict[str, str]] = field(
        default_factory=dict
    )
    agent365_tenant_id: str = ""
    agent365_blueprint_client_id: str = ""
    agent365_agent_identity_client_id: str = ""
    agent365_agent_user_id: str = ""
    managed_identity_client_id: str = ""
    runtime_image_reference: str = ""
    disk_image_id: str = ""
    disk_image_name: str = "hermes-api-server-image"
    cpu: str = "2000m"
    memory: str = "2048Mi"
    root_disk_size: str = "20Gi"
    runtime_home: str = RUNTIME_HOME
    runtime_workspace: str = f"{RUNTIME_HOME}/workspace"
    role_blueprint: str = ""
    role_blueprint_source: str = ""
    role_blueprint_path: str = ""
    role_release: str = ""
    role_release_commit: str = ""
    worker_id: str = ""
    assignment_scope: str = ""
    collective_learning_approval_public_key: str = ""
    previous_api_server_key: str = ""
    runtime_config_revision: str = ""
    always_on: bool = False
    persistent_home: bool = False


@dataclass(frozen=True)
class AgentSandboxResult:
    sandbox_id: str
    endpoint_url: str | None
    reused_existing_sandbox: bool
    runtime_kind: str
    port: int
    health_path: str
    backup_volume: str
    runtime_home: str
    runtime_workspace: str
    private_incidents_mcp_url: str | None
    private_incidents_mcp_server: str | None

def run_text(command: list[str]) -> str:
    result = subprocess.run([resolve_executable(command[0]), *command[1:]], check=True, capture_output=True, text=True, env=os.environ.copy())
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


def existing_named(items, name: str):
    for item in items:
        labels = getattr(item, "labels", None) or {}
        if getattr(item, "name", None) == name or getattr(item, "id", None) == name:
            return item
        if labels.get("name") == name:
            return item
    return None


def runtime_labels(config: AgentSandboxConfig) -> dict[str, str]:
    labels = {
        "app": "autopilots-on-azure",
        "kind": config.runtime_kind,
        "identityArchitecture": "agent-federation-v1",
    }
    if config.role_blueprint:
        labels["roleBlueprint"] = config.role_blueprint
    if config.role_release:
        labels["roleRelease"] = config.role_release
    if config.role_release_commit:
        labels["roleReleaseCommit"] = config.role_release_commit
    if config.worker_id:
        labels["worker"] = config.worker_id
    if config.runtime_config_revision:
        labels["runtimeConfigRevision"] = config.runtime_config_revision
    if config.disk_image_name:
        labels["runtimeImage"] = config.disk_image_name
    if config.always_on:
        labels["alwaysOn"] = "true"
    if config.persistent_home:
        labels["persistentHome"] = "true"
    labels.update(config.labels)
    return labels


def mounts_backup_volume(sandbox: Mapping[str, Any], *, persistent_home: bool = False) -> bool:
    volume_name = "hermes-data" if persistent_home else RUNTIME_BACKUP_VOLUME
    return any(volume.get("volumeName") == volume_name for volume in sandbox.get("volumes") or [])


def existing_agent_sandbox(client: SandboxGroupClient, config: AgentSandboxConfig) -> dict | None:
    expected_labels = runtime_labels(config)
    for sandbox in client._dp_get(f"{client._group_path}/sandboxes"):
        labels = sandbox.get("labels", {})
        if any(labels.get(key) != value for key, value in expected_labels.items()):
            continue
        if "runtime" in labels or "autopilot" in labels:
            continue
        if mounts_backup_volume(sandbox, persistent_home=config.persistent_home):
            return sandbox
    return None


def stale_agent_sandboxes(client: SandboxGroupClient, config: AgentSandboxConfig) -> list[dict]:
    expected_labels = runtime_labels(config)
    stale = []
    for sandbox in client._dp_get(f"{client._group_path}/sandboxes"):
        labels = sandbox.get("labels", {})
        if labels.get("app") != "autopilots-on-azure":
            continue
        if labels.get("kind") != config.runtime_kind:
            continue
        if config.worker_id and labels.get("worker") != config.worker_id:
            continue
        if not mounts_backup_volume(sandbox, persistent_home=config.persistent_home):
            continue
        if any(labels.get(key) != value for key, value in expected_labels.items()):
            stale.append(sandbox)
    return stale


def publish_final_backup(client: SandboxGroupClient, sandbox: Mapping[str, Any]) -> dict[str, Any] | None:
    """Back up a runtime about to be deleted, so its replacement restores the newest Worker state.

    A `Failed` runtime cannot run commands; its replacement restores the newest automatic backup.
    """
    if str(sandbox.get("state") or "") == "Failed":
        return None
    sandbox_id = str(sandbox.get("id") or "")
    sandbox_client = client.get_sandbox_client(sandbox_id)
    sandbox_client.ensure_running(timeout=600)
    application = json.loads(sandbox_client.read_file("/app/.sandbox-service.json"))
    directory = str(application["workingDirectory"]).rstrip("/")
    if directory != "/app" and not directory.startswith("/app/releases/"):
        raise ValueError("The active runtime application directory is outside /app.")
    result = sandbox_client.exec(
        "/app/.venv/bin/python " + shlex.quote(directory + "/runtime_backup.py")
        + f" --home {RUNTIME_HOME} --backup-dir {RUNTIME_BACKUP_DIR}"
    )
    if result.exit_code != 0:
        detail = (result.stderr or result.stdout or "")[-2000:]
        raise RuntimeError(f"Final Worker backup on runtime Sandbox {sandbox_id} failed: {detail}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def recycle_failed_agent_sandbox(
    client: SandboxGroupClient,
    sandbox: dict | None,
) -> dict | None:
    if not sandbox:
        return sandbox
    state = str(sandbox.get("state") or "")
    if state != "Failed":
        return sandbox
    client.begin_delete_sandbox(
        sandbox["id"],
        polling_timeout=600,
    ).result()
    return None


def require_worker_refresh_ready(
    client: SandboxGroupClient,
    config: AgentSandboxConfig,
    sandbox: dict[str, Any],
) -> None:
    labels = sandbox.get("labels") or {}
    if labels.get("roleReleaseCommit") == config.role_release_commit:
        return
    sandbox_id = str(sandbox.get("id") or "")
    if not sandbox_id:
        raise RuntimeError("Stale Hermes Sandbox has no ID for Worker Refresh preflight.")
    sandbox_client = client.get_sandbox_client(sandbox_id)
    sandbox_client.ensure_running(timeout=600)
    current = sandbox_client.get()
    endpoint = next(
        (
            getattr(port, "url", None)
            for port in (getattr(current, "ports", []) or [])
            if getattr(port, "port", None) == HERMES_API_PORT
        ),
        None,
    )
    api_keys = [
        value
        for value in (
            config.environment.get("API_SERVER_KEY", ""),
            config.previous_api_server_key,
        )
        if value
    ]
    if not endpoint or not api_keys:
        raise RuntimeError("Worker Refresh preflight cannot reach the current Hermes Worker.")
    payload = None
    last_error: Exception | None = None
    last_detail = ""
    deadline = time.time() + WORKER_REFRESH_PREFLIGHT_TIMEOUT_SECONDS
    for api_key in api_keys:
        while time.time() < deadline:
            request = urllib.request.Request(
                f"{str(endpoint).rstrip('/')}/internal/collective-learning/refresh-ready",
                headers={"X-Autopilot-Key": api_key},
            )
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    payload = json.load(response)
                break
            except urllib.error.HTTPError as exc:
                last_error = exc
                last_detail = exc.read().decode("utf-8", errors="replace")
                if exc.code == 401:
                    break
                if exc.code not in WORKER_REFRESH_TRANSIENT_STATUS_CODES:
                    raise RuntimeError(
                        f"Worker Refresh is blocked by the current Worker ({exc.code}): {last_detail}"
                    ) from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
                last_detail = str(exc)
            time.sleep(WORKER_REFRESH_RETRY_SECONDS)
        if payload is not None:
            break
    if payload is None and last_error is not None:
        raise RuntimeError(
            f"Worker Refresh preflight did not become ready: {last_detail}"
        ) from last_error
    if not isinstance(payload, dict) or payload.get("ready") is not True:
        raise RuntimeError("Current Worker did not approve Worker Refresh.")


def agent_mcp_environment(config: AgentSandboxConfig) -> dict[str, str]:
    servers: dict[str, dict[str, str]] = {}
    environment = {
        "AGENT_MCP_PROXY_PORT": str(AGENT_MCP_PROXY_PORT),
        "AGENT365_TENANT_ID": config.agent365_tenant_id,
        "AGENT365_BLUEPRINT_CLIENT_ID": config.agent365_blueprint_client_id,
        "AGENT365_AGENT_IDENTITY_CLIENT_ID": config.agent365_agent_identity_client_id,
        "AGENT365_AGENT_USER_ID": config.agent365_agent_user_id,
    }
    if config.agent365_agent_user_id:
        environment["M365_COLLABORATION_MCP_URL"] = (
            f"http://127.0.0.1:{M365_COLLABORATION_MCP_PORT}/mcp"
        )
        environment["M365_COLLABORATION_MCP_PORT"] = str(
            M365_COLLABORATION_MCP_PORT
        )
        environment["M365_GRAPH_SCOPE"] = (
            "https://graph.microsoft.com/.default"
        )
    if config.private_incidents_mcp_url and config.private_incidents_mcp_scope:
        servers["private-incidents"] = {
            "upstreamUrl": config.private_incidents_mcp_url,
            "scope": config.private_incidents_mcp_scope,
            "identityMode": "agent",
        }
        environment["PRIVATE_INCIDENTS_MCP_URL"] = PRIVATE_MCP_LOCAL_URL
    if config.public_shipments_mcp_url and config.public_shipments_mcp_scope:
        servers["public-shipments"] = {
            "upstreamUrl": config.public_shipments_mcp_url,
            "scope": config.public_shipments_mcp_scope,
            "identityMode": "agent",
        }
        environment["PUBLIC_SHIPMENTS_MCP_URL"] = PUBLIC_SHIPMENTS_LOCAL_URL
    if config.workiq_mail_mcp_url and config.workiq_mail_mcp_scope:
        servers["workiq-mail"] = {
            "upstreamUrl": config.workiq_mail_mcp_url,
            "scope": config.workiq_mail_mcp_scope,
            "identityMode": "agent_user",
        }
        environment["WORKIQ_MAIL_MCP_URL"] = WORKIQ_MAIL_LOCAL_URL
    if config.workiq_word_mcp_url and config.workiq_word_mcp_scope:
        servers["workiq-word"] = {
            "upstreamUrl": config.workiq_word_mcp_url,
            "scope": config.workiq_word_mcp_scope,
            "identityMode": "agent_user",
        }
        environment["WORKIQ_WORD_MCP_URL"] = WORKIQ_WORD_LOCAL_URL
    for name, environment_name in ADDITIONAL_WORKIQ_SERVERS.items():
        server = config.additional_agent_user_mcp_servers.get(name) or {}
        upstream_url = str(server.get("upstreamUrl") or "")
        scope = str(server.get("scope") or "")
        if not upstream_url or not scope:
            continue
        servers[name] = {
            "upstreamUrl": upstream_url,
            "scope": scope,
            "identityMode": "agent_user",
        }
        environment[environment_name] = (
            f"http://127.0.0.1:{AGENT_MCP_PROXY_PORT}/servers/{name}"
        )
    if servers:
        required = {
            "AGENT365_TENANT_ID": config.agent365_tenant_id,
            "AGENT365_BLUEPRINT_CLIENT_ID": config.agent365_blueprint_client_id,
            "AGENT365_AGENT_IDENTITY_CLIENT_ID": config.agent365_agent_identity_client_id,
        }
        if any(
            server.get("identityMode") == "agent_user"
            for server in servers.values()
        ):
            required["AGENT365_AGENT_USER_ID"] = config.agent365_agent_user_id
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"Agent Identity MCP configuration requires: {', '.join(missing)}.")
        environment["AGENT_MCP_SERVERS_JSON"] = json.dumps(servers, separators=(",", ":"))
    return {key: value for key, value in environment.items() if value}


def runtime_telemetry_environment(
    config: AgentSandboxConfig,
    settings: Mapping[str, str],
) -> dict[str, str]:
    environment = {
        key: value
        for key in ("APPLICATIONINSIGHTS_CONNECTION_STRING", "FOUNDRY_AGENT_NAME", "OTEL_AGENT_ID",
                    "FOUNDRY_PROJECT_ENDPOINT", "OTEL_TRACES_SAMPLER_ARG")
        if (value := settings.get(key))
    }
    image = config.runtime_image_reference or config.image_name
    environment["OTEL_CONTAINER_IMAGE"] = image
    environment["OTEL_SERVICE_VERSION"] = image.split("@")[-1]
    return environment


def hermes_runtime_environment(
    *,
    api_server_key: str = "",
    foundry_openai_base_url: str = "",
    model_deployment: str = "",
    role_blueprint: str = "",
    role_blueprint_source: str = "",
    role_blueprint_path: str = "",
    role_release: str = "",
    role_release_commit: str = "",
    worker_id: str = "",
    assignment_scope: str = "",
    collective_learning_approval_public_key: str = "",
    user_scheduling_enabled: bool = False,
    scheduler_servicebus_namespace: str = "",
    scheduler_servicebus_queue: str = "",
    scheduler_max_lock_renewal_seconds: int = 1800,
    servicebus_dream_enabled: bool = False,
    servicebus_dream_cron_expression: str = "0 2 * * *",
    bridge_url: str = "",
) -> dict[str, str]:
    environment = {
        "API_SERVER_ENABLED": "true",
        "API_SERVER_HOST": "0.0.0.0",
        "API_SERVER_PORT": str(HERMES_API_PORT),
        "RUNTIME_INGRESS_PORT": str(HERMES_API_PORT),
        "HERMES_GATEWAY_PORT": "9119",
        "HERMES_HOME": RUNTIME_HOME,
        "RUNTIME_BACKUP_DIR": RUNTIME_BACKUP_DIR,
        "PATH": "/app/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "PYTHONPATH": "/app",
        "NODE_PATH": "/app/node_modules",
        "HERMES_ROLE_BLUEPRINT": role_blueprint,
        "HERMES_ROLE_BLUEPRINT_SOURCE": role_blueprint_source,
        "HERMES_ROLE_BLUEPRINT_PATH": role_blueprint_path,
        "HERMES_ROLE_RELEASE": role_release,
        "HERMES_ROLE_RELEASE_COMMIT": role_release_commit,
        "WORKER_ID": worker_id,
        "WORKER_ASSIGNMENT_SCOPE": assignment_scope,
        "COLLECTIVE_LEARNING_APPROVAL_PUBLIC_KEY": collective_learning_approval_public_key,
        "USER_SCHEDULING_ENABLED": "true" if user_scheduling_enabled else "false",
        "SCHEDULER_SERVICEBUS_NAMESPACE": scheduler_servicebus_namespace,
        "SCHEDULER_SERVICEBUS_QUEUE": scheduler_servicebus_queue,
        "SCHEDULER_MAX_LOCK_RENEWAL_SECONDS": str(
            scheduler_max_lock_renewal_seconds
        ),
        "SERVICEBUS_DREAM_ENABLED": (
            "true" if servicebus_dream_enabled else "false"
        ),
        "SERVICEBUS_DREAM_CRON_EXPRESSION": servicebus_dream_cron_expression,
        "AUTOPILOT_BRIDGE_URL": bridge_url,
    }
    if foundry_openai_base_url:
        environment["FOUNDRY_OPENAI_BASE_URL"] = foundry_openai_base_url
        environment["HERMES_MODEL_PROVIDER"] = "azure-foundry"
        environment["HERMES_MODEL"] = model_deployment or "gpt-6-1-sol"
        environment["HERMES_INFERENCE_MODEL"] = model_deployment or "gpt-6-1-sol"
    if api_server_key:
        environment["API_SERVER_KEY"] = api_server_key
    return {key: value for key, value in environment.items() if value}


def hermes_sandbox_config(**overrides: Any) -> AgentSandboxConfig:
    environment = hermes_runtime_environment(
        api_server_key=overrides.get("api_server_key") or "",
        foundry_openai_base_url=overrides.get("foundry_openai_base_url") or "",
        model_deployment=overrides.get("model_deployment") or "",
        role_blueprint=overrides.get("role_blueprint") or "",
        role_blueprint_source=overrides.get("role_blueprint_source") or "",
        role_blueprint_path=overrides.get("role_blueprint_path") or "",
        role_release=overrides.get("role_release") or "",
        role_release_commit=overrides.get("role_release_commit") or "",
        worker_id=overrides.get("worker_id") or "",
        assignment_scope=overrides.get("assignment_scope") or "",
        collective_learning_approval_public_key=overrides.get("collective_learning_approval_public_key") or "",
        user_scheduling_enabled=bool(overrides.get("user_scheduling_enabled")),
        scheduler_servicebus_namespace=overrides.get("scheduler_servicebus_namespace") or "",
        scheduler_servicebus_queue=overrides.get("scheduler_servicebus_queue") or "",
        scheduler_max_lock_renewal_seconds=int(
            overrides.get("scheduler_max_lock_renewal_seconds") or 1800
        ),
        servicebus_dream_enabled=bool(
            overrides.get("servicebus_dream_enabled")
        ),
        servicebus_dream_cron_expression=(
            overrides.get("servicebus_dream_cron_expression")
            or "0 2 * * *"
        ),
        bridge_url=overrides.get("bridge_url") or "",
    )
    config = AgentSandboxConfig(
        subscription_id=overrides.get("subscription_id") or "",
        resource_group=overrides.get("resource_group") or "",
        sandbox_group=overrides.get("sandbox_group") or "",
        region=overrides.get("region") or "",
        image_name=overrides.get("image_name") or "",
        runtime_kind="hermes",
        port=HERMES_API_PORT,
        health_path="/health",
        command=("/app/.venv/bin/python",),
        args=("/app/start_hermes.py",),
        environment=environment,
        labels=overrides.get("labels") or {},
        foundry_openai_base_url=overrides.get("foundry_openai_base_url") or "",
        model_deployment=overrides.get("model_deployment") or "",
        customer_vnet_connection_name=overrides.get("customer_vnet_connection_name") or "",
        private_incidents_mcp_url=overrides.get("private_incidents_mcp_url") or "",
        private_incidents_mcp_scope=overrides.get("private_incidents_mcp_scope") or "",
        public_shipments_mcp_url=overrides.get("public_shipments_mcp_url") or "",
        public_shipments_mcp_scope=overrides.get("public_shipments_mcp_scope") or "",
        workiq_mail_mcp_url=overrides.get("workiq_mail_mcp_url") or "",
        workiq_mail_mcp_scope=overrides.get("workiq_mail_mcp_scope") or "",
        workiq_word_mcp_url=overrides.get("workiq_word_mcp_url") or "",
        workiq_word_mcp_scope=overrides.get("workiq_word_mcp_scope") or "",
        additional_agent_user_mcp_servers=(
            overrides.get("additional_agent_user_mcp_servers") or {}
        ),
        agent365_tenant_id=overrides.get("agent365_tenant_id") or "",
        agent365_blueprint_client_id=overrides.get("agent365_blueprint_client_id") or "",
        agent365_agent_identity_client_id=overrides.get("agent365_agent_identity_client_id") or "",
        agent365_agent_user_id=overrides.get("agent365_agent_user_id") or "",
        runtime_image_reference=overrides.get("runtime_image_reference") or "",
        managed_identity_client_id=overrides.get("managed_identity_client_id") or "",
        disk_image_id=overrides.get("disk_image_id") or "",
        disk_image_name=overrides.get("disk_image_name") or "hermes-api-server-image",
        cpu=overrides.get("cpu") or "2000m",
        memory=overrides.get("memory") or "2048Mi",
        root_disk_size=overrides.get("root_disk_size") or "20Gi",
        role_blueprint=overrides.get("role_blueprint") or "",
        role_blueprint_source=overrides.get("role_blueprint_source") or "",
        role_blueprint_path=overrides.get("role_blueprint_path") or "",
        role_release=overrides.get("role_release") or "",
        role_release_commit=overrides.get("role_release_commit") or "",
        worker_id=overrides.get("worker_id") or "",
        assignment_scope=overrides.get("assignment_scope") or "",
        collective_learning_approval_public_key=overrides.get("collective_learning_approval_public_key") or "",
        previous_api_server_key=overrides.get("previous_api_server_key") or "",
    )
    environment.update(agent_mcp_environment(config))
    environment.update(
        runtime_telemetry_environment(config, overrides.get("telemetry_settings") or {})
    )
    return replace(config, runtime_config_revision=runtime_config_revision(config))


def runtime_config_revision(config: AgentSandboxConfig) -> str:
    """Fingerprint every desired runtime property that requires replacing compute."""
    desired = {
        "environment": config.environment,
        "resources": [config.cpu, config.memory, config.root_disk_size],
        "idleSeconds": RUNTIME_IDLE_SECONDS,
        "suspendMode": RUNTIME_SUSPEND_MODE,
        "volumes": {RUNTIME_BACKUP_VOLUME: RUNTIME_BACKUP_DIR},
        "activationMode": RUNTIME_ACTIVATION_MODE,
        "diskImageId": config.disk_image_id,
        "command": [*config.command, *config.args],
    }
    return hashlib.sha256(json.dumps(desired, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def create_agent_sandbox(
    client: SandboxGroupClient,
    *,
    config: AgentSandboxConfig,
    disk_id: str,
):
    environment = dict(config.environment)
    environment.setdefault("SSL_CERT_FILE", "/etc/ssl/certs/ca-certificates.crt")
    environment.setdefault("REQUESTS_CA_BUNDLE", "/etc/ssl/certs/ca-certificates.crt")
    if config.managed_identity_client_id:
        environment["AZURE_CLIENT_ID"] = config.managed_identity_client_id

    body = {
        "sourcesRef": {
            "diskImage": {
                "id": disk_id,
            },
        },
        "resources": {
            "cpu": config.cpu,
            "memory": config.memory,
            "disk": config.root_disk_size,
        },
        "lifecycle": {
            "autoSuspendPolicy": {
                "enabled": not config.always_on,
                "interval": RUNTIME_IDLE_SECONDS,
                "mode": RUNTIME_SUSPEND_MODE,
            },
        },
        "labels": runtime_labels(config),
        "environment": environment,
        "volumes": [
            SandboxVolume(RUNTIME_BACKUP_VOLUME, RUNTIME_BACKUP_DIR)._to_dict(),
        ],
        "ports": [
            AddPortRequest(
                config.port, protocol="Http", activation_mode=RUNTIME_ACTIVATION_MODE,
                auth=PortAuthConfig(anonymous=True),
            )._to_dict(),
        ],
        "entrypoint": list(config.command),
        "cmd": list(config.args),
        "skipEgressProxy": False,
    }
    if config.customer_vnet_connection_name:
        body["customerVnetConnectionName"] = config.customer_vnet_connection_name
    if config.always_on:
        body["lifecycle"]["autoDeletePolicy"] = {"enabled": False}
    if config.persistent_home:
        body["volumes"] = [SandboxVolume("hermes-data", "/data")._to_dict()]

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


def create_sandbox_group_client(config: AgentSandboxConfig, credential: TokenCredential | None = None) -> SandboxGroupClient:
    return SandboxGroupClient(
        endpoint_for_region(config.region),
        credential or AzureCliCredential(subscription=config.subscription_id, process_timeout=150),
        subscription_id=config.subscription_id,
        resource_group=config.resource_group,
        sandbox_group=config.sandbox_group,
        audience="https://management.azuredevcompute.io/.default",
        read_timeout=1800,
    )


def port_url(ports: list[Any], port: int) -> str | None:
    for item in ports or []:
        number = item.get("port") if isinstance(item, dict) else getattr(item, "port", None)
        if number == port:
            return item.get("url") if isinstance(item, dict) else getattr(item, "url", None)
    return None


def deploy_agent_sandbox(
    config: AgentSandboxConfig,
    *,
    credential: TokenCredential | None = None,
) -> AgentSandboxResult:
    """Reconcile runtime compute at deployment; later requests wake it through OnDemand ingress."""
    if not config.resource_group:
        raise ValueError("AZURE_RESOURCE_GROUP is required for runtime deployment.")
    if not config.sandbox_group:
        raise ValueError("AZURE_SANDBOX_GROUP is required for runtime deployment.")
    if not config.region:
        raise ValueError("AZURE_REGION is required for runtime deployment.")
    if not config.disk_image_id:
        raise ValueError("AGENT_RUNTIME_DISK_IMAGE_ID is required. Deploy the Sandbox disk images before starting the runtime.")
    client = create_sandbox_group_client(config, credential)
    persistent_volume = None
    if config.persistent_home:
        persistent_volume = existing_named(client.list_volumes(), "hermes-data")
        if persistent_volume is not None and (
            persistent_volume.type != "DataDisk"
            or (persistent_volume.labels or {}).get("app") != "autopilots-on-azure"
            or (persistent_volume.labels or {}).get("worker") != config.worker_id
        ):
            raise ValueError("The runtime Data Disk has unexpected type or ownership.")

    for stale in stale_agent_sandboxes(client, config):
        require_worker_refresh_ready(client, config, stale)
        if not config.persistent_home:
            publish_final_backup(client, stale)
        client.begin_delete_sandbox(stale["id"], polling_timeout=600).result()

    existing_sandbox = recycle_failed_agent_sandbox(
        client,
        existing_agent_sandbox(client, config),
    )
    reused_existing_sandbox = existing_sandbox is not None
    if existing_sandbox:
        sandbox_id = str(existing_sandbox["id"])
        endpoint_url = port_url(existing_sandbox.get("ports") or [], config.port)
    else:
        volume_name = "hermes-data" if config.persistent_home else RUNTIME_BACKUP_VOLUME
        volume = persistent_volume if config.persistent_home else existing_named(client.list_volumes(), volume_name)
        if volume is None:
            volume_labels = {"app": "autopilots-on-azure", "worker": config.worker_id}
            client.create_volume(
                volume_name,
                type="DataDisk" if config.persistent_home else "AzureBlob",
                **({"size": "10Gi"} if config.persistent_home else {}),
                labels={key: value for key, value in volume_labels.items() if value},
            )
        if config.persistent_home:
            for _ in range(90):
                if not client.get_volume(volume_name).is_attached:
                    break
                time.sleep(2)
            else:
                raise TimeoutError("Runtime Data Disk did not detach.")
        _, current = create_agent_sandbox(
            client,
            config=config,
            disk_id=config.disk_image_id,
        )
        sandbox_id = str(getattr(current, "id"))
        endpoint_url = port_url(getattr(current, "ports", []) or [], config.port)
    if not endpoint_url:
        raise RuntimeError(f"Runtime Sandbox {sandbox_id} does not publish port {config.port}.")

    return AgentSandboxResult(
        sandbox_id=sandbox_id,
        endpoint_url=str(endpoint_url).rstrip("/"),
        reused_existing_sandbox=reused_existing_sandbox,
        runtime_kind=config.runtime_kind,
        port=config.port,
        health_path=config.health_path,
        backup_volume="hermes-data" if config.persistent_home else RUNTIME_BACKUP_VOLUME,
        runtime_home=config.runtime_home,
        runtime_workspace=config.runtime_workspace,
        private_incidents_mcp_url=config.private_incidents_mcp_url or None,
        private_incidents_mcp_server="private-incidents" if config.private_incidents_mcp_url else None,
    )


def require_runtime_ready(url: str, *, timeout: int = RUNTIME_READY_TIMEOUT_SECONDS) -> float:
    """Wake runtime compute through its OnDemand port and return seconds until the wrapper is healthy."""
    started = time.monotonic()
    detail = ""
    while time.monotonic() - started < timeout:
        try:
            with urllib.request.urlopen(f"{url.rstrip('/')}/health", timeout=60) as response:
                if response.status == 200:
                    return time.monotonic() - started
                detail = f"HTTP {response.status}"
        except urllib.error.HTTPError as exc:
            detail = f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError) as exc:
            detail = str(exc)
        time.sleep(2)
    raise TimeoutError(f"Runtime at {url} did not become healthy: {detail}")


def config_from_environment(
    environment: Mapping[str, str] | None = None,
    **overrides: Any,
) -> AgentSandboxConfig:
    """Build runtime configuration from deployment settings; os.environ is only the operator default."""
    settings = os.environ if environment is None else environment

    def get_config(name: str, fallback: str = "") -> str:
        return settings.get(name) or fallback

    subscription_id = overrides.get("subscription_id") or get_config("AZURE_SUBSCRIPTION_ID") or run_text(["az", "account", "show", "--query", "id", "-o", "tsv"])
    common = {
        "subscription_id": subscription_id,
        "resource_group": overrides.get("resource_group") or get_config("AZURE_RESOURCE_GROUP"),
        "sandbox_group": overrides.get("sandbox_group") or get_config("AZURE_SANDBOX_GROUP"),
        "region": overrides.get("region") or get_config("AZURE_REGION", get_config("AZURE_LOCATION")),
        "image_name": (
            overrides.get("image_name")
            or get_config("AGENT_RUNTIME_IMAGE")
        ),
        "runtime_image_reference": overrides.get("runtime_image_reference") or overrides.get("image_name") or get_config("AGENT_RUNTIME_IMAGE"),
        "customer_vnet_connection_name": overrides.get("customer_vnet_connection_name") or get_config("SANDBOX_VNET_CONNECTION_NAME"),
        "private_incidents_mcp_url": overrides.get("private_incidents_mcp_url") or get_config("PRIVATE_INCIDENTS_MCP_URL"),
        "private_incidents_mcp_scope": overrides.get("private_incidents_mcp_scope") or get_config("PRIVATE_INCIDENTS_MCP_SCOPE"),
        "public_shipments_mcp_url": overrides.get("public_shipments_mcp_url") or get_config("PUBLIC_SHIPMENTS_MCP_UPSTREAM_URL"),
        "public_shipments_mcp_scope": overrides.get("public_shipments_mcp_scope") or get_config("PUBLIC_SHIPMENTS_MCP_SCOPE"),
        "workiq_mail_mcp_url": overrides.get("workiq_mail_mcp_url") or get_config("WORKIQ_MAIL_MCP_UPSTREAM_URL"),
        "workiq_mail_mcp_scope": overrides.get("workiq_mail_mcp_scope") or get_config("WORKIQ_MAIL_MCP_SCOPE"),
        "workiq_word_mcp_url": overrides.get("workiq_word_mcp_url") or get_config("WORKIQ_WORD_MCP_UPSTREAM_URL"),
        "workiq_word_mcp_scope": overrides.get("workiq_word_mcp_scope") or get_config("WORKIQ_WORD_MCP_SCOPE"),
        "additional_agent_user_mcp_servers": {
            name: {
                "upstreamUrl": get_config(
                    f"{environment_name.removesuffix('_MCP_URL')}_MCP_UPSTREAM_URL"
                ),
                "scope": get_config(
                    f"{environment_name.removesuffix('_MCP_URL')}_MCP_SCOPE"
                ),
            }
            for name, environment_name in ADDITIONAL_WORKIQ_SERVERS.items()
        },
        "agent365_tenant_id": overrides.get("agent365_tenant_id") or get_config("AGENT365_TENANT_ID"),
        "agent365_blueprint_client_id": overrides.get("agent365_blueprint_client_id") or get_config("AGENT365_BLUEPRINT_CLIENT_ID", get_config("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID")),
        "agent365_agent_identity_client_id": overrides.get("agent365_agent_identity_client_id") or get_config("AGENT365_AGENT_IDENTITY_CLIENT_ID"),
        "agent365_agent_user_id": overrides.get("agent365_agent_user_id") or get_config("AGENT365_AGENT_USER_ID"),
        "user_scheduling_enabled": overrides.get("user_scheduling_enabled") if "user_scheduling_enabled" in overrides else get_config("USER_SCHEDULING_ENABLED").lower() in {"1", "true", "yes"},
        "scheduler_servicebus_namespace": overrides.get("scheduler_servicebus_namespace") or get_config("SCHEDULER_SERVICEBUS_NAMESPACE"),
        "scheduler_servicebus_queue": overrides.get("scheduler_servicebus_queue") or get_config("SCHEDULER_SERVICEBUS_QUEUE"),
        "scheduler_max_lock_renewal_seconds": int(
            overrides.get("scheduler_max_lock_renewal_seconds")
            or get_config("SCHEDULER_MAX_LOCK_RENEWAL_SECONDS", "1800")
        ),
        "servicebus_dream_enabled": (
            overrides.get("servicebus_dream_enabled")
            if "servicebus_dream_enabled" in overrides
            else get_config("SERVICEBUS_DREAM_ENABLED").lower()
            in {"1", "true", "yes"}
        ),
        "servicebus_dream_cron_expression": (
            overrides.get("servicebus_dream_cron_expression")
            or get_config(
                "SERVICEBUS_DREAM_CRON_EXPRESSION",
                "0 2 * * *",
            )
        ),
        "managed_identity_client_id": overrides.get("managed_identity_client_id") or get_config("AGENT_RUNTIME_MANAGED_IDENTITY_CLIENT_ID"),
        "disk_image_id": overrides.get("disk_image_id") or get_config("AGENT_RUNTIME_DISK_IMAGE_ID"),
        "disk_image_name": overrides.get("disk_image_name") or get_config("AGENT_RUNTIME_DISK_IMAGE_NAME"),
        "cpu": overrides.get("cpu") or get_config("AGENT_RUNTIME_SANDBOX_CPU", "2000m"),
        "memory": overrides.get("memory") or get_config("AGENT_RUNTIME_SANDBOX_MEMORY", "2048Mi"),
        "root_disk_size": overrides.get("root_disk_size") or get_config("AGENT_RUNTIME_SANDBOX_ROOT_DISK_SIZE", "20Gi"),
    }
    return hermes_sandbox_config(
        **common,
        bridge_url=overrides.get("bridge_url") or get_config("AUTOPILOT_BRIDGE_URL"),
        telemetry_settings=settings,
        foundry_openai_base_url=overrides.get("foundry_openai_base_url") or get_config("FOUNDRY_OPENAI_BASE_URL"),
        model_deployment=overrides.get("model_deployment") or get_config("HERMES_MODEL", "gpt-6-1-sol"),
        api_server_key=overrides.get("api_server_key") or get_config("API_SERVER_KEY"),
        role_blueprint=overrides.get("role_blueprint") or get_config("HERMES_ROLE_BLUEPRINT"),
        role_blueprint_source=overrides.get("role_blueprint_source") or get_config("HERMES_ROLE_BLUEPRINT_SOURCE"),
        role_blueprint_path=overrides.get("role_blueprint_path") or get_config("HERMES_ROLE_BLUEPRINT_PATH"),
        role_release=overrides.get("role_release") or get_config("HERMES_ROLE_RELEASE"),
        role_release_commit=overrides.get("role_release_commit") or get_config("HERMES_ROLE_RELEASE_COMMIT"),
        worker_id=overrides.get("worker_id") or get_config("WORKER_ID", get_config("AUTOPILOT_NAME")),
        assignment_scope=overrides.get("assignment_scope") or get_config("WORKER_ASSIGNMENT_SCOPE"),
        collective_learning_approval_public_key=(
            overrides.get("collective_learning_approval_public_key")
            or get_config("COLLECTIVE_LEARNING_APPROVAL_PUBLIC_KEY")
        ),
        previous_api_server_key=(
            overrides.get("previous_api_server_key")
            or get_config("PREVIOUS_API_SERVER_KEY")
        ),
    )
