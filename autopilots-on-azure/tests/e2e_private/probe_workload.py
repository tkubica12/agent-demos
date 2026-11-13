"""Run real Entra inference and authenticated telemetry from a Sandbox guest."""

import argparse
import datetime
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
import uuid


def token(resource, client_id):
    query = urllib.parse.urlencode({
        "resource": resource, "client_id": client_id, "api-version": "2019-08-01",
    })
    endpoint = os.environ["IDENTITY_ENDPOINT"]
    request = urllib.request.Request(
        endpoint + ("&" if "?" in endpoint else "?") + query,
        headers={"X-IDENTITY-HEADER": os.environ["IDENTITY_HEADER"]},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)["access_token"]


def post(url, body, access_token):
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Data plane HTTP {error.code}: {error.read().decode()}") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--foundry-host", required=True)
    parser.add_argument("--deployment", default="probe")
    parser.add_argument("--instrumentation-key")
    parser.add_argument("--ingestion-endpoint")
    args = parser.parse_args()
    nonce = uuid.uuid4().hex
    result = {
        "nonce": nonce,
        "foundry_address": socket.gethostbyname(args.foundry_host),
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    response = post(
        f"https://{args.foundry_host}/openai/deployments/{args.deployment}/chat/completions?api-version=2024-10-21",
        {"messages": [{"role": "user", "content": f"Reply with exactly this code: {nonce}"}], "max_tokens": 64},
        token("https://cognitiveservices.azure.com", args.client_id),
    )
    if nonce not in response["choices"][0]["message"]["content"]:
        raise AssertionError("Real inference did not echo the unique challenge.")
    result["inference"] = {
        "id": response["id"], "model": response["model"], "usage": response["usage"], "challenge_matched": True,
    }
    print(json.dumps(result, indent=2), flush=True)
    if bool(args.instrumentation_key) != bool(args.ingestion_endpoint):
        raise ValueError("Provide both telemetry arguments, or neither.")
    if args.ingestion_endpoint:
        result["monitor_address"] = socket.gethostbyname(urllib.parse.urlparse(args.ingestion_endpoint).hostname)
        accepted = post(
            args.ingestion_endpoint.rstrip("/") + "/v2.1/track",
            [{
                "name": "Microsoft.ApplicationInsights.Event",
                "time": result["at"], "iKey": args.instrumentation_key,
                "tags": {"ai.cloud.role": "private-network-probe"},
                "data": {"baseType": "EventData", "baseData": {
                    "ver": 2, "name": "private-acceptance-probe", "properties": {"nonce": nonce},
                }},
            }],
            token("https://monitor.azure.com", args.client_id),
        )
        if accepted.get("itemsAccepted") != 1 or accepted.get("errors"):
            raise AssertionError(f"Telemetry was not fully accepted: {accepted}")
        result["telemetry_ingestion"] = accepted
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
