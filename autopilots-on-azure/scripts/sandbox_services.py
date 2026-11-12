from __future__ import annotations

import hashlib
import json
import re
import shlex
import time
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit

from azure.containerapps.sandbox import SandboxGroupClient, endpoint_for_region
from azure.containerapps.sandbox import SandboxVolume
from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.core.rest import HttpRequest
from azure.identity import AzureCliCredential

from scripts.sandbox_runtime import (
    config_from_environment,
    deploy_agent_sandbox,
    existing_named,
    require_runtime_ready,
    run_text,
)
from scripts.application_releases import (
    PYTHON_BASE_IMAGE, ApplicationRelease, build_release, install_release,
)


DISK_IMAGE_AUDIENCE = "https://management.azuredevcompute.io/.default"

SERVICE_CONFIG_PATH = "/app/.sandbox-service.json"
BOOTSTRAP = """
import json, os, pathlib, subprocess, time
path = pathlib.Path("/app/.sandbox-service.json")
deadline = time.monotonic() + 900
while not path.exists():
    if time.monotonic() > deadline:
        raise RuntimeError("Sandbox deployment did not supply service configuration")
    time.sleep(1)
with open("/app/.sandbox-service.log", "a") as log:
    previous = ""
    process = None
    while True:
        content = path.read_text()
        if content != previous:
            config = json.loads(content)
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=45)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=15)
            environment = dict(os.environ)
            environment.update(config["environment"])
            process = subprocess.Popen(
                config["command"], cwd=config["workingDirectory"],
                env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            )
            previous = content
        if process.poll() is not None:
            raise RuntimeError("Application process exited: " + str(process.returncode))
        time.sleep(1)
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
    release: ApplicationRelease | None = None


def disk_image_request(source: str, name: str, identity_client_id: str) -> dict:
    """Native-v2 registry conversion authenticated by the Sandbox Group's managed identity."""
    return {
        "source": {"kind": "registry", "imageUrl": source, "managedIdentityClientId": identity_client_id},
        "labels": {"name": name},
    }


def convert_disk_image(client: Any, body: dict, *, timeout_seconds: int = 900, poll_seconds: int = 5) -> str:
    submitted = client._send_request(HttpRequest(
        "PUT", f"{client._endpoint}{client._group_path}/diskimages/v2/async", json=body,
    )).json()
    operation_id = str(submitted.get("operationId") or "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", operation_id):
        raise RuntimeError("Disk image conversion did not return an operation ID.")
    deadline = time.monotonic() + timeout_seconds
    while True:
        result = client._send_request(HttpRequest(
            "GET", f"{client._endpoint}{client._group_path}/diskimages/operations/{operation_id}",
        )).json()
        status = result.get("status")
        if status == "Succeeded":
            disk_id = str((result.get("diskImage") or {}).get("id") or "")
            if not disk_id:
                raise RuntimeError(f"Disk image conversion {operation_id} succeeded without a disk image ID.")
            return disk_id
        if status == "Failed":
            raise RuntimeError(
                f"Disk image conversion {operation_id} failed: {result.get('errorMessage') or 'no detail'}. "
                "The Sandbox Group identity needs AcrPull on the registry; a new assignment can take minutes to apply."
            )
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Disk image conversion {operation_id} is still {status}; inspect it before retrying.")
        time.sleep(poll_seconds)


def prepare_disk_image(client: Any, *, role: str, image: str, source: str, acr_name: str,
                       subscription_id: str, identity_client_id: str) -> str:
    name = f"{role}-{hashlib.sha256(image.encode()).hexdigest()[:20]}"
    disk = existing_named(client.list_disk_images(), name)
    if disk is None:
        if image == PYTHON_BASE_IMAGE and role != "runtime":
            disk = client.begin_create_disk_image(
                image, name=name, polling_timeout=900,
            ).result()
            if not disk.image or disk.image.base != image:
                raise RuntimeError(f"{role} disk conversion did not retain the pinned Python source.")
            if disk.status.state not in {"Ready", "Succeeded"}:
                raise RuntimeError(f"{role} disk image is not ready: {disk.status.state}")
            return disk.id
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
        disk = client.get_disk_image(convert_disk_image(client, disk_image_request(source, name, identity_client_id)))
    if disk.status.state not in {"Ready", "Succeeded"}:
        raise RuntimeError(f"{role} disk image {disk.id} is not ready: {disk.status.state}")
    return disk.id


