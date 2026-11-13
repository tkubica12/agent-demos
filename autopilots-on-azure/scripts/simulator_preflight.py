from __future__ import annotations

from collections import defaultdict
import ipaddress
import json
import math
from pathlib import Path
import re
import uuid

from scripts.tf_helpers import run


PROVIDERS = (
    "Microsoft.App", "Microsoft.CognitiveServices", "Microsoft.ContainerRegistry",
    "Microsoft.ManagedIdentity", "Microsoft.Network", "Microsoft.ServiceBus",
    "Microsoft.Insights", "Microsoft.OperationalInsights",
)
DNS_ZONES = (
    "privatelink.azurecr.io", "privatelink.cognitiveservices.azure.com",
    "privatelink.openai.azure.com", "privatelink.services.ai.azure.com",
    "privatelink.servicebus.windows.net",
)
IDENTITY_LIMITATION = (
    "UNVERIFIED: owner identity, Entra application/consent permissions and Azure write/role-assignment "
    "permissions are not proven by this read-only plan; no Entra calls were made."
)
NETWORK_LIMITATION = (
    "UNVERIFIED: DNS links/resolution, routes, NSGs, available subnet addresses and operator/workload "
    "connectivity are not proven; this plan creates or changes none of them."
)
RESOURCE_QUERY = "{id:id,name:name,type:type,location:location}"


class _ReadFailure(RuntimeError):
    def __init__(self, *, denied: bool = False):
        self.denied = denied
        super().__init__("access denied" if denied else "Azure CLI read failed")


class _Reads:
    def __init__(self):
        self.cache: dict[tuple[str, ...], object] = {}

    def read(self, arguments: list[str]):
        key = tuple(part.casefold() for part in arguments)
        if key not in self.cache:
            try:
                result = run(["az", *arguments, "--only-show-errors", "-o", "json"], capture=True, check=False)
            except OSError:
                raise RuntimeError("Azure CLI could not run; preflight requires an already authenticated Azure CLI.") from None
            if result.returncode:
                error = result.stderr or ""
                denied = bool(re.search(
                    r"\b(403|Forbidden|AuthorizationFailed|AuthorizationPermissionMismatch|AccessDenied)\b",
                    error, re.IGNORECASE,
                ))
                self.cache[key] = _ReadFailure(denied=denied)
            else:
                try:
                    self.cache[key] = json.loads(result.stdout)
                except (TypeError, ValueError):
                    raise RuntimeError("Azure CLI returned invalid JSON during a read-only preflight query.") from None
        result = self.cache[key]
        if isinstance(result, _ReadFailure):
            raise result
        return result

    def get(self, resource_id: str, api_version: str, query: str = RESOURCE_QUERY):
        return self.read([
            "rest", "--method", "get", "--url",
            f"https://management.azure.com{resource_id}?api-version={api_version}", "--query", query,
        ])


