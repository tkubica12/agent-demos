from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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
        config["environment"] = base + "-" + name
        config["stack_name"] = name
        config["agents"] = [{"name": name, "display_name": stack.get("display_name", name),
                             "assignment": stack.get("assignment", "")}]
        config["model_capacity"] = stack.get("model_capacity", inputs.get("model_capacity", 15))
        result.append(validate_config(config, validate_expiry=validate_expiry))
    return result


def environment_root(config: dict) -> Path:
    return REPO_ROOT / ".local" / ("simulator-" + config["environment"])


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
    cache = REPO_ROOT / ".local" / "terraform-provider-cache"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("TF_PLUGIN_CACHE_DIR", str(cache))
    write_tfvars(root / "generated.simulator.auto.tfvars.json", values)
    run(["terraform", "init", "-input=false", "-lockfile=readonly"], cwd=root)
    selected = run(["terraform", "workspace", "select", workspace], cwd=root, capture=True, check=False)
    if selected.returncode:
        if destroy:
            raise ValueError("The exact teardown workspace does not exist.")
        run(["terraform", "workspace", "new", workspace], cwd=root)
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
        principal = output(["az", "ad", "signed-in-user", "show", "--query", "id", "-o", "tsv"])
        existing = {key: config[key] for key in ("environment", "subscription_id", "tenant_id", "owner_id")}
        existing["deployer_principal_id"] = str(uuid.UUID(principal))
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
        deployer_principal_id=ownership["deployer_principal_id"], worker_owner_id=config["owner_id"],
        role_blueprint=release["blueprint"], role_blueprint_source=release["source"],
        role_blueprint_path=release.get("path", ""), role_release=release["release"],
        role_release_commit=release["commit"], assignment_scope=agent.get("assignment", ""),
        user_scheduling_enabled=False, servicebus_dream_enabled=False,
        collective_approval_private_key=approval["privateKey"], collective_approval_public_key=approval["publicKey"],
    )
    values.update({
        "channel": "simulator", "demo_mcp_enabled": False, "document_retry_enabled": False,
        "platform_state_path": str(state_path(root, "platform", config["environment"])),
        "simulator_auth_mode": config["auth_mode"], "simulator_url": simulator_url,
        "simulator_demo_key": load(root / "secrets.json")["agent_keys"][name],
        "simulator_agent_client_id": identities.get("agents", {}).get(name, {}).get("client_id", ""),
        "simulator_channel_client_id": identities.get("channel", {}).get("client_id", ""),
    })
    image_path = root / "runtime-image.json"
    if image_path.exists():
        values.update(load(image_path))
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


def build_images(config: dict, root: Path) -> None:
    from scripts.build_images import acr_build, import_hermes_upstream

    platform = load(root / "platform-outputs.json")
    registry, server = platform["acr_name"], platform["acr_login_server"]
    tag = "sim-" + output(["git", "rev-parse", "--short=12", "HEAD"], cwd=REPO_ROOT)
    runtime_path = root / "runtime-image.json"
    if not runtime_path.exists():
        base = import_hermes_upstream(registry, server)
        digest = acr_build(registry=registry, repository="hermes-runtime",
                           dockerfile="runtimes/hermes/Dockerfile", context=".", tag=tag,
                           build_args={"HERMES_BASE_IMAGE": base})
        save(runtime_path, {
            "runtime_image": f"{server}/hermes-runtime@{digest}",
            "runtime_disk_source_image": f"{server}/hermes-runtime:{tag}",
            "runtime_disk_image_name": "hermes-runtime-" + digest.removeprefix("sha256:")[:12],
        })
    if not (REPO_ROOT / "chat-simulator" / "Dockerfile").exists():
        raise FileNotFoundError("Session A chat-simulator/Dockerfile is not present. Merge its pushed source before building.")
    digest = acr_build(registry=registry, repository="chat-simulator",
                       dockerfile="Dockerfile", context="chat-simulator", tag=tag)
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

    identity = load(root / "identities.json") if (root / "identities.json").exists() else {}
    if config["auth_mode"] == "entra" and not identity.get("spa"):
        raise ValueError("Entra mode requires successful identity reconciliation first.")
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
        "source_commit": output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT),
    })
    print(json.dumps(load(root / "deployment.json"), indent=2))


def verify(config: dict, root: Path) -> None:
    import httpx
    from scripts.simulator_hosting import wait_health

    deployment = load(root / "deployment.json")
    wait_health(deployment["url"], "/api/health")
    for agent in config["agents"]:
        wait_health(load(root / agent["name"] / "outputs.json")["bridge_url"])
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        response = client.get(deployment["url"] + "/api/me")
        if response.status_code != 401:
            raise RuntimeError(f"Anonymous simulator data access returned {response.status_code}, expected 401.")
    print("Simulator and gateway health passed; anonymous access rejected.")
    if config["auth_mode"] == "demo" or os.environ.get("TSIM_SMOKE_ACCESS_TOKEN"):
        from scripts.simulator_smoke import verify_chat

        evidence = verify_chat(
            deployment["url"],
            access_code=load(root / "secrets.json")["demo_access_code"] if config["auth_mode"] == "demo" else "",
            access_token=os.environ.get("TSIM_SMOKE_ACCESS_TOKEN", ""),
        )
        save(root / "chat-verification.json", evidence)
        print(evidence["verified"])
    else:
        print("Entra real-chat verification not run: supply TSIM_SMOKE_ACCESS_TOKEN from an assigned SPA user.")


def destroy(config: dict, root: Path) -> None:
    from scripts.simulator_identity import destroy_identities

    ownership = load(root / "environment.json")
    for key in ("environment", "tenant_id", "subscription_id"):
        if ownership[key] != config[key]:
            raise ValueError("Teardown inputs differ from the captured ownership.")
    for agent in reversed(config["agents"]):
        layer = root / agent["name"]
        if state_path(root, agent["name"], config["environment"]).exists():
            terraform(layer, config["environment"], load(layer / "generated.simulator.auto.tfvars.json"), destroy=True)
    if state_path(root, "simulator", config["environment"]).exists():
        terraform(root / "simulator", config["environment"],
                  load(root / "simulator" / "generated.simulator.auto.tfvars.json"), destroy=True)
    if (root / "identities.json").exists():
        destroy_identities(config, root / "identities.json")
    if state_path(root, "platform", config["environment"]).exists():
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
    parser.add_argument("action", choices=["prepare", "infrastructure", "identities", "build", "deploy", "verify", "all", "destroy"],
                        nargs="?", default="all")
    args = parser.parse_args()
    configs = load_stack_configs(args.config, args.stack, validate_expiry=args.action != "destroy")
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
                build_images(config, root)
            if args.action in {"deploy", "all"}:
                deploy(config, root, ownership)
            if args.action in {"verify", "all"}:
                verify(config, root)
            if args.action == "destroy":
                destroy(config, root)


if __name__ == "__main__":
    main()
