from __future__ import annotations

import hashlib
import json
import shlex
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from azure.containerapps.sandbox import SandboxGroupClient, endpoint_for_region
from azure.containerapps.sandbox._models import RegistryCredentials
from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential

from scripts.sandbox_runtime import existing_named, run_text


SERVICE_CONFIG_PATH = "/app/.sandbox-service.json"
BOOTSTRAP = """
import json, os, pathlib, time
path = pathlib.Path("/app/.sandbox-service.json")
deadline = time.monotonic() + 900
while not path.exists():
    if time.monotonic() > deadline:
        raise RuntimeError("Sandbox deployment did not supply service configuration")
    time.sleep(1)
config = json.loads(path.read_text())
os.environ.update(config["environment"])
os.chdir("/app")
log = os.open("/app/.sandbox-service.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
os.dup2(log, 1)
os.dup2(log, 2)
os.close(log)
os.execvp(config["command"][0], config["command"])
""".strip()


@dataclass(frozen=True)
class SandboxService:
    role: str
    image: str
    port: int
    command: tuple[str, ...]
    environment: dict[str, str]
    auto_suspend: bool = True
    disk_source_image: str = ""


def prepare_disk_image(client: Any, *, role: str, image: str, source: str, acr_name: str,
                       subscription_id: str) -> str:
    name = f"{role}-{hashlib.sha256(image.encode()).hexdigest()[:20]}"
    disk = existing_named(client.list_disk_images(), name)
    if disk is None:
        registry = f"{acr_name}.azurecr.io/"
        if not image.startswith(registry) or "@sha256:" not in image or not source.startswith(registry):
            raise ValueError(f"{role} requires an ACR digest and a conversion source in the same registry.")
        repository, expected_digest = image[len(registry):].split("@", 1)
        source_repository = source[len(registry):]
        if source_repository.split(":", 1)[0] != repository:
            raise ValueError(f"{role} conversion source must use the deployed image repository.")
        actual_digest = run_text([
            "az", "acr", "repository", "show", "--name", acr_name, "--subscription", subscription_id,
            "--image", source_repository, "--query", "digest", "--output", "tsv", "--only-show-errors",
        ])
        if actual_digest != expected_digest:
            raise ValueError(f"{role} conversion source does not match the deployed image digest.")
        token = run_text([
            "az", "acr", "login", "--name", acr_name, "--subscription", subscription_id,
            "--expose-token", "--query", "accessToken", "--output", "tsv", "--only-show-errors",
        ])
        if not token:
            raise RuntimeError("ACR login returned an empty token.")
        disk = client.begin_create_disk_image(
            source, name=name,
            registry_credentials=RegistryCredentials("00000000-0000-0000-0000-000000000000", token),
            polling_timeout=900,
        ).result()
    if disk.status.state not in {"Ready", "Succeeded"}:
        raise RuntimeError(f"{role} disk image {disk.id} is not ready: {disk.status.state}")
    return disk.id


def text(value: Any) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


