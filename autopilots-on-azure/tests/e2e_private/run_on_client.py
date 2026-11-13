"""Run a local script on an authorized private VM without exporting CLI credentials."""

import argparse
import datetime
import json
from pathlib import Path
import re
import time
import uuid


def execution_result(view):
    state = view.get("executionState")
    if state == "Succeeded":
        if view.get("exitCode") != 0:
            raise RuntimeError(f"Managed command reported Succeeded but exitCode={view.get('exitCode')}.")
        return view
    if state in {"Failed", "TimedOut", "Canceled"}:
        raise RuntimeError(f"Managed command execution failed: {state}; exitCode={view.get('exitCode')}.")
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vm-id", required=True)
    parser.add_argument("--location", default="swedencentral")
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    args = parser.parse_args()
    if not re.fullmatch(
        r"/subscriptions/[0-9a-f-]{36}/resourceGroups/[a-z0-9_.()-]+/"
        r"providers/Microsoft\.Compute/virtualMachines/[a-z0-9_.-]+", args.vm_id, re.I,
    ):
        raise ValueError("Provide an explicit VM ARM ID; no URL, query, or unrelated resource is accepted.")
    if not 1 <= args.timeout_seconds <= 7200:
        raise ValueError("timeout-seconds must be between 1 and 7200.")
    script = args.script.read_text(encoding="utf-8")
    if not script.strip():
        raise ValueError("The client script cannot be empty.")
    from azure.core import PipelineClient
    from azure.core.pipeline.policies import BearerTokenCredentialPolicy, RetryPolicy
    from azure.core.rest import HttpRequest
    from azure.identity import AzureCliCredential

    command_id = f"{args.vm_id}/runCommands/d-acceptance-{uuid.uuid4().hex}"
    url = f"https://management.azure.com{command_id}?api-version=2024-11-01"
    body = {
        "location": args.location,
        "tags": {"owner": "private-acceptance-runner"},
        "properties": {
            "asyncExecution": True, "source": {"script": script},
            "timeoutInSeconds": args.timeout_seconds,
        },
    }
    evidence = {"command_id": command_id, "at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        args.output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")

    with AzureCliCredential() as credential, PipelineClient(
        base_url="https://management.azure.com",
        policies=[
            RetryPolicy(),
            BearerTokenCredentialPolicy(credential, "https://management.azure.com/.default"),
        ],
    ) as client:
        def request(method, target, payload=None):
            response = client.send_request(HttpRequest(method, target, json=payload) if payload is not None
                                           else HttpRequest(method, target))
            if response.status_code not in {200, 201, 202, 204}:
                raise RuntimeError(f"Managed command ARM {method} failed HTTP {response.status_code}.")
            return response

        request("PUT", url, body)
        save()
        deadline = time.monotonic() + args.timeout_seconds + 180
        last_state = None
        while time.monotonic() < deadline:
            item = request("GET", url + "&$expand=instanceView").json()
            properties = item["properties"]
            view = properties.get("instanceView", {})
            evidence["resource_provisioning_state"] = properties.get("provisioningState")
            evidence["instance_view"] = view
            save()
            state = view.get("executionState")
            if state != last_state:
                print(f"Private client execution: {state}", flush=True)
                last_state = state
            if state in {"Succeeded", "Failed", "TimedOut", "Canceled"}:
                evidence["status"] = "fail"
                save()
                try:
                    execution_result(view)
                finally:
                    request("DELETE", url)
                    evidence["command_delete_accepted"] = True
                    save()
                evidence["status"] = "pass"
                save()
                print(f"Private client succeeded; evidence: {args.output}")
                return
            time.sleep(15)
        raise TimeoutError(f"Managed command still unverified; inspect {command_id}. Do not submit a duplicate.")


if __name__ == "__main__":
    main()
