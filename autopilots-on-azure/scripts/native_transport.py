from __future__ import annotations

import argparse
import json
import shlex
import time
import uuid
from typing import Any
from urllib.parse import urlsplit

import httpx
from azure.containerapps.sandbox import SandboxGroupClient, endpoint_for_region
from azure.identity import AzureCliCredential

from scripts.setup_app_tfvars import runtime_outputs_path
from bridge.transport_ingress import CALLBACK_PATH


API = "2026-05-01-preview"
ROLE_API = "2022-04-01"
def expected_trigger(
    outputs: dict[str, Any], role: str, queue: str, sandbox_id: str,
    *, callback_url: str = "",
) -> dict[str, Any]:
    if role != "gateway":
        raise ValueError("Only the gateway receives managed scheduling callbacks.")
    group = outputs["sandbox_groups"][role]
    region = outputs["sandbox_location"]
    callback = callback_url or outputs["sandbox_services"]["gateway"]["callback_url"]
    parsed = urlsplit(callback)
    if parsed.scheme != "https" or not parsed.hostname or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError("The gateway callback requires its captured native HTTPS port URL.")
    return {
        "location": region,
        "properties": {
            "state": "Enabled",
            "description": "The gateway renews and settles each scheduled-work lease.",
            "connectionDetails": {
                "connectorName": "servicebus", "connectionName": f"{role}-servicebus-mi",
            },
            "operationName": "GetNewMessageFromQueueWithPeekLock",
            "parameters": [
                {"name": "queueName", "value": queue},
                {"name": "queueType", "value": "Main"},
                {"name": "sessionId", "value": "None"},
            ],
            "notificationDetails": {
                "callbackUrl": callback.rstrip("/") + CALLBACK_PATH, "httpMethod": "POST",
                "body": "@triggerBody()",
                "authentication": {
                    "type": "ManagedServiceIdentity",
                    "audience": "https://auth.adcproxy.io/",
                },
            },
            "metadata": {
                "sandboxGroupName": group["name"], "sandboxId": sandbox_id,
            },
        },
    }


def connection_body(namespace: str, location: str, role: str) -> dict[str, Any]:
    if role != "gateway":
        raise ValueError("Only the gateway owns a managed scheduling connection.")
    return {
        "location": location,
        "properties": {
            "displayName": f"Autopilots {role} caller-owned Service Bus",
            "connectorName": "servicebus",
            "parameterValueSet": {
                "name": "managedIdentityAuth",
                "values": {"namespaceEndpoint": {"value": f"sb://{namespace}/"}},
            },
            "connectionProperties": {"authentication": {"type": "ManagedServiceIdentity"}},
        },
    }


def properties_match(actual: dict[str, Any], desired: dict[str, Any]) -> bool:
    return all(actual.get(key) == value for key, value in desired.items())


def trigger_matches(actual: dict[str, Any], desired: dict[str, Any]) -> bool:
    if not properties_match(actual, {
        key: desired[key] for key in ("state", "connectionDetails", "operationName", "parameters")
    }):
        return False
    callback = actual.get("notificationDetails", {})
    expected = desired["notificationDetails"]
    if str(callback.get("httpMethod", "")).upper() != expected["httpMethod"].upper():
        return False
    if any(callback.get(key) != expected[key] for key in ("callbackUrl", "body")):
        return False
    if callback.get("authentication") is not None and callback["authentication"] != expected["authentication"]:
        return False
    if not properties_match(actual.get("metadata", {}), desired["metadata"]):
        return False
    return actual.get("provisioningState", "Succeeded") == "Succeeded"


