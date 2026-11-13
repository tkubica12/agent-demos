"""Probe the native v2 disk conversion path without registry credentials."""

import argparse
import json
import time
from pathlib import Path

from azure.containerapps.sandbox import SandboxGroupClient, endpoint_for_region
from azure.core.rest import HttpRequest
from azure.identity import AzureCliCredential


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subscription", required=True)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--group", required=True)
    parser.add_argument("--region", default="swedencentral")
    parser.add_argument("--image", required=True)
    parser.add_argument("--identity-client-id", required=True)
    parser.add_argument("--vnet-connection")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    request = {
        "source": {
            "kind": "registry",
            "imageUrl": args.image,
            "managedIdentityClientId": args.identity_client_id,
        },
        "labels": {"name": f"private-probe-{int(time.time())}"},
    }
    if args.vnet_connection:
        request["customerVnetConnectionName"] = args.vnet_connection
    with AzureCliCredential() as credential, SandboxGroupClient(
        endpoint_for_region(args.region),
        credential,
        subscription_id=args.subscription,
        resource_group=args.resource_group,
        sandbox_group=args.group,
    ) as client:
        base = f"{client._endpoint}{client._group_path}"
        submitted = client._send_request(
            HttpRequest("PUT", f"{base}/diskimages/v2/async", json=request)
        ).json()
        evidence = {"request": request, "submitted": submitted, "polls": []}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        operation = submitted["operationId"]
        for _ in range(180):
            result = client._send_request(
                HttpRequest("GET", f"{base}/diskimages/operations/{operation}")
            ).json()
            evidence["polls"].append(result)
            args.output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
            if result["status"] in {"Succeeded", "Failed"}:
                print(json.dumps(result, indent=2))
                if result["status"] != "Succeeded":
                    raise SystemExit(1)
                return
            time.sleep(5)
        raise TimeoutError("Conversion still pending; inspect recorded operation before retrying.")


if __name__ == "__main__":
    main()