def _arm_id(value: object, resource_type: str, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a full Azure resource ID.")
    parts = resource_type.split("/")
    resource_path = "/".join(re.escape(part) + r"/[^/?#\s]+" for part in parts[1:])
    pattern = (
        r"/subscriptions/[0-9a-f-]{36}/resourceGroups/[^/?#\s]+/providers/"
        + re.escape(parts[0]) + "/" + resource_path
    )
    if not re.fullmatch(pattern, value, re.IGNORECASE):
        raise ValueError(f"{label} must identify {resource_type}.")
    try:
        uuid.UUID(value.split("/")[2])
    except ValueError:
        raise ValueError(f"{label} has an invalid subscription ID.") from None
    return value


def _resource(reads: _Reads, resource_id: str, resource_type: str, api_version: str,
              label: str, query: str = RESOURCE_QUERY) -> dict:
    _arm_id(resource_id, resource_type, label)
    try:
        resource = reads.get(resource_id, api_version, query)
    except _ReadFailure as error:
        raise RuntimeError(f"{label} is missing or unreadable ({error}).") from None
    if not isinstance(resource, dict):
        raise RuntimeError(f"{label} returned an invalid resource response.")
    if (str(resource.get("id", "")).casefold() != resource_id.casefold()
            or str(resource.get("type", "")).casefold() != resource_type.casefold()):
        raise ValueError(f"{label} returned a different resource ID or type; expected {resource_type}.")
    return resource


def _token(value: object, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", value):
        raise ValueError(f"{label} must be a nonempty resource/model name.")
    return value


def _build_scope(config: dict, *, private: bool) -> dict:
    settings = config.get("build", {})
    if not isinstance(settings, dict):
        raise ValueError("build must be a mapping.")
    mode = settings.get("mode", "source")
    if mode not in ("import", "source"):
        raise ValueError("build.mode must be import or source; quick-build/public staging fallbacks are not permitted.")
    platform = config.get("platform", {})
    allow_import = settings.get("allow_trusted_service_import", False)
    trusted_bypass = platform.get("acr_trusted_services_bypass_enabled", False)
    if not isinstance(allow_import, bool) or not isinstance(trusted_bypass, bool):
        raise ValueError("allow_trusted_service_import and acr_trusted_services_bypass_enabled must be booleans.")
    if mode == "import" and (not allow_import or not trusted_bypass):
        raise ValueError(
            "Import mode is opt-in only: build.allow_trusted_service_import=true and "
            "platform.acr_trusted_services_bypass_enabled=true are both required."
        )
    pool_id = platform.get("acr_agent_pool_subnet_id") or None
    pool_name = platform.get("acr_agent_pool_name", "private-build")
    instance_count = platform.get("acr_agent_pool_instance_count", 1)
    if not isinstance(pool_name, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]{0,48}[a-zA-Z0-9]", pool_name):
        raise ValueError("acr_agent_pool_name must contain 2-50 alphanumeric/hyphen characters and start/end alphanumeric.")
    if isinstance(instance_count, bool) or not isinstance(instance_count, int) or instance_count < 1:
        raise ValueError("acr_agent_pool_instance_count must be a positive integer.")
    if private and mode == "source" and not pool_id:
        raise ValueError(
            "Private source build requires platform.acr_agent_pool_subnet_id; "
            "quick-build/public staging fallbacks are not permitted."
        )
    if pool_id:
        _arm_id(pool_id, "Microsoft.Network/virtualNetworks/subnets", "ACR build-agent subnet")
    result = {
        "mode": mode,
        "allow_trusted_service_import": allow_import,
        "acr_trusted_services_bypass_enabled": trusted_bypass,
        "agent_pool": {"subnet_id": pool_id, "name": pool_name, "instance_count": instance_count} if pool_id else None,
    }
    if mode == "import":
        source_id = _arm_id(settings.get("source_registry_id"), "Microsoft.ContainerRegistry/registries",
                            "build.source_registry_id")
        server = settings.get("source_registry_server")
        hostname = r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
        if not isinstance(server, str) or len(server) > 253 or not re.fullmatch(hostname + r"(?:\." + hostname + r")+", server):
            raise ValueError("build.source_registry_server must be a bare registry DNS hostname.")
        result.update(source_registry_id=source_id, source_registry_server=server)
        component = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
        image_pattern = component + r"(?:/" + component + r")*@sha256:[0-9a-fA-F]{64}"
        for field in ("runtime_image", "simulator_image", "gateway_image"):
            image = settings.get(field)
            if (not isinstance(image, str) or "/" not in image
                    or image.split("/", 1)[0].casefold() != server.casefold()
                    or not re.fullmatch(image_pattern, image.split("/", 1)[1])):
                raise ValueError(f"build.{field} must be a full same-server repository@sha256:<64 hex digits> reference.")
            result[field] = image
        result["artifact_verification"] = "immutable references only; manifests, network access and image pulls are unverified"
    return result


def _scope(config: dict) -> dict:
    platform = config.get("platform", {})
    if not isinstance(platform, dict):
        raise ValueError("platform must be a mapping.")
    scope = {}
    for field in ("tenant_id", "subscription_id", "owner_id"):
        try:
            scope[field] = str(uuid.UUID(config[field]))
        except (KeyError, ValueError, TypeError, AttributeError):
            raise ValueError(f"{field} must be a UUID.") from None
    network = config.get("network_mode", "private")
    group_mode = config.get("resource_group_mode", "existing")
    auth = config.get("auth_mode", "entra")
    if network not in {"private", "public"}:
        raise ValueError("network_mode must be private or public; there is no automatic fallback.")
    if group_mode not in {"existing", "create"}:
        raise ValueError("resource_group_mode must be existing or create.")
    if auth not in {"entra", "demo"}:
        raise ValueError("auth_mode must be entra or demo.")
    private = platform.get("private_network_enabled", network == "private")
    if not isinstance(private, bool) or private != (network == "private"):
        raise ValueError("network_mode and platform.private_network_enabled disagree.")
    capacity = config.get("model_capacity", 15)
    if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
        raise ValueError("model_capacity must be a positive integer.")
    group = platform.get("existing_resource_group_name") or None
    if group is not None and (not isinstance(group, str) or not re.fullmatch(r"[\w.()-]{1,90}", group)):
        raise ValueError("existing_resource_group_name must be a valid resource group name.")
    if group_mode == "existing" and not group:
        raise ValueError("Existing resource group mode requires platform.existing_resource_group_name.")
    dns = platform.get("private_dns_zone_ids", {})
    tags = platform.get("required_tags", {})
    if not isinstance(dns, dict) or not all(isinstance(key, str) for key in dns):
        raise ValueError("private_dns_zone_ids must map full zone names to resource IDs.")
    if not isinstance(tags, dict) or not all(isinstance(key, str) and isinstance(value, str)
                                             for key, value in tags.items()):
        raise ValueError("required_tags must map tag names to string values.")
    scheduler_enabled = platform.get("scheduler_enabled", True)
    if not isinstance(scheduler_enabled, bool):
        raise ValueError("platform.scheduler_enabled must be a boolean.")
    scope.update({
        "environment": _token(config.get("environment"), "environment"),
        "stack_name": _token(config.get("stack_name", config.get("environment")), "stack_name"),
        "network_mode": network, "profile": "private" if private else "PUBLIC",
        "auth_mode": auth,
        "scheduler_enabled": scheduler_enabled,
        "regions": {
            "infrastructure": _token(platform.get("sandbox_location", "swedencentral"), "sandbox_location").lower(),
            "ai": _token(platform.get("location", "northeurope"), "location").lower(),
        },
        "resource_group": {"mode": group_mode, "name": group},
        "subnets": {
            "sandbox": platform.get("existing_sandbox_subnet_id") or None,
            "private_endpoint": platform.get("existing_private_endpoint_subnet_id") or None,
        },
        "private_dns_zone_ids": dict(dns),
        "monitoring": {key: platform.get(key) or None for key in (
            "existing_log_analytics_workspace_id", "existing_application_insights_id", "ampls_id",
        )},
        "model": {
            "name": _token(platform.get("model_name", "gpt-6.1-sol"), "model_name"),
            "version": _token(platform.get("model_version", "2026-09-29"), "model_version"),
            "deployment_name": _token(platform.get("model_deployment_name", "gpt-6-1-sol"), "model_deployment_name"),
            "sku": _token(platform.get("model_sku_name", "GlobalStandard"), "model_sku_name"),
            "capacity": capacity,
        },
        "required_tag_names": sorted(tags),
        "build": _build_scope(config, private=private),
        "providers": {}, "warnings": [IDENTITY_LIMITATION],
    })
    return scope


def _account_and_providers(reads: _Reads, scope: dict, *, scheduler_required: bool) -> None:
    subscription = scope["subscription_id"]
    try:
        account = reads.read([
            "account", "show", "--subscription", subscription,
            "--query", "{id:id,tenantId:tenantId,state:state}",
        ])
    except _ReadFailure as error:
        raise RuntimeError(f"Configured Azure account is unreadable ({error}); authenticate separately.") from None
    if (not isinstance(account, dict)
            or str(account.get("id", "")).casefold() != subscription
            or str(account.get("tenantId", "")).casefold() != scope["tenant_id"]):
        raise ValueError("Azure CLI tenant/subscription mismatch with the configured scope.")
    if account.get("state") != "Enabled":
        raise ValueError("Configured Azure subscription is not Enabled.")
    for provider in PROVIDERS:
        if provider == "Microsoft.ServiceBus" and not scheduler_required:
            scope["providers"][provider] = "not_required"
            continue
        try:
            response = reads.read([
                "provider", "show", "--namespace", provider, "--subscription", subscription,
                "--query", "{registrationState:registrationState}",
            ])
        except _ReadFailure as error:
            if not error.denied:
                raise RuntimeError(f"Required provider {provider} could not be read.") from None
            scope["providers"][provider] = "unverified"
            scope["warnings"].append(f"UNVERIFIED: access denied reading provider {provider}; registration is not proven.")
            continue
        state = response.get("registrationState") if isinstance(response, dict) else None
        if state != "Registered":
            raise ValueError(f"Required provider {provider} is not Registered (or returned no readable state).")
        scope["providers"][provider] = "Registered"


def _group(reads: _Reads, scope: dict) -> None:
    group = scope["resource_group"]
    if group["mode"] == "create":
        scope["warnings"].append("UNVERIFIED: create mode skips existence checks for the resource group to be created.")
        return
    resource_id = f"/subscriptions/{scope['subscription_id']}/resourceGroups/{group['name']}"
    try:
        response = reads.get(resource_id, "2021-04-01")
    except _ReadFailure as error:
        raise RuntimeError(f"Existing resource group is missing or unreadable ({error}).") from None
    if (not isinstance(response, dict)
            or str(response.get("id", "")).casefold() != resource_id.casefold()
            or str(response.get("type", "")).casefold() != "microsoft.resources/resourcegroups"):
        raise ValueError("Existing resource group returned an incorrect ID or resource type.")


def _subnet(reads: _Reads, resource_id: str, *, sandbox: bool, scope: dict, label: str | None = None) -> None:
    label = label or ("Sandbox subnet" if sandbox else "Private endpoint subnet")
    subnet = _resource(
        reads, resource_id, "Microsoft.Network/virtualNetworks/subnets", "2024-05-01", label,
        "{id:id,type:type,properties:{addressPrefix:properties.addressPrefix,"
        "addressPrefixes:properties.addressPrefixes,delegations:properties.delegations}}",
    )
    properties = subnet.get("properties") or {}
    delegations = properties.get("delegations") or []
    services = [item.get("properties", {}).get("serviceName", "").casefold() for item in delegations]
    if sandbox and services != ["microsoft.app/environments"]:
        raise ValueError("Sandbox subnet must be delegated only to Microsoft.App/environments.")
    if not sandbox and delegations:
        raise ValueError(f"{label} must have no delegation.")
    prefixes = properties.get("addressPrefixes") or [properties.get("addressPrefix")]
    try:
        networks = [ipaddress.ip_network(prefix) for prefix in prefixes if prefix]
    except (ValueError, TypeError):
        raise ValueError(f"{label} returned an invalid address prefix.") from None
    ipv4 = [network for network in networks if network.version == 4]
    if not ipv4 or (sandbox and not any(network.prefixlen <= 27 for network in ipv4)):
        raise ValueError(f"{label} requires an IPv4 subnet" + (" of /27 or larger." if sandbox else "."))
    if sandbox and not any(network.prefixlen <= 26 for network in ipv4):
        scope["warnings"].append("Sandbox subnet is /27; /26 or larger is recommended.")
    vnet_id = resource_id.rsplit("/", 2)[0]
    vnet = _resource(reads, vnet_id, "Microsoft.Network/virtualNetworks", "2024-05-01", label + " VNet")
    if str(vnet.get("location", "")).casefold() != scope["regions"]["infrastructure"]:
        raise ValueError(f"{label} VNet location does not match sandbox_location (infrastructure region).")


def _network(reads: _Reads, scope: dict) -> None:
    if scope["network_mode"] == "public":
        scope["warnings"].append("PUBLIC PROFILE: private network prerequisites are skipped; no private fallback is implied.")
        return
    subnets = scope["subnets"]
    if not all(subnets.values()):
        raise ValueError("Private mode requires existing sandbox and private endpoint subnet IDs.")
    if str(subnets["sandbox"]).casefold() == str(subnets["private_endpoint"]).casefold():
        raise ValueError("Sandbox and private endpoint subnet IDs must be different.")
    _subnet(reads, subnets["sandbox"], sandbox=True, scope=scope)
    _subnet(reads, subnets["private_endpoint"], sandbox=False, scope=scope)
    zones = scope["private_dns_zone_ids"]
    regional = "privatelink." + scope["regions"]["infrastructure"] + ".azurecontainerapps.io"
    required = [*DNS_ZONES]
    if not scope["scheduler_enabled"]:
        required.remove("privatelink.servicebus.windows.net")
    if regional not in zones and "privatelink.azurecontainerapps.io" not in zones:
        required.append(regional)
    missing = sorted(set(required) - zones.keys())
    if missing:
        raise ValueError("Private mode is missing required private DNS zones: " + ", ".join(missing))
    for name, resource_id in zones.items():
        zone = _resource(reads, resource_id, "Microsoft.Network/privateDnsZones", "2024-06-01", "Private DNS zone")
        if name.casefold() != str(zone.get("name", "")).casefold() or name.casefold() != resource_id.rsplit("/", 1)[-1].casefold():
            raise ValueError("Private DNS zone name does not match its configured map key.")
    scope["warnings"].append(NETWORK_LIMITATION)


def _monitoring(reads: _Reads, scope: dict) -> None:
    monitoring = scope["monitoring"]
    workspace = monitoring["existing_log_analytics_workspace_id"]
    insights = monitoring["existing_application_insights_id"]
    if bool(workspace) != bool(insights):
        raise ValueError("Existing monitoring requires both Log Analytics and Application Insights resource IDs.")
    if workspace:
        _resource(reads, workspace, "Microsoft.OperationalInsights/workspaces", "2023-09-01", "Log Analytics workspace")
        application = _resource(
            reads, insights, "Microsoft.Insights/components", "2020-02-02", "Application Insights",
            "{id:id,type:type,properties:{WorkspaceResourceId:properties.WorkspaceResourceId}}",
        )
        linked = (application.get("properties") or {}).get("WorkspaceResourceId")
        if not isinstance(linked, str) or linked.casefold() != workspace.casefold():
            raise ValueError("Existing Application Insights must be linked to the supplied Log Analytics workspace.")
    if monitoring["ampls_id"]:
        _resource(reads, monitoring["ampls_id"], "Microsoft.Insights/privateLinkScopes", "2021-07-01-preview", "AMPLS")
    scope["warnings"].append(
        "UNVERIFIED: monitoring ingestion/query connectivity, private-link associations and local-auth/public-access "
        "policy settings are not proven by resource readability/type checks."
    )


def _build(reads: _Reads, scope: dict) -> None:
    build = scope["build"]
    pool = build["agent_pool"]
    if pool:
        pool_id = pool["subnet_id"]
        if any(pool_id.casefold() == str(subnet_id).casefold() for subnet_id in scope["subnets"].values()):
            raise ValueError("ACR build-agent subnet must be dedicated and different from sandbox/private endpoint subnets.")
        _subnet(reads, pool_id, sandbox=False, scope=scope, label="ACR build-agent subnet")
    if build["mode"] == "import":
        registry = _resource(
            reads, build["source_registry_id"], "Microsoft.ContainerRegistry/registries", "2023-07-01",
            "Source registry", "{id:id,type:type,properties:{loginServer:properties.loginServer,"
            "publicNetworkAccess:properties.publicNetworkAccess}}",
        )
        properties = registry.get("properties") or {}
        if str(properties.get("loginServer", "")).casefold() != build["source_registry_server"].casefold():
            raise ValueError("Source registry loginServer does not match build.source_registry_server.")
        if scope["network_mode"] == "private" and properties.get("publicNetworkAccess") != "Disabled":
            raise ValueError("Private import requires source registry publicNetworkAccess to be Disabled.")
        scope["warnings"].append(
            "UNVERIFIED import artifacts: immutable digest references and source registry ARM metadata were checked; "
            "image existence/manifests, import/pull authorization and network/pull access are not verified. "
            "Both trusted-service import exceptions were explicitly requested, not proven deployed. "
            "No registry data-plane queries were made."
        )
    else:
        scope["warnings"].append(
            "UNVERIFIED source build: agent-pool provisioning/capacity, private registry connectivity/DNS, "
            "source/base-image availability and required build-service outbound dependencies are not proven. "
            "OCI bases are pulled directly through approved build-network egress (inside the dedicated pool "
            "in private mode); registry/package-feed access and that egress are not verified. "
            "No validation ACR/customer artifact registry or default ARM import is assumed; "
            "no build or quick-build/public staging fallback was attempted."
        )


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _model_availability(reads: _Reads, scope: dict) -> None:
    model = scope["model"]
    region = scope["regions"]["ai"]
    evidence = {
        "status": "unverified", "region": region, "model_name": model["name"],
        "version": model["version"], "sku": model["sku"],
    }
    scope["model_availability"] = evidence
    warning = (
        "UNVERIFIED regional model availability: regional model catalog metadata is unreadable or incomplete; "
        "quota does not establish regional model/version/SKU support. No model, version, SKU or region fallback was selected."
    )
    try:
        catalog = reads.read([
            "cognitiveservices", "model", "list", "--location", region,
            "--subscription", scope["subscription_id"], "--query", "[].{model:model}",
        ])
    except RuntimeError:
        scope["warnings"].append(warning)
        return
    if not isinstance(catalog, list):
        scope["warnings"].append(warning)
        return
    models = []
    incomplete = False
    for item in catalog:
        metadata = item.get("model") if isinstance(item, dict) else None
        if not isinstance(metadata, dict) or not isinstance(metadata.get("format"), str):
            incomplete = True
            continue
        if metadata["format"].casefold() != "openai":
            continue
        if not all(isinstance(metadata.get(field), str) and metadata[field] for field in ("name", "version")):
            incomplete = True
            continue
        if (metadata["name"].casefold() == model["name"].casefold()
                and metadata["version"] == model["version"]):
            models.append(metadata)
    for metadata in models:
        skus = metadata.get("skus")
        if not isinstance(skus, list):
            incomplete = True
            continue
        for sku in skus:
            if not isinstance(sku, dict) or not isinstance(sku.get("name"), str):
                incomplete = True
            elif sku["name"].casefold() == model["sku"].casefold():
                evidence["status"] = "verified"
                scope["warnings"].append(
                    "UNVERIFIED deployment admission: regional model/version/SKU metadata confirms catalog support, "
                    "not live deployment capacity, policy approval or guaranteed provisioning."
                )
                return
    if incomplete:
        scope["warnings"].append(warning)
        return
    raise ValueError(
        f"Unsupported regional model/version/SKU: OpenAI {model['name']} version {model['version']} "
        f"with {model['sku']} is not listed in {region}; quota does not establish model availability."
    )


def _state_json(path: Path, label: str) -> dict | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError):
        raise RuntimeError(f"{label} is unreadable; exact state ownership cannot be verified.") from None
    try:
        value = json.loads(raw)
    except ValueError:
        raise ValueError(f"{label} contains malformed JSON.") from None
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object.")
    return value


def _managed_attributes(state: dict, resource_type: str, name: str) -> dict | None:
    matches = [resource for resource in state["resources"]
               if resource.get("mode") == "managed" and not resource.get("module")
               and resource.get("type") == resource_type and resource.get("name") == name]
    if len(matches) > 1:
        raise ValueError("Exact platform state contains duplicate managed resource addresses.")
    if not matches:
        return None
    instances = matches[0].get("instances")
    if not isinstance(instances, list) or not all(isinstance(instance, dict) for instance in instances):
        raise ValueError("Exact platform state contains malformed managed resource instances.")
    current = [instance for instance in instances if not instance.get("deposed")]
    if len(current) > 1:
        raise ValueError("Exact platform state contains ambiguous managed resource instances.")
    if not current or current[0].get("status") == "tainted":
        return None
    attributes = current[0].get("attributes")
    if not isinstance(attributes, dict) or not isinstance(attributes.get("id"), str) or not attributes["id"]:
        raise ValueError("Exact platform state contains malformed managed resource attributes.")
    return attributes


def _owned_capacity(reads: _Reads, config: dict, scope: dict) -> None:
    from scripts.deploy_simulator_environment import environment_root, state_path

    root = environment_root(config)
    ownership_path = root / "environment.json"
    platform_path = state_path(root, "platform", config["environment"])
    evidence = {
        "status": "missing_state", "owned_capacity": 0,
        "requested_capacity": scope["model"]["capacity"], "incremental_capacity": scope["model"]["capacity"],
        "ownership_path": str(ownership_path), "state_path": str(platform_path),
        "provenance": "exact environment ownership + managed platform workspace state + live ARM GETs",
    }
    scope["model_capacity_credit"] = evidence
    ownership = _state_json(ownership_path, "Environment ownership file")
    if ownership is not None:
        for field in ("environment", "subscription_id", "tenant_id", "owner_id"):
            value = ownership.get(field)
            expected = scope[field]
            if not isinstance(value, str) or (value != expected if field == "environment" else value.casefold() != expected.casefold()):
                raise ValueError(f"Environment ownership {field} does not match the exact configured stack.")
    state = _state_json(platform_path, "Exact platform Terraform state")
    if state is None:
        return
    if ownership is None:
        raise ValueError("Exact platform Terraform state exists without its environment ownership file.")
    resources = state.get("resources", [])
    if state.get("version") != 4 or not isinstance(resources, list) or not all(isinstance(resource, dict) for resource in resources):
        raise ValueError("Exact platform Terraform state has a malformed state/resource schema.")
    state = {**state, "resources": resources}
    deployment = _managed_attributes(state, "azapi_resource", "foundry_deployment")
    if deployment is None:
        evidence["status"] = "no_current_owned_deployment"
        return
    account = _managed_attributes(state, "azapi_resource", "foundry")
    if account is None:
        raise ValueError("Owned Foundry deployment has no current managed parent account in the exact platform state.")
    deployment_id = _arm_id(deployment["id"], "Microsoft.CognitiveServices/accounts/deployments", "State-owned deployment ID")
    account_id = _arm_id(account["id"], "Microsoft.CognitiveServices/accounts", "State-owned Foundry account ID")
    if (str(deployment.get("type", "")).split("@")[0].casefold() != "microsoft.cognitiveservices/accounts/deployments"
            or str(account.get("type", "")).split("@")[0].casefold() != "microsoft.cognitiveservices/accounts"
            or deployment_id.rsplit("/", 2)[0].casefold() != account_id.casefold()
            or str(deployment.get("parent_id", "")).casefold() != account_id.casefold()):
        raise ValueError("State-owned deployment ID/type/parent does not match the state's Foundry account.")
    group_name = scope["resource_group"]["name"]
    if not group_name:
        group = _managed_attributes(state, "azurerm_resource_group", "main")
        if (group is None or not isinstance(group.get("name"), str)
                or not re.fullmatch(r"rg-ap-" + re.escape(scope["environment"]) + r"-[a-z]{4}", group["name"])):
            raise ValueError("Exact platform state cannot establish the configured created resource group boundary.")
        group_name = group["name"]
        expected_group_id = f"/subscriptions/{scope['subscription_id']}/resourceGroups/{group_name}"
        if group["id"].casefold() != expected_group_id.casefold():
            raise ValueError("State-owned created resource group ID does not match the configured subscription/group.")
    expected_boundary = f"/subscriptions/{scope['subscription_id']}/resourceGroups/{group_name}/providers/"
    if not account_id.casefold().startswith(expected_boundary.casefold()):
        raise ValueError("State-owned Foundry account is outside the configured subscription/resource group.")
    if str(deployment.get("name", "")).casefold() != deployment_id.rsplit("/", 1)[-1].casefold():
        raise ValueError("State-owned deployment name does not match its deployment ID.")
    evidence.update(deployment_id=deployment_id, account_id=account_id, resource_group_name=group_name)
    try:
        live = _resource(
            reads, deployment_id, "Microsoft.CognitiveServices/accounts/deployments", "2025-06-01",
            "State-owned live deployment",
            "{id:id,type:type,name:name,sku:sku,properties:{model:properties.model,provisioningState:properties.provisioningState}}",
        )
        parent = _resource(reads, account_id, "Microsoft.CognitiveServices/accounts", "2025-06-01",
                           "State-owned live Foundry account")
    except RuntimeError:
        evidence["status"] = "live_resource_unverified"
        scope["warnings"].append("UNVERIFIED owned capacity: exact state-owned ARM resources are missing/unreadable; no capacity credit was granted.")
        return
    properties = live.get("properties") or {}
    if not isinstance(properties, dict):
        raise ValueError("State-owned live deployment has malformed model/SKU/provisioning metadata.")
    live_model = properties.get("model") or {}
    live_sku = live.get("sku") or {}
    if not all(isinstance(value, dict) for value in (properties, live_model, live_sku)):
        raise ValueError("State-owned live deployment has malformed model/SKU/provisioning metadata.")
    model = scope["model"]
    if (str(live.get("name", "")).casefold() != model["deployment_name"].casefold()
            or str(live_model.get("format", "")).casefold() != "openai"
            or str(live_model.get("name", "")).casefold() != model["name"].casefold()
            or live_model.get("version") != model["version"]
            or str(live_sku.get("name", "")).casefold() != model["sku"].casefold()
            or str(parent.get("location", "")).casefold() != scope["regions"]["ai"]
            or properties.get("provisioningState") != "Succeeded"):
        evidence["status"] = "live_configuration_not_matching"
        scope["warnings"].append("No owned capacity credit: exact live deployment model/version/SKU/name/state or account region differs from the configured target.")
        return
    capacity = live_sku.get("capacity")
    if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 0:
        raise ValueError("State-owned live deployment capacity must be a nonnegative integer.")
    owned = min(capacity, model["capacity"])
    evidence.update(status="verified", live_capacity=capacity, owned_capacity=owned,
                    incremental_capacity=model["capacity"] - owned)


def _quota(reads: _Reads, scopes: list[dict]) -> None:
    groups = defaultdict(list)
    for scope in scopes:
        model = scope["model"]
        groups[(scope["subscription_id"], scope["regions"]["ai"],
                _normalized(model["name"]), _normalized(model["sku"]))].append(scope)
    for (subscription, location, model, sku), members in groups.items():
        requested = sum(member["model"]["capacity"] for member in members)
        owned = sum(member["model_capacity_credit"]["owned_capacity"] for member in members)
        incremental = requested - owned
        evidence = {"status": "unverified", "requested_capacity": requested,
                    "owned_capacity": owned, "incremental_capacity": incremental}
        warning = None
        try:
            usage = reads.get(
                f"/subscriptions/{subscription}/providers/Microsoft.CognitiveServices/locations/{location}/usages",
                "2025-06-01", "{value:value,nextLink:nextLink}",
            )
        except _ReadFailure as error:
            if not error.denied:
                raise RuntimeError("Cognitive Services model quota query failed; quota is not verified.") from None
            usage = None
            warning = "UNVERIFIED model quota: access denied; available model/SKU capacity is not proven."
        if usage is not None:
            if not isinstance(usage, dict) or not isinstance(usage.get("value"), list):
                raise RuntimeError("Cognitive Services model quota query returned an invalid response.")
            matches = []
            for item in usage["value"]:
                name = item.get("name", {})
                meter = name.get("value", "") if isinstance(name, dict) else ""
                if isinstance(meter, str) and _normalized(meter) == "openai" + sku + model:
                    matches.append(item)
            if usage.get("nextLink") or len(matches) != 1:
                warning = "UNVERIFIED model quota: the API did not identify one exact model/SKU meter in a complete response; no capacity was guessed."
            else:
                meter = matches[0]
                limit, current = meter.get("limit"), meter.get("currentValue")
                if (meter.get("unit") != "Count"
                        or any(isinstance(value, bool) or not isinstance(value, (int, float))
                               or not math.isfinite(value) or value < 0 for value in (limit, current))):
                    warning = "UNVERIFIED model quota: the exact meter lacks comparable finite Count units; no conversion was guessed."
                else:
                    free = limit - current
                    if incremental > free:
                        raise ValueError(
                            f"Insufficient model quota in {location}: selected stacks request {requested} capacity "
                            f"units ({owned} exact-state-owned, {incremental} incremental) but the exact "
                            f"model/SKU meter has {free:g} free units."
                        )
                    evidence.update(status="verified", meter=meter["name"]["value"],
                                    limit=limit, current_usage=current, free_capacity=free)
        for member in members:
            member["model_quota"] = dict(evidence)
            if warning:
                member["warnings"].append(warning)


def plan(configs: list[dict]) -> list[dict]:
    """Read Azure prerequisites for expanded stacks, raising ValueError/RuntimeError on blockers.

    Returns JSON-safe allowlisted reports, not a deployment authorization or a state file.
    All Azure commands are account/provider/model-catalog reads or explicit ARM GETs, cached only for this call.
    """
    if not isinstance(configs, list) or not configs or not all(isinstance(config, dict) for config in configs):
        raise ValueError("plan requires a nonempty list of expanded stack configuration dictionaries.")
    scopes = [_scope(config) for config in configs]
    scheduler_required = any(scope["scheduler_enabled"] for scope in scopes)
    reads = _Reads()
    credited_ids = set()
    for config, scope in zip(configs, scopes):
        _account_and_providers(reads, scope, scheduler_required=scheduler_required)
        _model_availability(reads, scope)
        _group(reads, scope)
        _network(reads, scope)
        _monitoring(reads, scope)
        _build(reads, scope)
        _owned_capacity(reads, config, scope)
        credit = scope["model_capacity_credit"]
        if credit["owned_capacity"]:
            deployment_id = credit["deployment_id"].casefold()
            if deployment_id in credited_ids:
                raise ValueError("The same state-owned deployment cannot receive capacity credit in multiple planned stacks.")
            credited_ids.add(deployment_id)
    _quota(reads, scopes)
    return scopes
