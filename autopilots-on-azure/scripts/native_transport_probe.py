from __future__ import annotations

import argparse
import base64
import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from azure.containerapps.sandbox import SandboxGroupClient, endpoint_for_region

from scripts.application_releases import build_release, install_release
from scripts.native_transport import NativeTransportDeployer, expected_trigger
from scripts.sandbox_services import SandboxService, service_body
from scripts.setup_app_tfvars import runtime_outputs_path, runtime_workspace
from scripts.tf_helpers import REPO_ROOT, run


ADC_SCOPE = "https://auth.adcproxy.io/.default"
ADC_AUDIENCE = "https://auth.adcproxy.io/"
REMOTE = "/app/transport-proof"


def connection_policy(outputs: dict) -> dict:
    return {
        "location": outputs["sandbox_location"],
        "properties": {"principal": {
            "type": "ActiveDirectory",
            "identity": {
                "objectId": outputs["sandbox_groups"]["gateway"]["identity_principal_id"],
                "tenantId": outputs["tenant_id"],
            },
        }},
    }


def authenticated_port(outputs: dict) -> dict:
    return {
        "port": 8000, "protocol": "Http", "activationMode": "OnDemand",
        "auth": {"anonymous": False, "entraId": {
            "enabled": True,
            "tenantIds": [outputs["tenant_id"]],
            "objectIds": [outputs["transport"]["connector_principal_id"]],
        }},
    }


def direct_trigger(outputs: dict, proof: dict, sandbox_id: str, url: str) -> dict:
    result = expected_trigger(outputs, "gateway", proof["queue"], sandbox_id, callback_url=url)
    result["properties"]["connectionDetails"]["connectionName"] = proof["connection_id"].rsplit("/", 1)[1]
    result["properties"]["notificationDetails"] = {
        "callbackUrl": url.rstrip("/") + "/callback", "httpMethod": "POST",
        "body": "@triggerBody()",
        "authentication": {"type": "ManagedServiceIdentity", "audience": ADC_AUDIENCE},
    }
    return result