def gateway_environment(config: dict, platform: dict, infrastructure: dict, endpoints: dict) -> dict[str, str]:
    groups = infrastructure["sandbox_groups"]
    runtime = groups["runtime"]
    gateway = groups["gateway"]
    tenant = config["agent365_tenant_id"] or infrastructure["tenant_id"]
    environment = {
        key.upper(): text(value)
        for key, value in config.items()
        if key.startswith(("hermes_role_", "scheduled_learning_", "servicebus_dream_", "collective_learning_"))
    }
    environment.update({
        "AGENT_RUNTIME": config["agent_runtime"],
        "AUTOPILOT_NAME": config["autopilot_name"],
        "WORKER_ID": config["autopilot_name"],
        "WORKER_ASSIGNMENT_SCOPE": config["worker_assignment_scope"],
        "AZURE_TENANT_ID": tenant,
        "AZURE_SUBSCRIPTION_ID": infrastructure["subscription_id"],
        "AZURE_RESOURCE_GROUP": infrastructure["resource_group_name"],
        "AZURE_REGION": infrastructure["sandbox_location"],
        "AZURE_CLIENT_ID": gateway["identity_client_id"],
        "AZURE_SANDBOX_GROUP": runtime["name"],
        "SANDBOX_VNET_CONNECTION_NAME": runtime["vnet_connection_name"],
        "AGENT_RUNTIME_MANAGED_IDENTITY_CLIENT_ID": runtime["identity_client_id"],
        "AGENT_RUNTIME_IMAGE": config["runtime_image"],
        "AGENT_RUNTIME_DISK_IMAGE_ID": endpoints["runtime_disk_image_id"],
        "AGENT_RUNTIME_DISK_IMAGE_NAME": config["runtime_disk_image_name"],
        "AGENT_RUNTIME_DATA_VOLUME_NAME": config["runtime_data_volume_name"],
        "OPENCLAW_IMAGE": config["runtime_image"],
        "OPENCLAW_DISK_IMAGE_NAME": config["runtime_disk_image_name"],
        "OPENCLAW_DATA_VOLUME_NAME": config["runtime_data_volume_name"],
        "OPENCLAW_GATEWAY_TOKEN": config["openclaw_gateway_token"],
        "OPENCLAW_BRIDGE_DEVICE_TOKEN": config["openclaw_bridge_device_token"],
        "OPENCLAW_BRIDGE_DEVICE_PRIVATE_KEY_PEM": config["openclaw_bridge_device_private_key_pem"],
        "API_SERVER_KEY": config["api_server_key"],
        "HERMES_API_SERVER_KEY": config["api_server_key"],
        "PREVIOUS_API_SERVER_KEY": config["previous_api_server_key"],
        "HERMES_BRIDGE_TIMEOUT_SECONDS": "900",
        "FOUNDRY_OPENAI_BASE_URL": platform["foundry_openai_base_url"],
        "APPLICATIONINSIGHTS_CONNECTION_STRING": platform["application_insights_connection_string"],
        "FOUNDRY_PROJECT_ENDPOINT": platform["foundry_project_endpoint"],
        "FOUNDRY_AGENT_NAME": infrastructure["foundry_agent_name"],
        "OTEL_AGENT_ID": infrastructure["foundry_agent_name"],
        "OTEL_TRACES_SAMPLER_ARG": "1.0",
        "OTEL_SERVICE_VERSION": config["bridge_image"].split("@")[-1],
        "OTEL_CONTAINER_IMAGE": config["bridge_image"],
        "OPENCLAW_MODEL_ID": platform["model_deployment_name"],
        "GENERATED_APPS_SANDBOX_GROUP": groups["generated-apps"]["name"],
        "GENERATED_APPS_REGION": infrastructure["sandbox_location"],
        "PRIVATE_INCIDENTS_MCP_URL": endpoints["private_mcp_url"],
        "PRIVATE_INCIDENTS_MCP_SCOPE": config["private_mcp_api_audience"] + "/.default",
        "PUBLIC_SHIPMENTS_MCP_UPSTREAM_URL": endpoints["public_shipments_mcp_url"],
        "PUBLIC_SHIPMENTS_MCP_SCOPE": config["public_shipments_mcp_api_audience"] + "/.default",
        "AGENT365_TENANT_ID": tenant,
        "AGENT365_BLUEPRINT_CLIENT_ID": config["agent365_client_id"],
        "AGENT365_AGENT_IDENTITY_CLIENT_ID": config["agent365_agent_identity_client_id"],
        "AGENT365_AGENT_USER_ID": config["agent365_agent_user_id"],
        "AGENT365_AGENT_USER_PRINCIPAL_NAME": config["agent365_agent_user_principal_name"],
        "USE_AGENTIC_AUTH": "true",
        "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID": config["agent365_client_id"],
        "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID": tenant,
        "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__AUTHTYPE": "FederatedCredentials",
        "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__FEDERATEDCLIENTID": gateway["identity_client_id"],
        "USER_SCHEDULING_ENABLED": text(config["user_scheduling_enabled"]),
        "DOCUMENT_RETRY_ENABLED": text(config["document_retry_enabled"]),
        "SCHEDULER_SERVICEBUS_NAMESPACE": platform["scheduler_servicebus_fully_qualified_namespace"],
        "SCHEDULER_SERVICEBUS_QUEUE": infrastructure["scheduler_servicebus_queue_name"],
        "SCHEDULER_MAX_LOCK_RENEWAL_SECONDS": text(config["user_scheduling_lock_renewal_seconds"]),
        "SCHEDULER_MAX_DELIVERY_COUNT": text(config["user_scheduling_max_delivery_count"]),
    })
    for name, value in config.items():
        if name.startswith("workiq_"):
            key = name.upper().replace("_MCP_URL", "_MCP_UPSTREAM_URL")
            environment[key] = text(value)
    environment["RUNTIME_CONFIG_REVISION"] = hashlib.sha256(
        json.dumps(environment, sort_keys=True).encode()
    ).hexdigest()[:16]
    return environment


