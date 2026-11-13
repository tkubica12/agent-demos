from __future__ import annotations

import base64
import io
import json
import unittest
import urllib.error
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx

from scripts.native_transport_probe import (
    authenticated_port, connection_policy, direct_trigger, inspect_contracts, require_negative_auth,
)
from scripts.native_transport_probe_worker import run_lease_proof, settlement, validate_envelope


class NativeTransportProbeTests(unittest.TestCase):
    def setUp(self):
        self.outputs = {
            "worker_id": "worker", "tenant_id": "tenant", "sandbox_location": "swedencentral",
            "subscription_id": "subscription", "resource_group_name": "rg-owned",
            "sandbox_groups": {"gateway": {"name": "gateway", "identity_principal_id": "gateway-mi"}},
            "transport": {
                "connector_principal_id": "connector-mi",
                "connection_ids": {"gateway": "/connection"},
            },
        }

    def test_native_https_preserves_whole_trigger_body_without_shell_or_shared_key(self):
        result = direct_trigger(
            self.outputs, {"queue": "proof-queue", "connection_id": "/connections/proof"}, "sandbox", "https://proof",
        )
        notification = result["properties"]["notificationDetails"]
        self.assertEqual(notification["body"], "@triggerBody()")
        self.assertEqual(notification["callbackUrl"], "https://proof/callback")
        self.assertNotIn("executeShellCommand", json.dumps(result))
        self.assertNotIn("API_SERVER_KEY", json.dumps(result))
        port = authenticated_port(self.outputs)
        self.assertFalse(port["auth"]["anonymous"])
        self.assertEqual(port["auth"]["entraId"]["objectIds"], ["connector-mi"])
        self.assertEqual(port["auth"]["entraId"]["tenantIds"], ["tenant"])
        self.assertEqual(port["activationMode"], "OnDemand")
        self.assertEqual(connection_policy(self.outputs)["properties"]["principal"], {
            "type": "ActiveDirectory", "identity": {"objectId": "gateway-mi", "tenantId": "tenant"},
        })

    def test_inspection_never_claims_wake_or_settlement_proof(self):
        deployer = Mock(outputs=self.outputs, connector="/connector")
        deployer.arm.side_effect = [
            {"properties": {"createdBy": {"objectId": "gateway-mi"}}}, {"value": []}, {"value": []},
        ]
        result = inspect_contracts(deployer)
        self.assertFalse(result["e1_verified"])
        self.assertFalse(result["e2_verified"])
        self.assertFalse(result["azure_writes"])
        self.assertTrue(all(call.args[0] == "GET" for call in deployer.arm.call_args_list))

    def test_auth_controls_reject_redirect_or_unexpected_success(self):
        credential = Mock()
        credential.get_token.return_value = SimpleNamespace(token="not-printed")
        client = Mock()
        client.post.side_effect = [httpx.Response(401), httpx.Response(401), httpx.Response(403)]
        result = require_negative_auth(client, "https://proof", credential)
        self.assertEqual(result["foreign_principal"], 403)
        self.assertNotIn("Authorization", client.post.call_args_list[0].kwargs["headers"])
        client.post.side_effect = [httpx.Response(302)]
        with self.assertRaisesRegex(RuntimeError, "negative control"):
            require_negative_auth(client, "https://proof", credential)

    def test_probe_rejects_modified_body_or_missing_trigger_owned_lock(self):
        config = {"message_id": "proof-message"}
        envelope = {
            "MessageId": "proof-message", "LockToken": "opaque-lock",
            "ContentData": base64.b64encode(b'{"proof":"proof-message"}').decode(),
        }
        self.assertEqual(len(validate_envelope(envelope, config)), 64)
        with self.assertRaisesRegex(ValueError, "PeekLock"):
            validate_envelope({**envelope, "LockToken": ""}, config)
        with self.assertRaisesRegex(ValueError, "changed"):
            validate_envelope({**envelope, "ContentData": base64.b64encode(b'{"proof":"other"}').decode()}, config)

    def test_nested_connector_failure_is_not_mistaken_for_http_success(self):
        credential = Mock()
        credential.get_token.return_value = SimpleNamespace(token="not-printed")
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"response":{"statusCode":"Forbidden"}}'
        with patch("scripts.native_transport_probe_worker.urllib.request.urlopen", return_value=response):
            self.assertEqual(settlement(
                credential, {"connection_id": "/connection", "queue": "proof"}, {"LockToken": "lock"}, "renewlock",
            ), 403)

    def test_delegated_proof_renews_and_completes_same_trigger_lock_after_initial_lease(self):
        config = {"gateway_client_id": "client", "gateway_principal_id": "gateway-mi", "tenant_id": "tenant"}
        claims = base64.urlsafe_b64encode(b'{"oid":"gateway-mi","tid":"tenant"}').decode().rstrip("=")
        credential = Mock()
        credential.get_token.return_value = SimpleNamespace(token=f"header.{claims}.signature")
        credential.__enter__ = Mock(return_value=credential)
        credential.__exit__ = Mock(return_value=False)
        clock = [0.0]
        observations = []
        statuses = iter([200] * 20)
        envelope = {"LockToken": "the-trigger-owned-lease"}
        with (
            patch("scripts.native_transport_probe_worker.ManagedIdentityCredential", return_value=credential),
            patch("scripts.native_transport_probe_worker.time.monotonic", side_effect=lambda: clock[0]),
            patch("scripts.native_transport_probe_worker.time.sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)),
            patch("scripts.native_transport_probe_worker.settlement", side_effect=lambda *args: next(statuses)) as settle,
        ):
            run_lease_proof(config, envelope, lambda **values: observations.append(values))
        self.assertTrue(any(item.get("phase") == "renewing" for item in observations))
        self.assertEqual(observations[-1]["phase"], "completed")
        self.assertGreater(observations[-1]["elapsed_seconds"], 60)
        self.assertEqual(settle.call_args.args[-1], "complete")
        self.assertTrue(all(call.args[2] is envelope for call in settle.call_args_list))

    def test_owner_only_platform_error_remains_explicit(self):
        credential = Mock()
        credential.get_token.return_value = SimpleNamespace(token="not-printed")
        error = urllib.error.HTTPError(
            "https://management.azure.com/connection/dynamicInvoke", 502, "Bad Gateway", {},
            io.BytesIO(b'{"error":{"code":"ConnectorNamespaceConnectionOwnerAccessDenied","message":"Original owner only."}}'),
        )
        with patch("scripts.native_transport_probe_worker.urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "HTTP 502: ConnectorNamespaceConnectionOwnerAccessDenied"):
                settlement(
                    credential, {"connection_id": "/connection", "queue": "proof"},
                    {"LockToken": "same-trigger-lock"}, "renewlock",
                )


if __name__ == "__main__":
    unittest.main()
