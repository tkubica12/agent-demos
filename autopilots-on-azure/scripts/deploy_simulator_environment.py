from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import tomllib
import uuid

from scripts.deploy_apps_runtime import require_identity_preservation
from scripts.setup_app_tfvars import build_tfvars, load_or_create_collective_approval_identity
from scripts.tf_helpers import REPO_ROOT, output, run, terraform_output, write_tfvars


def load_config(path: Path, *, validate_expiry: bool = True) -> dict:
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    return validate_config(config, validate_expiry=validate_expiry)


def validate_config(config: dict, *, validate_expiry: bool = True) -> dict:
    environment = str(config.get("environment", ""))
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,23}", environment):
        raise ValueError("environment must be 3-24 lowercase letters, digits or hyphens.")
    for field in ("subscription_id", "tenant_id", "owner_id"):
        config[field] = str(uuid.UUID(config[field]))
    config.setdefault("auth_mode", "entra")
    if config["auth_mode"] not in {"entra", "demo"}:
        raise ValueError("auth_mode must be entra or demo.")
    if not isinstance(config.get("generated_chat_files_enabled", True), bool):
        raise ValueError("generated_chat_files_enabled must be a boolean.")
    config["worker_owner_id"] = config["owner_id"]
    if "demo_owner_display_name" in config:
        name = config["demo_owner_display_name"]
        if config["auth_mode"] != "demo":
            raise ValueError("demo_owner_display_name is only valid in DEMO mode.")
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError("demo_owner_display_name must contain 1-200 characters.")
        config["demo_owner_display_name"] = name.strip()
        config["worker_owner_id"] = str(uuid.uuid5(
            uuid.UUID("51f8c9cd-dcd4-4c33-9ef6-48b4c5919185"), name.strip().lower(),
        ))
    agents = config.get("agents")
    if agents is None:
        count = int(config.get("agent_count", 6))
        if not 1 <= count <= 20:
            raise ValueError("agent_count must be between 1 and 20.")
        agents = [{"name": f"agent-{index}", "display_name": f"Agent {index}"} for index in range(1, count + 1)]
    if not agents or len(agents) > 20:
        raise ValueError("Configure 1-20 agents.")
    names = [str(agent["name"]) for agent in agents]
    if len(set(names)) != len(names) or any(not re.fullmatch(r"agent-[1-9][0-9]*", name) for name in names):
        raise ValueError("Agent names must be distinct agent-1, agent-2, ...; existing Worker names are prohibited.")
    config["agents"] = agents
    role = config.get("role", {})
    if not all(role.get(key) for key in ("blueprint", "source", "release", "commit")):
        raise ValueError("role requires blueprint, source, release and a full immutable commit.")
    if not re.fullmatch(r"[a-fA-F0-9]{40}", role["commit"]):
        raise ValueError("role.commit must be a full 40-character Git commit SHA.")
    if role["source"] == "local":
        from scripts.role_bundle import build_role_bundle

        role["path"] = str(role.get("path") or "blueprints/" + role["blueprint"]).replace("\\", "/")
        build_role_bundle(role)
    if not isinstance(config.get("model_capacity", 100), int) or config.get("model_capacity", 100) < 1:
        raise ValueError("model_capacity must be a positive integer in the customer's model quota.")
    if config["auth_mode"] == "demo" and validate_expiry:
        expiry = datetime.fromisoformat(config["demo_expires_at"].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if expiry.utcoffset() != timedelta(0) or expiry <= now or (expiry - now).total_seconds() > 30 * 86400:
            raise ValueError("DEMO mode requires a future UTC demo_expires_at within 30 days.")
        if config.get("demo_acknowledge") != "test-data-only":
            raise ValueError('DEMO requires demo_acknowledge = "test-data-only".')
    return config


def load_stack_configs(path: Path, selected: list[str], *, validate_expiry: bool = True) -> list[dict]:
    inputs = tomllib.loads(path.read_text(encoding="utf-8"))
    base = str(inputs["environment"])
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,13}", base):
        raise ValueError("The deployment environment prefix must be 3-14 lowercase letters, digits or hyphens.")
    stacks = inputs.get("stacks")
    if stacks is None:
        count = inputs.get("stack_count", 6)
        if not isinstance(count, int) or not 1 <= count <= 20:
            raise ValueError("stack_count must be an integer between 1 and 20.")
        stacks = [{"name": f"agent-{index}"} for index in range(1, count + 1)]
    names = [stack["name"] for stack in stacks]
    if not names or len(set(names)) != len(names):
        raise ValueError("Stack names must be nonempty and unique.")
    missing = set(selected) - set(names)
    if missing:
        raise ValueError(f"Unknown selected stacks: {sorted(missing)}")
    result = []
    for stack in stacks:
        name = stack["name"]
        if not re.fullmatch(r"agent-[1-9][0-9]*", name):
            raise ValueError("Stack names must be agent-1, agent-2, ...")
        if selected and name not in selected:
            continue
        config = {**inputs, **stack}
        config["platform"] = {**inputs.get("platform", {}), **stack.get("platform", {})}
        config["build"] = {**inputs.get("build", {}), **stack.get("build", {})}
        config.setdefault("network_mode", "private")
        config.setdefault("resource_group_mode", "existing")
        if config["network_mode"] not in {"private", "public"}:
            raise ValueError("network_mode must be private or public; no public fallback is supported.")
        if config["resource_group_mode"] not in {"existing", "create"}:
            raise ValueError("resource_group_mode must be existing or create.")
        config["platform"]["private_network_enabled"] = config["network_mode"] == "private"
        config["platform"]["scheduler_enabled"] = False
        config["build"].setdefault("mode", "source")
        if config["build"]["mode"] not in {"import", "source"}:
            raise ValueError("build.mode must be import or source; there is no public fallback mode.")
        zones_file = config.get("private_dns_zone_ids_file")
        if zones_file:
            zones_path = (path.parent / zones_file).resolve()
            zones = load(zones_path)
            if not isinstance(zones, dict) or any(not isinstance(value, str) for value in zones.values()):
                raise ValueError("private_dns_zone_ids_file must contain a JSON object mapping zone names to ARM IDs.")
            config["platform"]["private_dns_zone_ids"] = zones
        if config["resource_group_mode"] == "existing" and not config["platform"].get("existing_resource_group_name"):
            raise ValueError(f"{name}: existing RG mode requires platform.existing_resource_group_name.")
        if config["resource_group_mode"] == "create" and config["platform"].get("existing_resource_group_name"):
            raise ValueError("create RG mode must not name an existing resource group.")
        if config["network_mode"] == "private" and config["resource_group_mode"] == "existing":
            for field in ("existing_sandbox_subnet_id", "existing_private_endpoint_subnet_id", "private_dns_zone_ids"):
                if not config["platform"].get(field):
                    raise ValueError(f"{name}: existing private mode requires platform.{field}.")
        config["environment"] = base + "-" + name
        config["stack_name"] = name
        config["agents"] = [{"name": name, "display_name": stack.get("display_name", name),
                             "assignment": stack.get("assignment", "")}]
        config["model_capacity"] = stack.get("model_capacity", inputs.get("model_capacity", 15))
        result.append(validate_config(config, validate_expiry=validate_expiry))
    return result