def mcp_service(role: str, config: dict, infrastructure: dict) -> SandboxService:
    private = role == "private-mcp"
    audience = config["private_mcp_api_audience" if private else "public_shipments_mcp_api_audience"]
    if not audience:
        raise ValueError(f"{role} requires its existing Entra API audience.")
    tenant = infrastructure["tenant_id"]
    environment = {
        "HOST": "0.0.0.0",
        "PORT": "8765",
        "MCP_AUTH_MODE": "entra_agent_identity" if private else "entra",
        "MCP_JWKS_URL": f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys",
        "MCP_JWT_ISSUER": f"https://login.microsoftonline.com/{tenant}/v2.0",
        "MCP_JWT_AUDIENCE": audience.removeprefix("api://"),
    }
    if private:
        environment.update({
            "MCP_REQUIRED_ROLES": "Incidents.Read.All",
            "MCP_ALLOWED_CLIENT_IDS": config["agent365_agent_identity_client_id"],
            "MCP_ALLOWED_OBJECT_IDS": config["agent365_agent_identity_object_id"],
        })
    else:
        environment.update({"MCP_ALLOWED_SCOPES": "Shipments.Read", "MCP_ALLOWED_ROLES": "Shipments.Read.All"})
    return SandboxService(
        role=role,
        image=config["private_mcp_image" if private else "public_shipments_mcp_image"],
        disk_source_image=config["private_mcp_disk_source_image" if private else "public_shipments_mcp_disk_source_image"],
        port=8765,
        command=("/app/.venv/bin/python", "-m", "private_incidents_mcp.server" if private else "public_shipments_mcp.server"),
        environment=environment,
    )


def service_body(service: SandboxService, group: dict, disk_id: str, worker: str) -> dict:
    body = {
        "sourcesRef": {"diskImage": {"id": disk_id}},
        "resources": {"cpu": "500m", "memory": "1024Mi", "disk": "10Gi"},
        "lifecycle": {"autoSuspendPolicy": {"enabled": service.auto_suspend, "interval": 1800, "mode": "Disk"}},
        "labels": {"app": "autopilots-on-azure", "worker": worker, "service": service.role,
                   "image": hashlib.sha256(service.image.encode()).hexdigest()[:32]},
        "environment": {"SSL_CERT_FILE": "/etc/ssl/certs/ca-certificates.crt",
                        "REQUESTS_CA_BUNDLE": "/etc/ssl/certs/ca-certificates.crt",
                        "AZURE_CLIENT_ID": group["identity_client_id"]},
        "ports": [{"port": service.port, "protocol": "Http", "auth": {"anonymous": True}}],
        "entrypoint": ["python3", "-c", BOOTSTRAP],
        "cmd": [],
        "skipEgressProxy": False,
    }
    if group["vnet_connection_name"]:
        body["customerVnetConnectionName"] = group["vnet_connection_name"]
    body["labels"]["deployment"] = hashlib.sha256(
        json.dumps(body, sort_keys=True).encode()
    ).hexdigest()[:32]
    return body


def require_exec_success(result: Any, operation: str) -> None:
    if result.exit_code != 0:
        raise RuntimeError(f"{operation} failed: {result.stderr}")