class NativeTransportDeployer:
    def __init__(self, outputs: dict[str, Any]) -> None:
        self.outputs = outputs
        self.subscription = str(uuid.UUID(outputs["subscription_id"]))
        self.group_id = f"/subscriptions/{self.subscription}/resourceGroups/{outputs['resource_group_name']}"
        self.credential = AzureCliCredential(subscription=self.subscription, process_timeout=150)
        self.client = httpx.Client(timeout=90)
        self.connector = outputs["transport"]["connector_id"]
        if not self.connector.lower().startswith(
            (self.group_id + "/providers/Microsoft.Web/connectorGateways/").lower()
        ):
            raise ValueError("Native connector is outside the captured Worker's resource group.")

    def close(self) -> None:
        self.client.close()
        self.credential.close()

    def arm(self, method: str, resource: str, body: Any = None, *, api: str = API) -> dict[str, Any]:
        token = self.credential.get_token("https://management.azure.com/.default").token
        response = self.client.request(
            method, f"https://management.azure.com{resource}?api-version={api}",
            headers={"Authorization": f"Bearer {token}"}, json=body,
        )
        if method == "GET" and response.status_code == 404:
            return {}
        if method == "DELETE" and response.status_code == 404:
            return {}
        response.raise_for_status()
        return response.json() if response.content else {}

    def validate_ownership(self) -> None:
        group = self.arm("GET", self.group_id, api="2022-09-01")
        if group.get("tags", {}).get("app") != "autopilots-on-azure":
            raise ValueError("The captured resource group is not an owned Autopilots deployment.")
        for role in ("gateway", "runtime"):
            metadata = self.arm(
                "GET", self.outputs["sandbox_groups"][role]["id"], api="2026-02-01-preview",
            )
            tags = metadata.get("tags", {})
            if tags.get("autopilot") != self.outputs["worker_id"] or tags.get("role") != role:
                raise ValueError("The callback group does not belong to this Worker and role.")

    def create_as_owner(
        self, role: str, sandbox_id: str, resource: str, body: dict[str, Any],
    ) -> None:
        actor = self.outputs["sandbox_groups"][role]
        existing = self.arm("GET", resource)
        if existing:
            properties = existing.get("properties", {})
            if "/connections/" in resource:
                owner = {"objectId": actor["identity_principal_id"], "tenantId": self.outputs["tenant_id"]}
                if properties.get("createdBy") != owner:
                    raise ValueError("An existing native connection has a different immutable creator.")
            matches = properties_match if "/connections/" in resource else trigger_matches
            if matches(properties, body["properties"]):
                return
        definition_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, self.connector + "/bounded-bootstrap"))
        definition = self.group_id + "/providers/Microsoft.Authorization/roleDefinitions/" + definition_uuid
        role_definition = self.arm("PUT", definition, {"properties": {
            "roleName": f"autopilots-{self.outputs['worker_id']}-temporary-native-bootstrap",
            "type": "CustomRole", "assignableScopes": [self.group_id],
            "description": "Temporary caller-owned connection and trigger creation; removed after deployment.",
            "permissions": [{
                "actions": [
                    "Microsoft.Web/connectorGateways/connections/read",
                    "Microsoft.Web/connectorGateways/connections/write",
                    "Microsoft.Web/connectorGateways/triggerconfigs/read",
                    "Microsoft.Web/connectorGateways/triggerconfigs/write",
                ], "notActions": [], "dataActions": [], "notDataActions": [],
            }],
        }}, api=ROLE_API)
        assignment = self.connector + "/providers/Microsoft.Authorization/roleAssignments/" + str(
            uuid.uuid5(uuid.NAMESPACE_URL, resource + actor["identity_principal_id"] + "/bootstrap")
        )
        try:
            self.arm("PUT", assignment, {"properties": {
                "principalId": actor["identity_principal_id"], "principalType": "ServicePrincipal",
                "roleDefinitionId": role_definition["id"],
            }}, api=ROLE_API)
            script = """
import base64,json,urllib.request,urllib.error
from azure.identity import ManagedIdentityCredential
with ManagedIdentityCredential(client_id=CLIENT) as credential:
 token=credential.get_token('https://management.azure.com/.default').token
 part=token.split('.')[1]
 claims=json.loads(base64.urlsafe_b64decode(part+'='*(-len(part)%4)))
 if claims['oid']!=PRINCIPAL or claims['tid']!=TENANT:
  raise RuntimeError('Managed identity differs from the intended native resource creator')
 request=urllib.request.Request(URL,method='PUT',data=json.dumps(BODY).encode(),
  headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
 try:
  with urllib.request.urlopen(request,timeout=60) as response:
   code=response.status
   result=json.load(response)
 except urllib.error.HTTPError as error:
  code=error.code
  result=json.loads(error.read(4096))
 print(json.dumps({'status':code,'id':result.get('id'),'error':result.get('error')}))
""".strip()
            substitutions = {
                "CLIENT": actor["identity_client_id"], "PRINCIPAL": actor["identity_principal_id"],
                "TENANT": self.outputs["tenant_id"],
                "URL": f"https://management.azure.com{resource}?api-version={API}", "BODY": body,
            }
            for name, value in substitutions.items():
                script = script.replace(name, repr(value))
            release = self.outputs["sandbox_services"]["gateway"]["application_release"]
            python = f"/app/releases/{release}/.venv/bin/python"
            command = python + " -c " + shlex.quote(script)
            with SandboxGroupClient(
                endpoint_for_region(self.outputs["sandbox_location"]), self.credential,
                subscription_id=self.subscription, resource_group=self.outputs["resource_group_name"],
                sandbox_group=actor["name"], audience="https://management.azuredevcompute.io/.default",
            ) as group:
                sandbox = group.get_sandbox_client(sandbox_id)
                sandbox.ensure_running(timeout=600)
                for attempt in range(5):
                    result = sandbox.exec(command)
                    if result.exit_code != 0:
                        raise RuntimeError(f"Native owner bootstrap command failed: {result.stderr}")
                    response = json.loads(result.stdout)
                    if response["status"] in (200, 201):
                        break
                    if response["status"] != 403 or attempt == 4:
                        raise RuntimeError(f"Native resource creation failed: {response.get('error')}")
                    time.sleep(20)
            created = self.arm("GET", resource)
            matches = properties_match if "/connections/" in resource else trigger_matches
            if not matches(created.get("properties", {}), body["properties"]):
                raise RuntimeError("The native resource readback differs from its desired contract.")
            if "/connections/" in resource and created["properties"].get("createdBy") != {
                "objectId": actor["identity_principal_id"], "tenantId": self.outputs["tenant_id"],
            }:
                raise RuntimeError("The created native connection does not belong to its settling identity.")
        finally:
            self.arm("DELETE", assignment, api=ROLE_API)
            self.arm("DELETE", definition, api=ROLE_API)

    def deploy(self) -> dict[str, Any]:
        self.validate_ownership()
        transport = self.outputs["transport"]
        compute = {"gateway": self.outputs["sandbox_services"]["gateway"]["sandbox_id"]}
        for role in ("gateway",):
            self.create_as_owner(
                role, compute[role], transport["connection_ids"][role],
                connection_body(transport["namespace"], self.outputs["sandbox_location"], role),
            )
        schedule = self.outputs.get("scheduler_servicebus_queue_name")
        queues = {}
        if schedule:
            queues[schedule] = "gateway"
        triggers = {}
        for queue, role in queues.items():
            resource = self.connector + "/triggerConfigs/" + queue + "-wake"
            self.create_as_owner(role, compute[role], resource, expected_trigger(self.outputs, role, queue, compute[role]))
            triggers[queue] = resource
        obsolete_activity_queue = f"worker-{self.outputs['worker_id']}-activities"
        obsolete_activity_trigger = self.connector + "/triggerConfigs/" + obsolete_activity_queue + "-wake"
        self.arm("DELETE", obsolete_activity_trigger)
        return {
            "triggers": triggers, "connections": transport["connection_ids"],
            "removedTriggers": [obsolete_activity_trigger],
            "callback_execution_verified": False,
            "callback_authentication_readback": "write-only; verify with real authenticated callback execution",
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure caller-owned native Service Bus wake and settlement.")
    parser.add_argument("--state-name", default="hermes")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    path = runtime_outputs_path("hermes", args.state_name)
    outputs = json.loads(path.read_text(encoding="utf-8"))
    deployer = NativeTransportDeployer(outputs)
    try:
        if not args.apply:
            deployer.validate_ownership()
            print(json.dumps({"state": args.state_name, "native_transport": outputs["transport"], "applied": False}))
            return
        result = deployer.deploy()
        outputs["native_transport"] = result
        path.write_text(json.dumps(outputs, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2))
    finally:
        deployer.close()


if __name__ == "__main__":
    main()