def state_root() -> Path:
    return Path(os.environ.get("AUTOPILOTS_STATE_ROOT", str(REPO_ROOT / ".local"))).expanduser().resolve()


def environment_root(config: dict) -> Path:
    return state_root() / ("simulator-" + config["environment"])


def source_revision() -> str:
    manifest = REPO_ROOT / "deployment-manifest.json"
    if manifest.exists():
        revision = load(manifest)["source_commit"]
    elif any((path / ".git").exists() for path in (REPO_ROOT, REPO_ROOT.parent)) and shutil.which("git"):
        revision = output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT)
    else:
        raise ValueError("A git-free snapshot requires deployment-manifest.json with source_commit (the full source SHA).")
    if not isinstance(revision, str) or not re.fullmatch(r"[a-fA-F0-9]{40}", revision):
        raise ValueError("The deployment source revision must be a full Git commit SHA.")
    return revision


def state_path(root: Path, layer: str, workspace: str) -> Path:
    return root / layer / "terraform.tfstate.d" / workspace / "terraform.tfstate"


def prepare_root(root: Path, layer: str, source: str) -> Path:
    target = root / layer
    target.mkdir(parents=True, exist_ok=True)
    template = REPO_ROOT / "terraform" / source
    for path in [*template.glob("*.tf"), *template.glob(".terraform.lock.hcl")]:
        shutil.copy2(path, target / path.name)
    return target