def require_service_health(client: Any, port: int) -> None:
    probe = (
        "import urllib.request; "
        f"r=urllib.request.urlopen('http://127.0.0.1:{port}/health', timeout=5); "
        "assert r.status == 200"
    )
    deadline = time.monotonic() + 180
    detail = ""
    while time.monotonic() < deadline:
        result = client.exec("python3 -c " + shlex.quote(probe))
        if result.exit_code == 0:
            return
        detail = result.stderr
        time.sleep(3)
    raise TimeoutError(f"Sandbox service on port {port} did not become healthy: {detail}")


def write_service_settings(sandbox: Any, settings: dict) -> None:
    sandbox.write_file(SERVICE_CONFIG_PATH + ".next", json.dumps(settings), mode="0600")
    require_exec_success(
        sandbox.exec(f"mv {SERVICE_CONFIG_PATH}.next {SERVICE_CONFIG_PATH}"), "Activating service configuration"
    )


def deploy_service(client: Any, service: SandboxService, group: dict, worker: str, *, disk_image_id: str) -> dict:
    if not disk_image_id:
        raise ValueError("A deployment-prepared disk image ID is required.")
    image_hash = hashlib.sha256(service.image.encode()).hexdigest()[:32]
    body = service_body(service, group, disk_image_id, worker)
    existing = [
        item for item in client._dp_get(f"{client._group_path}/sandboxes")
        if item.get("labels", {}).get("worker") == worker
        and item.get("labels", {}).get("service") == service.role
    ]
    matching = [
        item for item in existing
        if item.get("labels", {}).get("image") == image_hash
        and item.get("labels", {}).get("deployment") == body["labels"]["deployment"]
        and client.get_sandbox_client(item["id"]).get().state != "Failed"
    ]
    if len(matching) > 1:
        raise RuntimeError(f"Multiple current {service.role} Sandboxes exist; refusing ambiguous deployment.")
    old_clients = []
    sandbox = None
    created = not matching
    previous = None
    config_changed = False
    try:
        if matching:
            sandbox = client.get_sandbox_client(matching[0]["id"])
        for item in existing:
            if matching and item["id"] == matching[0]["id"]:
                continue
            old = client.get_sandbox_client(item["id"])
            state = old.get().state
            if state not in {"Running", "Stopped", "Suspended", "Failed"}:
                raise RuntimeError(f"Previous {service.role} Sandbox is still transitioning: {state}")
            was_running = state == "Running"
            old_clients.append((old, was_running))
            if was_running:
                old.begin_stop(polling_timeout=300).result()
        if created:
            result = client._dp_put(
                f"{client._group_path}/sandboxes", body
            )
            sandbox = client.get_sandbox_client(result["id"])
        sandbox.ensure_running(timeout=600)
        current = sandbox.get()
        endpoint = next((p.url for p in current.ports if p.port == service.port), None)
        if not endpoint or urlsplit(endpoint).scheme != "https":
            raise RuntimeError(f"{service.role} has no native HTTPS port URL.")
        environment = dict(service.environment)
        if service.role == "gateway":
            environment["AUTOPILOT_BRIDGE_URL"] = endpoint.rstrip("/")
        settings = {"environment": environment, "command": list(service.command)}
        if not created:
            try:
                previous = json.loads(sandbox.read_file(SERVICE_CONFIG_PATH))
            except ResourceNotFoundError:
                pass
        if previous != settings:
            write_service_settings(sandbox, settings)
            config_changed = True
            if not created:
                sandbox.begin_stop(polling_timeout=300).result()
                sandbox.ensure_running(timeout=600)
        require_service_health(sandbox, service.port)
    except (AzureError, OSError, ValueError, RuntimeError) as exc:
        try:
            if created and sandbox is not None:
                sandbox.begin_delete(polling_timeout=600).result()
            elif config_changed and previous is not None:
                write_service_settings(sandbox, previous)
                sandbox.begin_stop(polling_timeout=300).result()
                sandbox.ensure_running(timeout=600)
        except (AzureError, OSError, ValueError, RuntimeError) as cleanup_error:
            exc.add_note(f"Sandbox rollback failed: {cleanup_error}")
        for old, was_running in old_clients:
            if not was_running:
                continue
            try:
                old.ensure_running(timeout=600)
            except (AzureError, OSError, RuntimeError) as restart_error:
                exc.add_note(f"Restarting previous Sandbox failed: {restart_error}")
        raise
    for old, _ in old_clients:
        old.begin_delete(polling_timeout=600).result()
    return {"sandbox_id": sandbox.sandbox_id, "url": endpoint.rstrip("/"),
            "image": service.image, "disk_source_image": service.disk_source_image, "disk_image_id": disk_image_id}