def prepare(outputs: dict, directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    source_directory = REPO_ROOT / "terraform" / "transport-proof"
    for source in [*source_directory.glob("*.tf"), source_directory / ".terraform.lock.hcl"]:
        shutil.copyfile(source, directory / source.name)
    platform = json.loads((REPO_ROOT / "terraform" / "platform" / "terraform.tfstate").read_text(encoding="utf-8"))
    namespace_id = platform["outputs"]["scheduler_servicebus_namespace_id"]["value"]
    worker = {
        "subscription_id": outputs["subscription_id"],
        "resource_group_id": (
            f"/subscriptions/{outputs['subscription_id']}/resourceGroups/{outputs['resource_group_name']}"
        ),
        "location": outputs["sandbox_location"], "worker_id": outputs["worker_id"],
        "connector_id": outputs["transport"]["connector_id"],
        "connector_principal_id": outputs["transport"]["connector_principal_id"],
        "gateway_principal_id": outputs["sandbox_groups"]["gateway"]["identity_principal_id"],
        "servicebus_namespace_id": namespace_id, "servicebus_namespace": outputs["transport"]["namespace"],
    }
    (directory / "worker.auto.tfvars.json").write_text(json.dumps({"worker": worker}, indent=2) + "\n")
    return {"prepared": str(directory), "azure_writes": False}


def inspect_contracts(deployer: NativeTransportDeployer) -> dict:
    deployer.validate_ownership()
    outputs = deployer.outputs
    connection = outputs["transport"]["connection_ids"]["gateway"]
    resource = deployer.arm("GET", connection)
    policies = deployer.arm("GET", connection + "/accessPolicies")
    triggers = deployer.arm("GET", deployer.connector + "/triggerConfigs")
    return {
        "azure_writes": False, "worker": outputs["worker_id"],
        "connection_creator": resource.get("properties", {}).get("createdBy"),
        "access_policy_count": len(policies.get("value", [])),
        "callbacks": [
            {"name": item["name"], "url": item.get("properties", {}).get("notificationDetails", {}).get("callbackUrl")}
            for item in triggers.get("value", [])
        ],
        "e1_verified": False, "e2_verified": False,
        "blocker": "Run the isolated, explicitly provisioned contract proof before replacing production transport.",
    }


def require_negative_auth(client: httpx.Client, url: str, credential) -> dict:
    results = {}
    for label, scope in (("anonymous", ""), ("wrong_audience", "https://management.azure.com/.default"),
                         ("foreign_principal", ADC_SCOPE)):
        headers = {"Authorization": f"Bearer {credential.get_token(scope).token}"} if scope else {}
        response = client.post(url + "/callback", headers=headers, json={}, follow_redirects=False)
        expected = (403,) if label == "foreign_principal" else (401, 403)
        if response.status_code not in expected:
            raise RuntimeError(f"The {label} negative control returned {response.status_code}, expected {expected}.")
        results[label] = response.status_code
    return results


def run_proof(deployer: NativeTransportDeployer, directory: Path) -> dict:
    outputs = deployer.outputs
    deployer.validate_ownership()
    proof = json.loads(run(["terraform", "output", "-json", "proof"], cwd=directory, capture=True).stdout)
    expected_name = f"worker-{outputs['worker_id']}-transport-proof"
    expected_connection = deployer.connector + "/connections/" + expected_name
    if proof["queue"] != expected_name or proof["connection_id"].lower() != expected_connection.lower():
        raise ValueError("Proof outputs are not the dedicated resources for the captured Worker.")
    queue = deployer.arm("GET", proof["queue_id"], api="2024-01-01")
    properties = queue.get("properties", {})
    if properties.get("lockDuration") != "PT1M" or properties.get("messageCount", 0):
        raise ValueError("The isolated proof queue must be empty with a one-minute lock duration.")
    creator = deployer.arm("GET", proof["connection_id"])["properties"]["createdBy"]
    gateway = outputs["sandbox_groups"]["gateway"]
    if creator["objectId"] == gateway["identity_principal_id"]:
        raise ValueError("E2 requires a connection whose creator is not the gateway.")
    caller_token = deployer.credential.get_token("https://management.azure.com/.default").token
    caller = json.loads(base64.urlsafe_b64decode(caller_token.split(".")[1] + "=="))
    if creator != {"objectId": caller["oid"], "tenantId": caller["tid"]}:
        raise ValueError("Run Terraform and this proof as the same connection creator.")
    if deployer.arm("GET", proof["connection_id"] + "/accessPolicies").get("value"):
        raise ValueError("The disposable connection must start without access policies.")
    if caller["oid"] == outputs["transport"]["connector_principal_id"]:
        raise ValueError("The foreign-principal negative control must use a different principal.")

    trigger_id = deployer.connector + "/triggerConfigs/" + expected_name
    policy_id = proof["connection_id"] + "/accessPolicies/gateway-proof"
    if deployer.arm("GET", trigger_id):
        raise ValueError("A previous proof trigger exists; inspect and remove it before rerunning.")
    message_id = "transport-proof-" + uuid.uuid4().hex
    sandbox = None
    trigger_created = False
    policy_created = False
    report: dict[str, Any] = {
        "e1_verified": False, "e2_verified": False,
        "rbac_removal_proven": False, "production_transport_changed": False,
        "connection_creator": creator, "message_id": message_id,
        "terraform_resource_ids": proof.get("resource_ids", []),
        "connection_id": proof["connection_id"], "queue_id": proof["queue_id"],
        "trigger_id": trigger_id,
    }
    try:
        with SandboxGroupClient(
            endpoint_for_region(outputs["sandbox_location"]), deployer.credential,
            subscription_id=outputs["subscription_id"], resource_group=outputs["resource_group_name"],
            sandbox_group=gateway["name"], audience="https://management.azuredevcompute.io/.default",
        ) as group:
            service = SandboxService("transport-proof", "proof", 8000, (), {})
            body = service_body(
                service, gateway, outputs["sandbox_services"]["gateway"]["disk_image_id"], outputs["worker_id"],
            )
            body["ports"] = [authenticated_port(outputs)]
            body["entrypoint"] = ["python3", "-c", (
                "import pathlib,time; p=pathlib.Path('/app/transport-proof/start.py'); "
                "exec(\"while not p.exists(): time.sleep(1)\"); exec(p.read_text())"
            )]
            created = group._dp_put(group._group_path + "/sandboxes", body)
            sandbox = group.get_sandbox_client(created["id"])
            report["sandbox_id"] = created["id"]
            (directory / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            sandbox.ensure_running(timeout=600)
            release = install_release(sandbox, build_release("gateway"))
            sandbox.write_file(REMOTE + "/worker.py", Path(__file__).with_name("native_transport_probe_worker.py").read_bytes())
            config = {
                **proof, "message_id": message_id, "gateway_client_id": gateway["identity_client_id"],
                "gateway_principal_id": gateway["identity_principal_id"], "tenant_id": outputs["tenant_id"],
            }
            sandbox.write_file(REMOTE + "/config.json", json.dumps(config), mode="0600")
            sandbox.write_file(REMOTE + "/start.py", (
                "import os\n"
                f"os.chdir({release!r})\n"
                f"os.execv({(release + '/.venv/bin/python')!r},"
                f"[{(release + '/.venv/bin/python')!r},{(REMOTE + '/worker.py')!r},{REMOTE!r}])\n"
            ))
            deadline = time.monotonic() + 90
            while True:
                try:
                    state = json.loads(sandbox.read_file(REMOTE + "/result.json"))
                    if state["phase"] == "ready":
                        break
                except Exception:
                    pass
                if time.monotonic() >= deadline:
                    raise TimeoutError("The isolated receiver did not start.")
                time.sleep(2)
            raw = group._dp_get(sandbox._sbx_path)
            port = next(item for item in raw["ports"] if item["port"] == 8000)
            policy = port.get("auth", {}).get("entraId", {})
            if (
                port.get("activationMode") != "OnDemand"
                or port.get("auth", {}).get("anonymous") is not False
                or policy.get("objectIds") != [outputs["transport"]["connector_principal_id"]]
                or policy.get("tenantIds") != [outputs["tenant_id"]]
            ):
                raise RuntimeError("The native port readback lost its authenticated OnDemand policy.")
            url = port["url"].rstrip("/")
            with httpx.Client(timeout=45) as client:
                report["auth_rejection"] = require_negative_auth(client, url, deployer.credential)
            policy_created = True
            deployer.arm("PUT", policy_id, connection_policy(outputs))
            deadline = time.monotonic() + 120
            while True:
                provisioned = deployer.arm("GET", policy_id).get("properties", {})
                if provisioned.get("provisioningState") == "Succeeded":
                    break
                if provisioned.get("provisioningState") == "Failed" or time.monotonic() >= deadline:
                    raise RuntimeError("The gateway connection access policy did not provision successfully.")
                time.sleep(3)
            if provisioned.get("principal") != connection_policy(outputs)["properties"]["principal"]:
                raise RuntimeError("The access policy did not retain the noncreator gateway principal.")
            report["access_policy_id"] = policy_id
            trigger_created = True
            deployer.arm("PUT", trigger_id, direct_trigger(outputs, proof, sandbox.sandbox_id, url))
            deadline = time.monotonic() + 600
            while sandbox.get().state not in {"Stopped", "Suspended"}:
                if time.monotonic() >= deadline:
                    raise TimeoutError("The isolated receiver never suspended; OnDemand wake is not proven.")
                time.sleep(10)
            report["state_before_trigger"] = sandbox.get().state
            send = deployer.arm("POST", proof["connection_id"] + "/dynamicInvoke", {"request": {
                "method": "POST", "path": f"/{proof['queue']}/messages",
                "body": {"ContentData": base64.b64encode(json.dumps({"proof": message_id}).encode()).decode(),
                         "ContentType": "application/json", "MessageId": message_id},
            }})
            if send.get("response", {}).get("statusCode") not in (200, 201, 202, 204, "OK", "Accepted", "Created", "NoContent"):
                raise RuntimeError("The connector did not accept the isolated proof message.")
            deadline = time.monotonic() + 120
            while sandbox.get().state != "Running":
                if time.monotonic() >= deadline:
                    raise TimeoutError("A real trigger did not wake the suspended receiver.")
                time.sleep(1)
            report["state_after_trigger"] = "Running"
            deadline = time.monotonic() + 150
            while True:
                state = json.loads(sandbox.read_file(REMOTE + "/result.json"))
                report["settlement"] = state
                if state.get("envelope_sha256"):
                    report["e1_verified"] = True
                if state["phase"] == "completed":
                    policy = deployer.arm("GET", policy_id).get("properties", {})
                    if policy.get("principal") != connection_policy(outputs)["properties"]["principal"]:
                        raise RuntimeError("The delegated connection policy readback differs from the gateway principal.")
                    if policy.get("provisioningState") != "Succeeded":
                        raise RuntimeError("The delegated connection policy has not successfully provisioned.")
                    report.update(e2_verified=True, settlement=state)
                    break
                if state["phase"] == "failed":
                    raise RuntimeError(f"The isolated lease proof failed: {state.get('error')}")
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"The isolated lease proof remained {state['phase']}.")
                time.sleep(2)
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        cleanup_errors = []
        for resource, owned in ((trigger_id, trigger_created), (policy_id, policy_created)):
            if owned:
                try:
                    deployer.arm("DELETE", resource)
                except Exception as error:
                    cleanup_errors.append({"resource": resource, "error_type": type(error).__name__})
        if sandbox is not None:
            try:
                with SandboxGroupClient(
                    endpoint_for_region(outputs["sandbox_location"]), deployer.credential,
                    subscription_id=outputs["subscription_id"], resource_group=outputs["resource_group_name"],
                    sandbox_group=gateway["name"], audience="https://management.azuredevcompute.io/.default",
                ) as cleanup_group:
                    cleanup_group.get_sandbox_client(sandbox.sandbox_id).begin_delete(polling_timeout=600).result()
            except Exception as error:
                cleanup_errors.append({"resource": sandbox.sandbox_id, "error_type": type(error).__name__})
        report["cleanup_errors"] = cleanup_errors
        report["terraform_cleanup_required"] = True
        (directory / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        (directory / (message_id + ".json")).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if cleanup_errors:
            raise RuntimeError("Proof cleanup failed; inspect result.json and remove only its recorded resources.")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only E1/E2 inspection; opt in to an isolated real contract proof.")
    parser.add_argument("--state-name", default="hermes")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--prepare", action="store_true", help="Prepare local Terraform only; no Azure writes.")
    actions.add_argument("--apply", action="store_true", help="Run billable isolated compute after the proof Terraform apply.")
    args = parser.parse_args()
    outputs = json.loads(runtime_outputs_path("hermes", args.state_name).read_text(encoding="utf-8"))
    directory = runtime_workspace("hermes", args.state_name).parent / "transport-proof"
    if args.prepare:
        print(json.dumps(prepare(outputs, directory), indent=2))
        return
    deployer = NativeTransportDeployer(outputs)
    try:
        print(json.dumps(run_proof(deployer, directory) if args.apply else inspect_contracts(deployer), indent=2))
    finally:
        deployer.close()


if __name__ == "__main__":
    main()