def terraform(root: Path, workspace: str, values: dict, *, destroy: bool = False) -> dict:
    if root.resolve().is_relative_to((REPO_ROOT / "terraform").resolve()):
        raise ValueError("Simulator may not operate in the shared Terraform roots.")
    cache = state_root() / "terraform-provider-cache"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("TF_PLUGIN_CACHE_DIR", str(cache))
    write_tfvars(root / "generated.simulator.auto.tfvars.json", values)
    run(["terraform", "init", "-input=false", "-lockfile=readonly"], cwd=root)
    selection = ["terraform", "workspace", "select"]
    if not destroy:
        selection.append("-or-create")
    run([*selection, workspace], cwd=root)
    if output(["terraform", "workspace", "show"], cwd=root) != workspace or workspace == "default":
        raise ValueError("Refusing to use Terraform's default workspace.")
    plan = root / "deployment.tfplan"
    arguments = ["terraform", "plan", "-input=false", f"-out={plan}"]
    if destroy:
        arguments.append("-destroy")
    run(arguments, cwd=root)
    payload = json.loads(output(["terraform", "show", "-json", str(plan)], cwd=root))
    if not destroy:
        require_identity_preservation(payload)
    run(["terraform", "apply", "-input=false", str(plan)], cwd=root)
    return {} if destroy else terraform_output(root)


