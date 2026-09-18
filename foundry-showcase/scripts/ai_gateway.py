from __future__ import annotations

import argparse
import json
import os
import tomllib
from pathlib import Path
from typing import Any

from deploy_phase2 import run, run_json
from ai_gateway_runtime import Gateway, budget_demo, demo
from ai_gateway_telemetry import telemetry


SHOWCASE_ROOT = Path(__file__).resolve().parents[1]
TERRAFORM_DIR = SHOWCASE_ROOT / "terraform" / "ai-gateway"
DEFAULT_CONFIG = SHOWCASE_ROOT / "ai-gateway.toml"
GATEWAY_API = "2025-09-01-preview"
CONNECTOR_API = "2026-05-01-preview"
FOUNDRY_API = "2025-06-01"


def terraform_variables(config: dict[str, Any]) -> dict[str, str | int]:
    azure = config["azure"]
    gateway = config["gateway"]
    foundry = config["foundry"]
    return {
        **azure,
        "gateway_name": gateway["name"],
        "publisher_name": gateway["publisher_name"],
        "publisher_email": gateway["publisher_email"],
        "foundry_name": foundry["name"],
        "model_name": foundry["model_name"],
        "model_version": foundry["model_version"],
        "model_capacity": foundry["model_capacity"],
    }


def resource_ids(config: dict[str, Any]) -> dict[str, str]:
    azure = config["azure"]
    scope = (
        f"/subscriptions/{azure['subscription_id']}"
        f"/resourceGroups/{azure['resource_group_name']}"
    )
    name = config["gateway"]["name"]
    return {
        "gateway": f"{scope}/providers/Microsoft.ApiManagement/service/{name}",
        "connector": f"{scope}/providers/Microsoft.Web/connectorGateways/{name}",
        "foundry": (
            f"{scope}/providers/Microsoft.CognitiveServices/accounts/"
            f"{config['foundry']['name']}"
        ),
    }


def arm_get(resource_id: str, api_version: str) -> dict[str, Any]:
    return run_json([
        "az", "rest", "--method", "get",
        "--url", f"https://management.azure.com{resource_id}?api-version={api_version}",
        "--output", "json",
    ])


def require_dedicated_gateway(gateway: dict[str, Any], location: str) -> None:
    if gateway["sku"]["name"] != "AIGateway":
        raise ValueError("Expected the dedicated AIGateway SKU, not a conventional APIM tier.")
    if gateway["location"].replace(" ", "").lower() != location:
        raise ValueError("The live gateway region differs from the configured showcase region.")
    if gateway["properties"]["provisioningState"] != "Succeeded":
        raise ValueError("The gateway has not finished provisioning.")
    if gateway.get("identity", {}).get("type") != "SystemAssigned":
        raise ValueError("The dedicated gateway must have its system-assigned identity enabled.")


def status(config: dict[str, Any]) -> None:
    ids = resource_ids(config)
    gateway = arm_get(ids["gateway"], GATEWAY_API)
    require_dedicated_gateway(gateway, config["azure"]["location"])
    connector = arm_get(ids["connector"], CONNECTOR_API)
    foundry = arm_get(ids["foundry"], FOUNDRY_API)
    model = arm_get(f"{ids['foundry']}/deployments/showcase-chat", FOUNDRY_API)
    namespace = f"{ids['gateway']}/workspaces/default"
    published = arm_get(f"{namespace}/modelProviders/showcase-foundry/models/showcase-chat", GATEWAY_API)
    tools = arm_get(f"{namespace}/toolServers/microsoft-learn", GATEWAY_API)
    exporter = arm_get(f"{namespace}/telemetryExporters/showcase-monitor", GATEWAY_API)
    for label, resource in (("connector", connector), ("Foundry backend", foundry), ("model", model)):
        if resource["properties"]["provisioningState"] != "Succeeded":
            raise ValueError(f"The {label} has not finished provisioning.")
    if foundry["properties"]["disableLocalAuth"] is not True:
        raise ValueError("The gateway's Foundry backend must disable local-key authentication.")
    print(json.dumps({
        "gateway": gateway["name"],
        "sku": gateway["sku"],
        "region": gateway["location"],
        "url": gateway["properties"]["gatewayUrl"],
        "ownership_tags": gateway["tags"],
        "managed_identity": gateway["identity"]["principalId"],
        "connector_gateway": connector["name"],
        "foundry_backend": foundry["name"],
        "backend_local_auth_disabled": True,
        "model": model["properties"]["model"],
        "capacity": model["sku"],
        "backend_rate_limits": model["properties"]["rateLimits"],
        "model_policies": published["properties"]["policies"],
        "mcp_allowlist": tools["properties"]["allowList"],
        "mcp_blocked": tools["properties"]["blocked"],
        "telemetry": exporter["properties"],
        "agent_routing": "Existing showcase agents retain their shared Foundry project path.",
        "verification_scope": "Live configuration; run the no-argument demonstration for runtime evidence.",
    }, indent=2))