def text(value: Any) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


def worker_settings(config: dict, platform: dict, infrastructure: dict, endpoints: dict) -> dict[str, str]:
    """All Worker deployment settings; the gateway receives only its own subset."""
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
        "API_SERVER_KEY": config["api_server_key"],
        "PREVIOUS_API_SERVER_KEY": config["previous_api_server_key"],
        "HERMES_BRIDGE_TIMEOUT_SECONDS": "900",
        "FOUNDRY_OPENAI_BASE_URL": platform["foundry_openai_base_url"],
        "APPLICATIONINSIGHTS_CONNECTION_STRING": platform["application_insights_connection_string"],
        "FOUNDRY_PROJECT_ENDPOINT": platform["foundry_project_endpoint"],
        "FOUNDRY_AGENT_NAME": infrastructure["foundry_agent_name"],
        "OTEL_AGENT_ID": infrastructure["foundry_agent_name"],
        "OTEL_TRACES_SAMPLER_ARG": "1.0",
        "OTEL_SERVICE_VERSION": PYTHON_BASE_IMAGE.split("@")[-1],
        "OTEL_CONTAINER_IMAGE": PYTHON_BASE_IMAGE,
        "HERMES_MODEL": platform["model_deployment_name"],
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
        "WORKER_OWNER_ID": config["worker_owner_id"],
        "USE_AGENTIC_AUTH": "true",
        "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID": config["agent365_client_id"],
        "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID": tenant,
        "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__AUTHTYPE": "FederatedCredentials",
        "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__FEDERATEDCLIENTID": gateway["identity_client_id"],
        "USER_SCHEDULING_ENABLED": text(config["user_scheduling_enabled"]),
        "DOCUMENT_RETRY_ENABLED": text(config["document_retry_enabled"]),
        "SERVICEBUS_DREAM_ENABLED": text(config["servicebus_dream_enabled"]),
        "SERVICEBUS_DREAM_CRON_EXPRESSION": config["servicebus_dream_cron_expression"],
        "SCHEDULER_SERVICEBUS_NAMESPACE": platform["scheduler_servicebus_fully_qualified_namespace"],
        "SCHEDULER_SERVICEBUS_QUEUE": infrastructure["scheduler_servicebus_queue_name"],
        "SCHEDULER_MAX_LOCK_RENEWAL_SECONDS": text(config["user_scheduling_lock_renewal_seconds"]),
        "SCHEDULER_MAX_DELIVERY_COUNT": text(config["user_scheduling_max_delivery_count"]),
    })
    for name, value in config.items():
        if name.startswith(("workiq_", "scheduled_learning_")):
            key = name.upper().replace("_MCP_URL", "_MCP_UPSTREAM_URL")
            environment[key] = text(value)
    transport = infrastructure.get("transport")
    if not isinstance(transport, dict):
        raise ValueError("Apply the Worker's native transport infrastructure before deploying applications.")
    environment.update({
        "TRANSPORT_ROLE": "gateway",
        "TRANSPORT_CONNECTION_ID": transport["connection_ids"]["gateway"],
        "TRANSPORT_JOURNAL_PATH": "/data/autopilots/transport.db",
        "TRANSPORT_JOURNAL_BACKUP": "/backup/transport/journal.db",
        "SCHEDULER_SERVICEBUS_NAMESPACE": transport["namespace"],
    })
    return environment