def deploy_sandbox_services(infrastructure: dict, platform: dict) -> dict:
    config = infrastructure["deployment_config"]
    required_identity = ("agent365_client_id", "agent365_agent_identity_client_id",
                         "agent365_agent_identity_object_id", "agent365_agent_user_id")
    if any(not config.get(key) for key in required_identity):
        raise ValueError("Public gateway deployment requires the existing Agent 365 blueprint, Agent Identity and Agent User.")
    if infrastructure["private_ingress"].get("publicNetworkAccess") != "Disabled":
        raise RuntimeError("Private MCP Sandbox Group does not report disabled public ingress.")
    groups = infrastructure["sandbox_groups"]
    roles = ("runtime", "gateway", "private-mcp", "public-mcp", "generated-apps")
    if len({groups[role]["name"] for role in roles}) != len(roles):
        raise ValueError("Runtime, gateway and tool roles require separate Sandbox Groups.")
    if len({groups[role]["identity_client_id"] for role in roles}) != len(roles):
        raise ValueError("Sandbox role groups must not share workload identities.")
    for name in ("bridge_image", "private_mcp_image", "public_shipments_mcp_image", "runtime_image",
                 "runtime_disk_source_image", "bridge_disk_source_image", "private_mcp_disk_source_image", "public_shipments_mcp_disk_source_image"):
        if not config.get(name):
            raise ValueError(f"{name} is required before Sandbox deployment.")
    endpoints: dict[str, Any] = {}
    services = {}
    with DefaultAzureCredential(process_timeout=120) as credential:
        disks = {}
        for role, prefix in (("runtime", "runtime"), ("private-mcp", "private_mcp"),
                             ("public-mcp", "public_shipments_mcp"), ("gateway", "bridge")):
            with SandboxGroupClient(
                endpoint_for_region(infrastructure["sandbox_location"]), credential,
                subscription_id=infrastructure["subscription_id"],
                resource_group=infrastructure["resource_group_name"], sandbox_group=groups[role]["name"],
            ) as client:
                disks[role] = prepare_disk_image(
                    client, role=role, image=config[prefix + "_image"], source=config[prefix + "_disk_source_image"],
                    acr_name=platform["acr_name"], subscription_id=infrastructure["subscription_id"],
                )
        endpoints["runtime_disk_image_id"] = disks["runtime"]
        for role in ("private-mcp", "public-mcp", "gateway"):
            group = infrastructure["sandbox_groups"][role]
            service = mcp_service(role, config, infrastructure) if role != "gateway" else SandboxService(
                role="gateway", image=config["bridge_image"], port=8000,
                disk_source_image=config["bridge_disk_source_image"],
                command=("/app/.venv/bin/python", "-m", "uvicorn", "bridge.app:app", "--host", "0.0.0.0", "--port", "8000"),
                environment=gateway_environment(config, platform, infrastructure, endpoints),
                auto_suspend=False,
            )
            with SandboxGroupClient(
                endpoint_for_region(infrastructure["sandbox_location"]), credential,
                subscription_id=infrastructure["subscription_id"],
                resource_group=infrastructure["resource_group_name"], sandbox_group=group["name"],
            ) as client:
                result = deploy_service(client, service, group, infrastructure["worker_id"], disk_image_id=disks[role])
                services[role] = result
                prefix = {"private-mcp": "private_mcp", "public-mcp": "public_shipments_mcp", "gateway": "bridge"}[role]
                endpoints[prefix + "_url"] = result["url"] + ("/mcp" if role != "gateway" else "")
                endpoints[prefix + "_fqdn"] = urlsplit(result["url"]).hostname
                endpoints[prefix + "_sandbox_id"] = result["sandbox_id"]
    return {**endpoints, "sandbox_services": services}
