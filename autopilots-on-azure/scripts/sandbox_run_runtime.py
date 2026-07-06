from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from scripts.sandbox_runtime import ensure_agent_sandbox, config_from_environment


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", choices=["openclaw", "hermes"], default="openclaw")
    parser.add_argument("--dry-run", action="store_true", help="Print the resolved sandbox config without creating or starting a sandbox.")
    parser.add_argument("--image", help="ACR image, e.g. registry.azurecr.io/openclaw-runtime:latest")
    parser.add_argument("--registry-username", default="")
    parser.add_argument("--registry-password", default="")
    parser.add_argument("--disk-image-id", default="", help="Existing ACA Sandbox disk image id to run.")
    parser.add_argument("--foundry-openai-base-url", default="")
    parser.add_argument("--model-deployment", default="")
    parser.add_argument("--gateway-token", default="")
    parser.add_argument("--disk-image-name", default="")
    parser.add_argument("--data-volume-name", default="")
    parser.add_argument("--data-volume-size", default="20Gi")
    parser.add_argument("--cpu", default="2000m")
    parser.add_argument("--memory", default="2048Mi")
    parser.add_argument("--root-disk-size", default="20Gi")
    parser.add_argument("--customer-vnet-connection-name", default="")
    parser.add_argument("--private-incidents-mcp-url", default="")
    parser.add_argument("--private-incidents-mcp-static-key", default="")
    parser.add_argument("--api-server-key", default="", help="Hermes API_SERVER_KEY for dry-run/runtime config.")
    args = parser.parse_args()

    config = config_from_environment(
        subscription_id="dry-run" if args.dry_run else "",
        runtime_kind=args.runtime,
        image_name=args.image,
        registry_username=args.registry_username,
        registry_password=args.registry_password,
        disk_image_id=args.disk_image_id,
        foundry_openai_base_url=args.foundry_openai_base_url,
        model_deployment=args.model_deployment,
        gateway_token=args.gateway_token,
        disk_image_name=args.disk_image_name,
        data_volume_name=args.data_volume_name,
        data_volume_size=args.data_volume_size,
        cpu=args.cpu,
        memory=args.memory,
        root_disk_size=args.root_disk_size,
        customer_vnet_connection_name=args.customer_vnet_connection_name,
        private_incidents_mcp_url=args.private_incidents_mcp_url,
        private_incidents_mcp_static_key=args.private_incidents_mcp_static_key,
        api_server_key=args.api_server_key,
    )
    if args.dry_run:
        print(json.dumps(asdict(config), indent=2))
        return

    if config.disk_image_id:
        print(f"Using provided disk image id {config.disk_image_id}", flush=True)
    elif config.image_name:
        print(f"Ensuring sandbox disk image {config.disk_image_name} from {config.image_name}", flush=True)

    result = ensure_agent_sandbox(config)
    print(json.dumps(asdict(result), indent=2))


if __name__ == "__main__":
    main()