RUNTIME_ONLY_SETTINGS = frozenset({
    "AZURE_SANDBOX_GROUP",
    "AZURE_REGION",
    "SANDBOX_VNET_CONNECTION_NAME",
    "AGENT_RUNTIME_MANAGED_IDENTITY_CLIENT_ID",
    "AGENT_RUNTIME_IMAGE",
    "AGENT_RUNTIME_DISK_IMAGE_ID",
    "AGENT_RUNTIME_DISK_IMAGE_NAME",
    "PREVIOUS_API_SERVER_KEY",
    "FOUNDRY_OPENAI_BASE_URL",
    "PRIVATE_INCIDENTS_MCP_URL",
    "PRIVATE_INCIDENTS_MCP_SCOPE",
    "PUBLIC_SHIPMENTS_MCP_UPSTREAM_URL",
    "PUBLIC_SHIPMENTS_MCP_SCOPE",
    "WORKER_ASSIGNMENT_SCOPE",
})


def gateway_environment(settings: dict[str, str]) -> dict[str, str]:
    """Messaging settings only: the gateway reaches runtime compute through AGENT_RUNTIME_URL."""
    return {
        key: value
        for key, value in settings.items()
        if key not in RUNTIME_ONLY_SETTINGS and not key.startswith("WORKIQ_")
    }


def deploy_runtime(settings: dict[str, str], gateway_url: str, credential: Any) -> dict[str, Any]:
    """Create or reuse runtime compute at deployment and return its native OnDemand endpoint."""
    config = config_from_environment({**settings, "AUTOPILOT_BRIDGE_URL": gateway_url})
    config = replace(config, command=("python3", "-c", BOOTSTRAP), args=())
    runtime = deploy_agent_sandbox(config, credential=credential)
    release = build_release("runtime", base_image=config.runtime_image_reference)
    with SandboxGroupClient(
        endpoint_for_region(config.region), credential,
        subscription_id=config.subscription_id, resource_group=config.resource_group,
        sandbox_group=config.sandbox_group, audience=DISK_IMAGE_AUDIENCE, read_timeout=1800,
    ) as group:
        sandbox = group.get_sandbox_client(runtime.sandbox_id)
        sandbox.ensure_running(timeout=600)
        directory = install_release(sandbox, release)
        environment = dict(config.environment)
        environment.update({
            "APPLICATION_RELEASE": release.digest,
            "OTEL_SERVICE_VERSION": release.digest,
            "AUTOPILOTS_APPLICATION_ROOT": directory,
            "PYTHONPATH": directory,
            "AUTOPILOTS_PLUGIN_PATH": directory + ":/opt/autopilots/hermes-site",
            "HERMES_BUNDLED_SKILLS": directory + "/runtime_skills",
        })
        deployed = {
            "environment": environment,
            "command": ["/app/.venv/bin/python", directory + "/start_hermes.py"],
            "workingDirectory": directory, "release": release.digest,
        }
        if runtime.reused_existing_sandbox:
            previous = json.loads(sandbox.read_file(SERVICE_CONFIG_PATH))
            if previous != deployed:
                with service_maintenance(sandbox):
                    require_quiescent_service(sandbox, previous, config.port)
                    try:
                        write_service_settings(sandbox, deployed)
                        require_service_health(sandbox, config.port, release.digest)
                    except (AzureError, OSError, ValueError, RuntimeError) as error:
                        try:
                            write_service_settings(sandbox, previous)
                            require_service_health(sandbox, config.port, previous["release"])
                        except (AzureError, OSError, ValueError, RuntimeError) as rollback:
                            error.add_note(f"Restoring previous runtime application failed: {rollback}")
                        raise
        else:
            write_service_settings(sandbox, deployed)
        require_service_health(sandbox, config.port, release.digest)
    seconds = require_runtime_ready(runtime.endpoint_url)
    print(
        f"Runtime {runtime.sandbox_id} healthy through OnDemand ingress after {seconds:.1f}s "
        f"({'reused' if runtime.reused_existing_sandbox else 'created'}).",
        flush=True,
    )
    return {
        "runtime_url": runtime.endpoint_url, "runtime_sandbox_id": runtime.sandbox_id,
        "runtime_application_release": release.digest,
    }


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
        image=PYTHON_BASE_IMAGE,
        disk_source_image=PYTHON_BASE_IMAGE,
        port=8765,
        command=("/app/.venv/bin/python", "-m", "private_incidents_mcp.server" if private else "public_shipments_mcp.server"),
        environment=environment,
        release=build_release(role),
    )


