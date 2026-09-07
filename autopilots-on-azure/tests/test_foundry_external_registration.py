import io
import json
import sys
import time
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from azure.ai.projects import AIProjectClient
from azure.core.credentials import AccessToken
from azure.core.exceptions import AzureError, HttpResponseError, ResourceNotFoundError
from azure.core.pipeline.transport import HttpResponse, HttpTransport

from scripts import register_foundry_external_agent as registration


class RegistrationResponse(HttpResponse):
    def __init__(self, request, status_code, data):
        super().__init__(request, None)
        self.status_code = status_code
        self.headers = {"Content-Type": "application/json"}
        self.content_type = "application/json"
        self.data = data

    def body(self):
        return json.dumps(self.data).encode()

    def json(self):
        return self.data


class RegistrationTransport(HttpTransport):
    def __init__(self):
        self.requests = []
        self.exists = False

    def open(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def send(self, request, **kwargs):
        self.requests.append(request)
        version = {"name": "hermes", "version": "1", "definition": {"kind": "external", "otel_agent_id": "hermes"}}
        if request.method == "POST":
            self.exists = True
            return RegistrationResponse(request, 200, version)
        if not self.exists:
            return RegistrationResponse(request, 404, {"error": {"code": "NotFound", "message": "Missing"}})
        return RegistrationResponse(request, 200, {"name": "hermes", "versions": {"latest": version}})


class RegistrationCredential:
    def get_token(self, *scopes, **kwargs):
        return AccessToken("unit-test-token", int(time.time()) + 3600)


def agent(kind="external", otel_agent_id="hermes"):
    return SimpleNamespace(
        name="hermes",
        versions=SimpleNamespace(
            latest=SimpleNamespace(
                definition=SimpleNamespace(kind=kind, otel_agent_id=otel_agent_id)
            )
        ),
    )


class ExternalRegistrationTests(unittest.TestCase):
    def test_cli_does_not_mask_missing_modules_or_programming_errors(self):
        for error in (
            ModuleNotFoundError("missing SDK", name="azure.ai.projects"),
            ModuleNotFoundError("unrelated module", name="unrelated_dependency"),
            RuntimeError("unexpected programming error"),
        ):
            with (
                self.subTest(error=type(error).__name__),
                patch.object(sys, "argv", ["register_foundry_external_agent"]),
                patch.object(registration, "register", side_effect=error),
                self.assertRaises(type(error)),
            ):
                registration.main()

    def test_cli_sanitizes_expected_operational_errors(self):
        for error in (AzureError("private-token"), ValueError("private-token"), OSError("private-token")):
            output = io.StringIO()
            with (
                self.subTest(error=type(error).__name__),
                patch.object(sys, "argv", ["register_foundry_external_agent"]),
                patch.object(registration, "register", side_effect=error),
                redirect_stdout(output),
            ):
                self.assertEqual(registration.main(), 1)
            self.assertNotIn("private-token", output.getvalue())
            self.assertEqual(json.loads(output.getvalue())["type"], type(error).__name__)

    def test_real_sdk_serializes_external_registration_with_preview_header(self):
        transport = RegistrationTransport()
        with AIProjectClient(
            endpoint="https://demo.services.ai.azure.com/api/projects/demo",
            credential=RegistrationCredential(),
            transport=transport,
            allow_preview=True,
        ) as client:
            created = registration.ensure_registration(
                client, agent_name="hermes", otel_agent_id="hermes", apply=True
            )
            unchanged = registration.ensure_registration(
                client, agent_name="hermes", otel_agent_id="hermes", apply=True
            )
        self.assertEqual(created["status"], "created")
        self.assertEqual(unchanged["status"], "unchanged")
        writes = [request for request in transport.requests if request.method == "POST"]
        self.assertEqual(len(writes), 1)
        self.assertIn("/agents/hermes/versions", writes[0].url)
        self.assertIn("ExternalAgents=V1Preview", writes[0].headers["Foundry-Features"])
        self.assertEqual(
            json.loads(writes[0].body)["definition"], {"kind": "external", "otel_agent_id": "hermes"}
        )

    def test_endpoint_requires_explicit_https_project_without_credentials(self):
        endpoint = "https://demo.services.ai.azure.com/api/projects/demo"
        self.assertEqual(registration.validate_project_endpoint(endpoint + "/"), endpoint)
        for invalid in (
            "http://demo.services.ai.azure.com/api/projects/demo",
            "https://other.example/api/projects/demo",
            "https://demo.services.ai.azure.com",
            endpoint + "?token=private",
            "https://user:secret@demo.services.ai.azure.com/api/projects/demo",
        ):
            with self.subTest(endpoint=invalid), self.assertRaises(ValueError):
                registration.validate_project_endpoint(invalid)

    def test_registration_is_idempotent_when_agent_identity_matches(self):
        client = MagicMock()
        client.agents.get.return_value = agent()
        result = registration.ensure_registration(
            client, agent_name="hermes", otel_agent_id="hermes", apply=True
        )
        self.assertEqual(result["status"], "unchanged")
        client.agents.create_version.assert_not_called()

    def test_read_only_default_does_not_create_registration(self):
        client = MagicMock()
        client.agents.get.side_effect = ResourceNotFoundError()
        result = registration.ensure_registration(client, agent_name="hermes", otel_agent_id="hermes")
        self.assertEqual(result["status"], "would-create")
        client.agents.create_version.assert_not_called()

    def test_existing_non_external_agent_is_never_overwritten(self):
        client = MagicMock()
        client.agents.get.return_value = agent(kind="hosted")
        with self.assertRaisesRegex(ValueError, "not external"):
            registration.ensure_registration(
                client, agent_name="hermes", otel_agent_id="hermes", apply=True
            )
        client.agents.create_version.assert_not_called()

    def test_auth_failure_is_not_interpreted_as_missing_registration(self):
        client = MagicMock()
        client.agents.get.side_effect = HttpResponseError("Denied")
        with self.assertRaises(HttpResponseError):
            registration.ensure_registration(client, agent_name="hermes", otel_agent_id="hermes", apply=True)
        client.agents.create_version.assert_not_called()

    def test_changed_id_creates_revision_and_requires_readback(self):
        client = MagicMock()
        client.agents.get.side_effect = [agent(otel_agent_id="old"), agent()]
        definition = SimpleNamespace(kind="external", otel_agent_id="hermes")
        with patch.object(registration, "external_definition", return_value=definition):
            result = registration.ensure_registration(
                client, agent_name="hermes", otel_agent_id="hermes", apply=True
            )
        self.assertEqual(result["status"], "updated")
        client.agents.create_version.assert_called_once_with(
            agent_name="hermes",
            definition=definition,
            description=registration.DESCRIPTION,
            metadata={"hosting": "aca-sandbox", "scenario": "autopilots-on-azure"},
        )
        self.assertEqual(client.agents.get.call_count, 2)

    def test_wrong_readback_is_not_success(self):
        for registered in (agent(kind="hosted"), agent(otel_agent_id="different")):
            client = MagicMock()
            client.agents.get.side_effect = [ResourceNotFoundError(), registered]
            with self.subTest(registered=registered), self.assertRaisesRegex(ValueError, "read-back"):
                registration.ensure_registration(
                    client, agent_name="hermes", otel_agent_id="hermes", apply=True,
                )

    def test_identity_rejects_empty_control_characters_and_whitespace(self):
        for identifier in ("", "private name", "a\nb", "x\x00y", "a" * 257):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                registration.ensure_registration(
                    MagicMock(), agent_name="hermes", otel_agent_id=identifier,
                )


if __name__ == "__main__":
    unittest.main()