def save(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def assert_account(config: dict) -> None:
    account = json.loads(output(["az", "account", "show", "--subscription", config["subscription_id"], "-o", "json"]))
    if account["id"] != config["subscription_id"] or account["tenantId"] != config["tenant_id"]:
        raise ValueError("Azure CLI subscription or tenant differs from the inputs; use az login --use-device-code.")
    os.environ["ARM_SUBSCRIPTION_ID"] = config["subscription_id"]
    os.environ["ARM_TENANT_ID"] = config["tenant_id"]
    os.environ["AZURE_SUBSCRIPTION_ID"] = config["subscription_id"]


def prepare(config: dict, root: Path) -> dict:
    ownership = root / "environment.json"
    if ownership.exists():
        existing = load(ownership)
        for key in ("environment", "subscription_id", "tenant_id", "owner_id"):
            if existing[key] != config[key]:
                raise ValueError(f"Existing deployment {key} differs; refusing a different state boundary.")
        removed = set(existing.get("agent_names", [])) - {agent["name"] for agent in config["agents"]}
        if removed:
            raise ValueError(f"Removing Workers requires teardown with the original inventory first: {sorted(removed)}")
    else:
        token = output(["az", "account", "get-access-token", "--resource", "https://management.azure.com/",
                        "--query", "accessToken", "-o", "tsv"])
        claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
        if claims.get("tid") != config["tenant_id"]:
            raise ValueError("The deployment principal token belongs to a different tenant.")
        existing = {key: config[key] for key in ("environment", "subscription_id", "tenant_id", "owner_id")}
        existing["deployer_principal_id"] = str(uuid.UUID(claims["oid"]))
    existing["agent_names"] = [agent["name"] for agent in config["agents"]]
    save(ownership, existing)
    private = root / "secrets.json"
    values = load(private) if private.exists() else {
        "demo_access_code": secrets.token_urlsafe(24), "agent_keys": {}, "registration_client_ids": {},
    }
    values.setdefault("registration_client_ids", {})
    for agent in config["agents"]:
        values["agent_keys"].setdefault(agent["name"], secrets.token_urlsafe(32))
        values["registration_client_ids"].setdefault(agent["name"], str(uuid.uuid4()))
    save(private, values)
    prepare_root(root, "platform", "platform")
    prepare_root(root, "simulator", "simulator")
    for agent in config["agents"]:
        prepare_root(root, agent["name"], "apps")
    return existing


def platform_values(config: dict) -> dict:
    return {
        "environment_name": config["environment"],
        "model_capacity": config.get("model_capacity", 100),
        **config.get("platform", {}),
        "scheduler_enabled": False,
    }


def worker_values(config: dict, root: Path, agent: dict, ownership: dict, identities: dict,
                  *, simulator_url: str = "") -> dict:
    name = agent["name"]
    previous_path = root / name / "generated.simulator.auto.tfvars.json"
    previous = load(previous_path) if previous_path.exists() else {}
    release = config["role"]
    approval = load_or_create_collective_approval_identity(root / name / "approval.json")
    values = build_tfvars(
        runtime="hermes", autopilot_name=name, previous=previous,
        deployer_principal_id=ownership["deployer_principal_id"],
        worker_owner_id=config.get("worker_owner_id", config["owner_id"]),
        role_blueprint=release["blueprint"], role_blueprint_source=release["source"],
        role_blueprint_path=release.get("path", ""), role_release=release["release"],
        role_release_commit=release["commit"], assignment_scope=agent.get("assignment", ""),
        user_scheduling_enabled=False, servicebus_dream_enabled=False,
        collective_approval_private_key=approval["privateKey"], collective_approval_public_key=approval["publicKey"],
    )
    values.update({
        "channel": "simulator", "demo_mcp_enabled": False, "document_retry_enabled": False,
        "generated_chat_files_enabled": config.get("generated_chat_files_enabled", True),
        "private_network_enabled": config.get("network_mode", "private") == "private",
        "required_tags": config.get("platform", {}).get("required_tags", {}),
        "hermes_role_bundle_sha256": release.get("bundle_sha256", ""),
        "platform_state_path": str(state_path(root, "platform", config["environment"])),
        "simulator_auth_mode": config["auth_mode"], "simulator_url": simulator_url,
        "simulator_demo_key": load(root / "secrets.json")["agent_keys"][name],
        "simulator_agent_client_id": identities.get("agents", {}).get(name, {}).get("client_id", ""),
        "simulator_channel_client_id": identities.get("channel", {}).get("client_id", ""),
    })
    image_path = root / "runtime-image.json"
    if image_path.exists():
        images = load(image_path)
        values.update({key: images[key] for key in ("runtime_image", "runtime_disk_source_image", "runtime_disk_image_name")})
    gateway_image = root / "gateway-image.json"
    if gateway_image.exists():
        values.update(load(gateway_image))
    return values


def infrastructure(config: dict, root: Path, ownership: dict) -> None:
    terraform(root / "platform", config["environment"], platform_values(config))
    save(root / "platform-outputs.json", terraform_output(root / "platform"))
    shared = {
        "platform_state_path": str(state_path(root, "platform", config["environment"])),
        "environment_name": config["environment"],
        "deployer_principal_id": ownership["deployer_principal_id"],
    }
    hosting = terraform(root / "simulator", config["environment"], shared)
    bindings = {"channel": hosting["channel_binding"], "agents": {}}
    for agent in config["agents"]:
        worker = terraform(root / agent["name"], config["environment"],
                           worker_values(config, root, agent, ownership, {}))
        gateway = worker["sandbox_groups"]["gateway"]
        bindings["agents"][agent["name"]] = {
            "identity_client_id": gateway["identity_client_id"],
            "identity_principal_id": gateway["identity_principal_id"],
            "runtime_identity_principal_id": worker["sandbox_groups"]["runtime"]["identity_principal_id"],
        }
        save(root / agent["name"] / "outputs.json", worker)
        save(root / "bindings.json", bindings)


def import_image(target: dict, source_registry_id: str, source_server: str, image: str,
                 *, repository: str = "") -> tuple[str, str]:
    prefix = source_server + "/"
    if not image.startswith(prefix):
        raise ValueError("An imported image must belong to the configured source registry.")
    reference = image.removeprefix(prefix)
    if not re.fullmatch(r"[a-z0-9._/-]+@sha256:[a-f0-9]{64}", reference):
        raise ValueError("An imported image must use a complete immutable SHA-256 digest.")
    original, digest = reference.split("@", 1)
    repository = repository or original
    tag = repository + ":bundle-" + digest.removeprefix("sha256:")[:12]
    repositories = json.loads(output(["az", "acr", "repository", "list", "--name", target["acr_name"],
                                      "-o", "json", "--only-show-errors"]))
    tags = []
    if repository in repositories:
        tags = json.loads(output(["az", "acr", "repository", "show-tags", "--name", target["acr_name"],
                                  "--repository", repository, "-o", "json", "--only-show-errors"]))
    if tag.split(":", 1)[1] not in tags:
        run(["az", "acr", "import", "--name", target["acr_name"],
             *(["--registry", source_registry_id] if source_registry_id else []),
             "--source", reference if source_registry_id else image, "--image", tag, "--only-show-errors"])
    actual = output(["az", "acr", "repository", "show", "--name", target["acr_name"],
                     "--image", tag, "--query", "digest", "-o", "tsv", "--only-show-errors"])
    if actual != digest:
        raise ValueError("Independent registry copy did not preserve the image digest.")
    return target["acr_login_server"] + "/" + repository + "@" + digest, target["acr_login_server"] + "/" + tag


def copy_images(source_root: Path, root: Path) -> None:
    source = load(source_root / "platform-outputs.json")
    target = load(root / "platform-outputs.json")
    if source["acr_id"] == target["acr_id"]:
        raise ValueError("Independent stacks must not share a registry.")
    artifacts = [
        ("runtime-image.json", "runtime_image", "runtime_disk_source_image"),
        ("simulator-image.json", "image", "source"),
    ]
    if (source_root / "gateway-image.json").exists():
        artifacts.append(("gateway-image.json", "bridge_image", "bridge_disk_source_image"))
    for filename, image_key, tag_key in artifacts:
        manifest = load(source_root / filename)
        image, tagged = import_image(target, source["acr_id"], source["acr_login_server"], manifest[image_key])
        save(root / filename, {
            **manifest,
            image_key: image,
            tag_key: tagged,
        })


def import_configured_images(config: dict, root: Path, platform: dict) -> None:
    settings = config["build"]
    if config["network_mode"] == "private" and not (
        settings.get("allow_trusted_service_import", False) and platform.get("acr_trusted_services_bypass_enabled", False)
    ):
        raise ValueError("Private ARM image import requires explicit build.allow_trusted_service_import approval.")
    for key in ("source_registry_id", "source_registry_server", "runtime_image", "simulator_image", "gateway_image"):
        if not settings.get(key):
            raise ValueError("Prebuilt import mode requires build." + key)
    source = json.loads(output(["az", "resource", "show", "--ids", settings["source_registry_id"],
                                "--api-version", "2023-07-01", "-o", "json", "--only-show-errors"]))
    if (source["type"].lower() != "microsoft.containerregistry/registries"
            or source["properties"]["loginServer"] != settings["source_registry_server"]
            or config["network_mode"] == "private" and source["properties"].get("publicNetworkAccess") != "Disabled"):
        raise ValueError("Prebuilt source registry must match its ARM ID/login server and remain private in private mode.")
    for source_key, filename, image_key, tag_key in (
        ("runtime_image", "runtime-image.json", "runtime_image", "runtime_disk_source_image"),
        ("simulator_image", "simulator-image.json", "image", "source"),
        ("gateway_image", "gateway-image.json", "bridge_image", "bridge_disk_source_image"),
    ):
        image, tagged = import_image(platform, settings["source_registry_id"], settings["source_registry_server"],
                                     settings[source_key])
        values = {image_key: image, tag_key: tagged}
        if source_key == "runtime_image":
            values["runtime_disk_image_name"] = "hermes-runtime-" + image.split("@sha256:", 1)[1][:12]
        save(root / filename, values)


def build_images(config: dict, root: Path, *, source_root: Path | None = None) -> None:
    from scripts.build_images import acr_build, import_hermes_upstream, HERMES_UPSTREAM_SOURCE, HERMES_UPSTREAM_DIGEST

    platform = load(root / "platform-outputs.json")
    private = config.get("network_mode", "private") == "private"
    if source_root is not None and (
        not private or (
            config.get("build", {}).get("allow_trusted_service_import", False)
            and platform.get("acr_trusted_services_bypass_enabled", False)
        )
    ):
        copy_images(source_root, root)
        return
    if config.get("build", {}).get("mode") == "import":
        import_configured_images(config, root, platform)
        return
    registry, server = platform["acr_name"], platform["acr_login_server"]
    pool = platform.get("acr_agent_pool_name", "")
    if private and not pool:
        raise ValueError(
            "Private ACR builds require a dedicated VNet agent pool (platform.acr_agent_pool_subnet_id). "
            "Default quick-build and public staging registries are not permitted."
        )
    tag = "sim-" + source_revision()[:12]
    if private:
        from scripts.application_releases import PYTHON_BASE_IMAGE

        digest = acr_build(
            registry=registry, repository="bridge-base", dockerfile=str(Path("runtimes") / "gateway" / "Dockerfile"),
            context=".", tag=tag, agent_pool_name=pool,
            build_args={"PYTHON_BASE_IMAGE": config.get("build", {}).get("gateway_base_image", PYTHON_BASE_IMAGE)},
        )
        save(root / "gateway-image.json", {
            "bridge_image": f"{server}/bridge-base@{digest}", "bridge_disk_source_image": f"{server}/bridge-base:{tag}",
        })
    runtime_path = root / "runtime-image.json"
    base = (
        config.get("build", {}).get("hermes_base_image", f"{HERMES_UPSTREAM_SOURCE}@{HERMES_UPSTREAM_DIGEST}")
        if private else import_hermes_upstream(registry, server)
    )
    dependencies = (
        "Dockerfile", "pyproject.toml", "uv.lock", "hermes-site.txt", "package.json", "package-lock.json",
        "office_helper/OfficeCollaboration.csproj", "office_helper/Program.cs",
    )
    build_fingerprint = hashlib.sha256(json.dumps({
        "base": base,
        "files": {name: hashlib.sha256((REPO_ROOT / "runtimes" / "hermes" / name).read_bytes()).hexdigest()
                  for name in dependencies},
    }, sort_keys=True).encode()).hexdigest()
    if not runtime_path.exists() or load(runtime_path).get("build_fingerprint") != build_fingerprint:
        digest = acr_build(registry=registry, repository="hermes-runtime",
                           dockerfile=str(Path("runtimes") / "hermes" / "Dockerfile"), context=".", tag=tag,
                           build_args={"HERMES_BASE_IMAGE": base}, agent_pool_name=pool)
        save(runtime_path, {
            "runtime_image": f"{server}/hermes-runtime@{digest}",
            "runtime_disk_source_image": f"{server}/hermes-runtime:{tag}",
            "runtime_disk_image_name": "hermes-runtime-" + digest.removeprefix("sha256:")[:12],
            "build_fingerprint": build_fingerprint,
        })
    if not (REPO_ROOT / "chat-simulator" / "Dockerfile").exists():
        raise FileNotFoundError("Session A chat-simulator/Dockerfile is not present. Merge its pushed source before building.")
    digest = acr_build(registry=registry, repository="chat-simulator",
                       dockerfile=str(Path("chat-simulator") / "Dockerfile"), context="chat-simulator", tag=tag,
                       build_args={"SIMULATOR_CONTAINER_USER": "0:0"}, agent_pool_name=pool)
    save(root / "simulator-image.json", {
        "image": f"{server}/chat-simulator@{digest}", "source": f"{server}/chat-simulator:{tag}",
    })


def identities(config: dict, root: Path, *, redirect_uri: str = "") -> dict:
    from scripts.simulator_identity import reconcile_identities

    return reconcile_identities(config, load(root / "bindings.json"), root / "identities.json",
                                redirect_uri=redirect_uri)


def deploy(config: dict, root: Path, ownership: dict) -> None:
    from scripts.sandbox_services import deploy_sandbox_services
    from scripts.simulator_hosting import configure_host, deploy_host

    identity = {}
    if config["auth_mode"] == "entra" and not (root / "identities.json").exists():
        raise ValueError("Entra mode requires the identity stage first.")
    platform = load(root / "platform-outputs.json")
    binding = deploy_host(config, load(root / "bindings.json")["channel"], platform,
                          load(root / "simulator-image.json"), root / "hosting.json")
    if config["auth_mode"] == "entra":
        identity = identities(config, root, redirect_uri=binding["url"] + "/")
    registrations = []
    for agent in config["agents"]:
        name = agent["name"]
        values = worker_values(config, root, agent, ownership, identity, simulator_url=binding["url"])
        worker = terraform(root / name, config["environment"], values)
        services = deploy_sandbox_services(worker, platform)
        save(root / name / "outputs.json", {**worker, **services})
        registrations.append({
            "name": name, "displayName": agent.get("display_name", name),
            "clientId": (
                identity["agents"][name]["client_id"] if config["auth_mode"] == "entra"
                else load(root / "secrets.json")["registration_client_ids"][name]
            ),
            "messagingEndpoint": services["bridge_url"] + "/api/messages",
            **({"demoKey": values["simulator_demo_key"]} if config["auth_mode"] == "demo" else {}),
        })
    hosted_config = {**config, **load(root / "secrets.json")}
    configure_host(hosted_config, binding, identity, registrations)
    save(root / "deployment.json", {
        "environment": config["environment"], "auth_mode": config["auth_mode"],
        "url": binding["url"], "resource_group": platform["resource_group_name"],
        "agents": [{key: value for key, value in item.items() if key != "demoKey"} for item in registrations],
        "source_commit": source_revision(),
    })
    print(json.dumps(load(root / "deployment.json"), indent=2))


def verify(config: dict, root: Path) -> None:
    import httpx
    from scripts.simulator_hosting import wait_health

    deployment = load(root / "deployment.json")
    evidence = {"auth_mode": config["auth_mode"], "simulator": wait_health(deployment["url"], "/api/health"), "gateways": {}}
    for agent in config["agents"]:
        evidence["gateways"][agent["name"]] = wait_health(load(root / agent["name"] / "outputs.json")["bridge_url"])
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        response = client.get(deployment["url"] + "/api/me")
        if response.status_code != 401:
            raise RuntimeError(f"Anonymous simulator data access returned {response.status_code}, expected 401.")
        evidence["anonymous_data_status"] = response.status_code
    save(root / "verification.json", evidence)
    print("Simulator and gateway health passed; anonymous access rejected.")
    if config["auth_mode"] == "demo":
        from scripts.simulator_smoke import verify_chat

        chat_evidence = verify_chat(
            deployment["url"],
            access_code=load(root / "secrets.json")["demo_access_code"],
        )
        save(root / "chat-verification.json", chat_evidence)
        evidence["real_chat"] = chat_evidence
        save(root / "verification.json", evidence)
        print(chat_evidence["verified"])
    else:
        path = root / "browser-acceptance.json"
        if not path.exists():
            raise ValueError(
                "Entra acceptance requires an assigned user's in-VNet browser evidence at "
                + str(path) + "; never paste or export access tokens."
            )
        manual = load(path)
        expected = ("login", "messageReply", "typing", "reactions", "cards", "groupChat", "files")
        if (manual.get("schemaVersion") != 1 or manual.get("environment") != config["environment"]
                or manual.get("url") != deployment["url"] or manual.get("authMode") != "entra"
                or any(manual.get("checks", {}).get(key) is not True for key in expected)):
            raise ValueError("Browser acceptance evidence is incomplete or belongs to a different stack/URL.")
        verified = datetime.fromisoformat(str(manual["verifiedAt"]).replace("Z", "+00:00"))
        if verified.utcoffset() != timedelta(0):
            raise ValueError("Browser evidence must record UTC acceptance from the last seven days.")
        age = datetime.now(timezone.utc) - verified
        if not timedelta(0) <= age <= timedelta(days=7):
            raise ValueError("Browser evidence must record UTC acceptance from the last seven days.")
        evidence["manual_browser_acceptance"] = manual
        save(root / "verification.json", evidence)
        print("Assigned-user browser acceptance recorded; evidence is explicitly manual, not an automated Entra login.")


def remove_owned_smart_alert(config: dict, platform: dict) -> None:
    component = platform.get("application_insights_id", "")
    resource_group = platform.get("resource_group_name", "")
    boundary = f"/subscriptions/{config['subscription_id']}/resourceGroups/{resource_group}"
    if not component:
        return
    settings = config.get("platform", {})
    if settings.get("existing_application_insights_id"):
        return
    expected_group = "rg-ap-" + config["environment"] + "-"
    owned_group = resource_group.startswith(expected_group) or resource_group == settings.get("existing_resource_group_name")
    if not owned_group or not component.lower().startswith(boundary.lower() + "/"):
        raise ValueError("Monitoring cleanup differs from this stack's resource boundary.")
    name = "Failure Anomalies - " + component.rsplit("/", 1)[-1]
    resources = json.loads(output([
        "az", "resource", "list", "--subscription", config["subscription_id"],
        "--resource-group", resource_group, "-o", "json", "--only-show-errors",
    ]))
    for resource in resources:
        if resource["type"].lower() != "microsoft.alertsmanagement/smartdetectoralertrules" or resource["name"] != name:
            continue
        expected = boundary + "/providers/microsoft.alertsmanagement/smartDetectorAlertRules/" + name
        if resource["id"].lower() != expected.lower():
            raise ValueError("Auto-created monitoring alert is outside the exact captured boundary.")
        run(["az", "resource", "delete", "--ids", resource["id"], "--subscription",
             config["subscription_id"], "--only-show-errors"])


def destroy(config: dict, root: Path) -> None:
    from scripts.sandbox_services import delete_owned_sandboxes
    from scripts.simulator_identity import destroy_identities

    ownership = load(root / "environment.json")
    for key in ("environment", "tenant_id", "subscription_id"):
        if ownership[key] != config[key]:
            raise ValueError("Teardown inputs differ from the captured ownership.")
    for agent in reversed(config["agents"]):
        layer = root / agent["name"]
        if state_path(root, agent["name"], config["environment"]).exists():
            infrastructure = terraform_output(layer)
            if infrastructure.get("sandbox_groups"):
                delete_owned_sandboxes(
                    list(infrastructure["sandbox_groups"].values()),
                    {"app": "autopilots-on-azure", "worker": agent["name"]},
                    subscription_id=config["subscription_id"],
                    resource_group=infrastructure["resource_group_name"], location=infrastructure["sandbox_location"],
                )
            terraform(layer, config["environment"], load(layer / "generated.simulator.auto.tfvars.json"), destroy=True)
    if state_path(root, "simulator", config["environment"]).exists():
        binding = load(root / "bindings.json")["channel"]
        delete_owned_sandboxes(
            [binding], {"app": "autopilots-on-azure", "environment": config["environment"], "service": "simulator"},
            subscription_id=config["subscription_id"], resource_group=binding["resource_group_name"],
            location=binding["location"],
        )
        terraform(root / "simulator", config["environment"],
                  load(root / "simulator" / "generated.simulator.auto.tfvars.json"), destroy=True)
    if (root / "identities.json").exists():
        destroy_identities(config, root / "identities.json")
    if state_path(root, "platform", config["environment"]).exists():
        captured = root / "platform-outputs.json"
        remove_owned_smart_alert(config, load(captured) if captured.exists() else terraform_output(root / "platform"))
        terraform(root / "platform", config["environment"], platform_values(config), destroy=True)
    save(root / "teardown.json", {"completed_at": datetime.now(timezone.utc).isoformat()})


@contextmanager
def deployment_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    path = root / "deployment.lock"
    with path.open("x", encoding="utf-8") as file:
        file.write(str(os.getpid()))
    try:
        yield
    finally:
        path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy fully isolated Agent Chat Simulator/Hermes stacks, one resource group each.")
    parser.add_argument("--config", type=Path, required=True, help="Local TOML inputs; never commit customer identifiers or secrets.")
    parser.add_argument("--stack", action="append", default=[], help="Select agent-1, agent-2, ...; repeat for a subset. Defaults to all.")
    parser.add_argument("--state-root", type=Path, help="Durable state directory outside the extracted source tree; also AUTOPILOTS_STATE_ROOT.")
    parser.add_argument("action", choices=["plan", "prepare", "infrastructure", "identities", "build", "deploy", "verify", "all", "destroy"],
                        nargs="?", default="all")
    args = parser.parse_args()
    if args.state_root is not None:
        os.environ["AUTOPILOTS_STATE_ROOT"] = str(args.state_root.expanduser().resolve())
    configs = load_stack_configs(args.config, args.stack, validate_expiry=args.action != "destroy")
    if args.action == "plan":
        from scripts.simulator_preflight import plan

        print(json.dumps(plan(configs), indent=2))
        return
    image_seed = None
    image_profile = None
    for config in configs:
        print(f"Stack {config['stack_name']}: {args.action}", flush=True)
        assert_account(config)
        root = environment_root(config)
        with deployment_lock(root):
            ownership = prepare(config, root)
            if args.action == "prepare":
                print(f"Prepared isolated deployment: {root}")
            if args.action in {"infrastructure", "all"}:
                infrastructure(config, root, ownership)
            if args.action == "identities" or (args.action == "all" and config["auth_mode"] == "entra"):
                identities(config, root)
            if args.action in {"build", "all"}:
                profile = (config["network_mode"], json.dumps(config["build"], sort_keys=True))
                build_images(config, root, source_root=image_seed if profile == image_profile else None)
                image_seed = root
                image_profile = profile
            if args.action in {"deploy", "all"}:
                deploy(config, root, ownership)
            if args.action in {"verify", "all"}:
                verify(config, root)
            if args.action == "destroy":
                destroy(config, root)


if __name__ == "__main__":
    main()