def service_body(service: SandboxService, group: dict, disk_id: str, worker: str) -> dict:
    body = {
        "sourcesRef": {"diskImage": {"id": disk_id}},
        "resources": {"cpu": "500m", "memory": "1024Mi", "disk": "10Gi"},
        "lifecycle": {"autoSuspendPolicy": {
            "enabled": service.auto_suspend, "interval": 300,
            "mode": "Memory",
        }},
        "labels": {"app": "autopilots-on-azure", "worker": worker, "service": service.role,
                   "image": hashlib.sha256(service.image.encode()).hexdigest()[:32]},
        "environment": {"SSL_CERT_FILE": "/etc/ssl/certs/ca-certificates.crt",
                        "REQUESTS_CA_BUNDLE": "/etc/ssl/certs/ca-certificates.crt",
                        "AZURE_CLIENT_ID": group["identity_client_id"]},
        "ports": [{"port": service.port, "protocol": "Http", "activationMode": "OnDemand",
                   "auth": {"anonymous": True}}],
        "entrypoint": ["python3", "-c", BOOTSTRAP],
        "cmd": [],
        "skipEgressProxy": False,
    }
    if service.role == "gateway":
        body["volumes"] = [SandboxVolume("gateway-state", "/backup")._to_dict()]
    if group["vnet_connection_name"]:
        body["customerVnetConnectionName"] = group["vnet_connection_name"]
    body["labels"]["deployment"] = hashlib.sha256(
        json.dumps(body, sort_keys=True).encode()
    ).hexdigest()[:32]
    return body


def require_exec_success(result: Any, operation: str) -> None:
    if result.exit_code != 0:
        raise RuntimeError(f"{operation} failed: {result.stderr}")