def require_non_destructive_plan(plan: dict[str, Any]) -> None:
    destructive = [
        change["address"]
        for change in plan.get("resource_changes", [])
        if "delete" in change["change"]["actions"]
    ]
    if destructive:
        raise ValueError(f"Refusing deletion or replacement: {', '.join(destructive)}")


def manage(config: dict[str, Any], action: str, approve: bool) -> None:
    env = os.environ.copy()
    env.update({f"TF_VAR_{key}": str(value) for key, value in terraform_variables(config).items()})
    run(["terraform", "init", "-input=false", "-lockfile=readonly", "-no-color"], cwd=TERRAFORM_DIR, env=env)
    run(["terraform", "validate", "-no-color"], cwd=TERRAFORM_DIR, env=env)
    if action == "adopt":
        ids = resource_ids(config)
        require_dedicated_gateway(arm_get(ids["gateway"], GATEWAY_API), config["azure"]["location"])
        arm_get(ids["connector"], CONNECTOR_API)
        for address, key, version in (
            ("azapi_resource.gateway", "gateway", GATEWAY_API),
            ("azapi_resource.connector_gateway", "connector", CONNECTOR_API),
        ):
            run([
                "terraform", "import", "-input=false", "-no-color",
                address, f"{ids[key]}?api-version={version}",
            ], cwd=TERRAFORM_DIR, env=env)
        return

    plan_path = TERRAFORM_DIR / "gateway.tfplan"
    run([
        "terraform", "plan", "-input=false", "-no-color", f"-out={plan_path}",
    ], cwd=TERRAFORM_DIR, env=env)
    plan = run_json(["terraform", "show", "-json", str(plan_path)], cwd=TERRAFORM_DIR)
    require_non_destructive_plan(plan)
    if action == "apply":
        if not approve:
            raise ValueError("Review the plan, then pass --approve to authorize its resource and RBAC changes.")
        run(["terraform", "apply", "-input=false", "-no-color", str(plan_path)], cwd=TERRAFORM_DIR, env=env)
        status(config)


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect or manage the separate dedicated AI Gateway showcase.")
    parser.add_argument("action", choices=("demo", "budget-demo", "telemetry", "status", "adopt", "plan", "apply"),
                        nargs="?", default="demo")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--approve", action="store_true")
    parser.add_argument("--trace-id", help="Filter telemetry logs and spans to one demonstration trace.")
    args = parser.parse_args()
    with args.config.open("rb") as source:
        config = tomllib.load(source)
    if args.trace_id and args.action != "telemetry":
        parser.error("--trace-id is only supported with telemetry.")
    if args.action in ("demo", "budget-demo", "telemetry"):
        if args.action == "budget-demo" and not args.approve:
            parser.error("budget-demo requires --approve to create a temporary key and temporarily override its budget.")
        gateway = Gateway(config["azure"]["subscription_id"], resource_ids(config)["gateway"])
        if args.action == "telemetry":
            result = telemetry(gateway, args.trace_id)
        elif args.action == "budget-demo":
            result = budget_demo(gateway)
        else:
            result = demo(gateway)
        print(json.dumps(result, indent=2))
    elif args.action == "status":
        status(config)
    else:
        manage(config, args.action, args.approve)


if __name__ == "__main__":
    main()