def require_service_health(client: Any, port: int, release: str = "") -> None:
    probe = (
        "import urllib.request; "
        f"r=urllib.request.urlopen('http://127.0.0.1:{port}/health', timeout=5); "
        "assert r.status == 200; "
        "import json; data=json.load(r); "
        f"assert not {release!r} or data.get('release') == {release!r}, data"
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
    sandbox.write_file(SERVICE_CONFIG_PATH + ".next", json.dumps(settings, sort_keys=True), mode="0600")
    require_exec_success(
        sandbox.exec(f"mv {SERVICE_CONFIG_PATH}.next {SERVICE_CONFIG_PATH}"), "Activating service configuration"
    )


def require_quiescent_service(sandbox: Any, previous: dict, port: int) -> None:
    if not isinstance(previous, dict):
        raise RuntimeError("The previous application configuration is missing; inspect the owned Sandbox.")
    environment = previous.get("environment", {})
    gateway = environment.get("TRANSPORT_ROLE") == "gateway"
    if gateway and not environment.get("TRANSPORT_CONNECTION_ID"):
        raise RuntimeError("The previous gateway has no durable scheduling configuration.")
    probe = """
import json,sqlite3,urllib.request
config=json.load(open('/app/.sandbox-service.json'))
environment=config['environment']
gateway=environment.get('TRANSPORT_ROLE')=='gateway'
path='/internal/transport/status' if gateway else '/internal/runtime/status'
request=urllib.request.Request(
 'http://127.0.0.1:__PORT__'+path,
 headers={'X-Autopilot-Key':config['environment']['API_SERVER_KEY']})
with urllib.request.urlopen(request,timeout=30) as response:
 result=json.load(response)
if result['active']:
 raise RuntimeError('Application update refused while durable work is unfinished')
if gateway and (result['unfinished'] or result['unsettled']):
 connection=sqlite3.connect(environment['TRANSPORT_JOURNAL_PATH'])
 connection.row_factory=sqlite3.Row
 rows=list(connection.execute("SELECT queue,phase,native_scope,native_run_id FROM deliveries WHERE settled=0 OR phase IN ('pending','running')"))
 connection.close()
 if any(row['phase'] not in ('completed','failed','unknown') for row in rows):
  raise RuntimeError('Gateway effects cannot be replayed automatically during an update')
 result['terminalReceiptsAwaitingSettlement']=len(rows)
print(json.dumps(result))
""".replace("__PORT__", str(port)).strip()
    result = sandbox.exec("python3 -c " + shlex.quote(probe))
    require_exec_success(result, "Checking application quiescence")


@contextmanager
def service_maintenance(sandbox: Any):
    owner = uuid.uuid4().hex
    acquire = (
        "import pathlib; "
        "path=pathlib.Path('/app/.application-maintenance'); "
        f"handle=path.open('x'); handle.write({owner!r}); handle.close()"
    )
    require_exec_success(sandbox.exec("python3 -c " + shlex.quote(acquire)), "Entering application maintenance")
    try:
        yield
    finally:
        release = (
            "import pathlib; path=pathlib.Path('/app/.application-maintenance'); "
            f"assert path.read_text()=={owner!r}, 'Maintenance belongs to another operation'; path.unlink()"
        )
        require_exec_success(
            sandbox.exec("python3 -c " + shlex.quote(release)), "Leaving application maintenance",
        )


def deploy_service(
    client: Any,
    service: SandboxService,
    group: dict,
    worker: str,
    *,
    disk_image_id: str,
    endpoint_environment: Any = None,
) -> dict:
    """Deploy one service; endpoint_environment derives settings that need this service's native URL."""
    if not disk_image_id:
        raise ValueError("A deployment-prepared disk image ID is required.")
    if service.release is None:
        raise ValueError(f"{service.role} requires a verified application release.")
    if service.role == "gateway":
        volumes = {volume.name: volume for volume in client.list_volumes()}
        if "gateway-state" not in volumes:
            client.create_volume(
                "gateway-state", type="AzureBlob",
                labels={"app": "autopilots-on-azure", "worker": worker},
            )
        elif volumes["gateway-state"].type != "AzureBlob":
            raise ValueError("The gateway-state volume is not the expected managed Blob volume.")
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
    superseded_ids = []
    sandbox = None
    created = not matching
    previous = None
    config_changed = False
    maintenance = ExitStack()
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
            superseded_ids.append(item["id"])
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
            environment["TRANSPORT_SELF_URL"] = endpoint.rstrip("/")
            if not created:
                try:
                    previous = json.loads(sandbox.read_file(SERVICE_CONFIG_PATH))
                except ResourceNotFoundError:
                    pass
                maintenance.enter_context(service_maintenance(sandbox))
                require_quiescent_service(sandbox, previous, service.port)
        if endpoint_environment is not None:
            environment.update(endpoint_environment(endpoint.rstrip("/")))
        directory = install_release(sandbox, service.release)
        environment["APPLICATION_RELEASE"] = service.release.digest
        environment["OTEL_SERVICE_VERSION"] = service.release.digest
        command = list(service.command)
        command[0] = directory + "/.venv/bin/python"
        settings = {
            "environment": environment, "command": command,
            "workingDirectory": directory, "release": service.release.digest,
        }
        if not created:
            try:
                previous = json.loads(sandbox.read_file(SERVICE_CONFIG_PATH))
            except ResourceNotFoundError:
                pass
        if previous != settings:
            write_service_settings(sandbox, settings)
            config_changed = True
        require_service_health(sandbox, service.port, service.release.digest)
    except (AzureError, OSError, ValueError, RuntimeError) as exc:
        try:
            if created and sandbox is not None:
                sandbox.begin_delete(polling_timeout=600).result()
            elif config_changed and previous is not None:
                write_service_settings(sandbox, previous)
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
    finally:
        maintenance.close()
    return {"sandbox_id": sandbox.sandbox_id, "url": endpoint.rstrip("/"),
            "image": service.image, "disk_image_id": disk_image_id,
            "application_release": service.release.digest,
            "superseded_sandbox_ids": superseded_ids}


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
    for name in ("runtime_image", "runtime_disk_source_image"):
        if not config.get(name):
            raise ValueError(f"{name} is required before Sandbox deployment.")
    endpoints: dict[str, Any] = {}
    services = {}
    with AzureCliCredential(
        subscription=infrastructure["subscription_id"], process_timeout=150,
    ) as credential:
        disks = {}
        for role, prefix in (("runtime", "runtime"), ("private-mcp", "private_mcp"),
                             ("public-mcp", "public_shipments_mcp"), ("gateway", "bridge")):
            with SandboxGroupClient(
                endpoint_for_region(infrastructure["sandbox_location"]), credential,
                subscription_id=infrastructure["subscription_id"],
                resource_group=infrastructure["resource_group_name"], sandbox_group=groups[role]["name"],
                audience=DISK_IMAGE_AUDIENCE,
            ) as client:
                disks[role] = prepare_disk_image(
                    client, role=role,
                    image=config["runtime_image"] if role == "runtime" else PYTHON_BASE_IMAGE,
                    source=config["runtime_disk_source_image"] if role == "runtime" else PYTHON_BASE_IMAGE,
                    acr_name=platform["acr_name"], subscription_id=infrastructure["subscription_id"],
                    identity_client_id=groups[role]["identity_client_id"],
                )
        endpoints["runtime_disk_image_id"] = disks["runtime"]
        runtime: dict[str, Any] = {}
        for role in ("private-mcp", "public-mcp", "gateway"):
            group = infrastructure["sandbox_groups"][role]
            endpoint_environment = None
            if role == "gateway":
                settings = worker_settings(config, platform, infrastructure, endpoints)
                service = SandboxService(
                    role="gateway", image=PYTHON_BASE_IMAGE, port=8000,
                    disk_source_image=PYTHON_BASE_IMAGE,
                    command=("/app/.venv/bin/python", "-m", "uvicorn", "bridge.app:app", "--host", "0.0.0.0", "--port", "8000"),
                    environment=gateway_environment(settings),
                    auto_suspend=True,
                    release=build_release("gateway"),
                )

                def endpoint_environment(gateway_url: str) -> dict[str, str]:
                    runtime.update(deploy_runtime(settings, gateway_url, credential))
                    return {"AGENT_RUNTIME_URL": runtime["runtime_url"]}
            else:
                service = mcp_service(role, config, infrastructure)
            with SandboxGroupClient(
                endpoint_for_region(infrastructure["sandbox_location"]), credential,
                subscription_id=infrastructure["subscription_id"],
                resource_group=infrastructure["resource_group_name"], sandbox_group=group["name"],
                audience=DISK_IMAGE_AUDIENCE, read_timeout=1800,
            ) as client:
                result = deploy_service(
                    client, service, group, infrastructure["worker_id"], disk_image_id=disks[role],
                    endpoint_environment=endpoint_environment,
                )
                services[role] = result
                prefix = {"private-mcp": "private_mcp", "public-mcp": "public_shipments_mcp", "gateway": "bridge"}[role]
                endpoints[prefix + "_url"] = result["url"] + ("/mcp" if role != "gateway" else "")
                endpoints[prefix + "_fqdn"] = urlsplit(result["url"]).hostname
                endpoints[prefix + "_sandbox_id"] = result["sandbox_id"]
    return {**endpoints, **runtime, "sandbox_services": services}
