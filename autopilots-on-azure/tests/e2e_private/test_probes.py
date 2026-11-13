import io
import json
import shlex
import socket
import ssl
import tempfile
from types import SimpleNamespace
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch
from zipfile import ZipFile

from fixtures import create_fixtures
from network_probe import check, private_address, public_control
from restart_sandbox import restart
from run_on_client import confirm_deleted, execution_result
from verify_download import verify_download
from verify_native_evidence import foundry_trace, query_failure_metadata, query_trace_in_sandbox, select_delivery, select_widget_turn, validate_run
from verify_widget_attestation import EXPECTED, validate_attestation


class PrivateProbeTests(unittest.TestCase):
    def test_only_rfc1918_is_private(self):
        for value in ("10.1.2.3", "172.20.1.2", "192.168.3.4"):
            self.assertTrue(private_address(value))
        for value in ("127.0.0.1", "169.254.169.254", "192.0.2.3", "100.65.0.1", "20.1.2.3"):
            self.assertFalse(private_address(value))

    @patch("network_probe.socket.getaddrinfo", return_value=[(socket.AF_INET, 1, 6, "", ("20.1.2.3", 443))])
    @patch("network_probe.socket.create_connection")
    @patch("network_probe.ssl.create_default_context")
    @patch("network_probe.request")
    def test_temporary_public_is_verified_but_not_private_certification(self, request, context, connection, _):
        tls = context.return_value.wrap_socket.return_value.__enter__.return_value
        tls.getpeername.return_value = ("20.1.2.3", 443)
        tls.cipher.return_value = ("trusted", "TLSv1.3", 256)
        tls.version.return_value = "TLSv1.3"
        response = io.BytesIO(b'{"status":"ok"}')
        response.status = 200
        request.return_value = response
        result = check("https://simulator.example", temporary_public=True)
        self.assertEqual(result["scope"], "temporary-public-readiness")
        self.assertTrue(result["tls_verified"])
        self.assertFalse(result["public_negative"])
        context.return_value.wrap_socket.assert_called_once_with(
            connection.return_value.__enter__.return_value, server_hostname="simulator.example",
        )
        with self.assertRaisesRegex(AssertionError, "RFC1918"):
            check("https://simulator.example")
        with self.assertRaisesRegex(ValueError, "separate phases"):
            check("https://simulator.example", public=True, temporary_public=True)

    @patch("network_probe.socket.getaddrinfo", return_value=[
        (socket.AF_INET, 1, 6, "", ("20.1.2.3", 443)), (socket.AF_INET, 1, 6, "", ("10.1.2.3", 443)),
    ])
    def test_temporary_public_rejects_mixed_private_dns(self, _):
        with self.assertRaisesRegex(AssertionError, "global"):
            check("https://simulator.example", temporary_public=True)

    @patch("network_probe.socket.getaddrinfo", return_value=[(socket.AF_INET, 1, 6, "", ("20.1.2.3", 443))])
    @patch("network_probe.socket.create_connection")
    @patch("network_probe.ssl.create_default_context")
    def test_temporary_public_never_ignores_bad_certificates(self, context, _, __):
        context.return_value.wrap_socket.side_effect = ssl.SSLCertVerificationError("untrusted")
        with self.assertRaises(ssl.SSLCertVerificationError):
            check("https://simulator.example", temporary_public=True)

    @patch("azure.containerapps.sandbox.SandboxGroupClient")
    @patch("azure.identity.AzureCliCredential")
    @patch("verify_native_evidence.uuid.uuid4", return_value=SimpleNamespace(hex="e" * 32))
    def test_private_query_sandbox_uses_own_identity_and_preserves_pending_evidence(self, _, __, group):
        sandbox = group.return_value.__enter__.return_value.get_sandbox_client.return_value.__enter__.return_value
        sandbox.exec.return_value = SimpleNamespace(exit_code=0, stdout=json.dumps({
            "queryNetwork": {"scope": "private-dns-tls", "status": "pass"}, "observed": None,
            "completionNonce": "e" * 32,
        }))
        configuration = {
            "subscription_id": "10000000-0000-0000-0000-000000000001",
            "resource_group": "test", "group": "test-query",
            "sandbox_id": "10000000-0000-0000-0000-000000000002",
        }
        with patch.dict("os.environ", {"PRIVATE_AGENT_2_API_KEY": "never-export-this-key"}):
            result = query_trace_in_sandbox(configuration, "10000000-0000-0000-0000-000000000003", "a" * 32)
        command = shlex.split(sandbox.exec.call_args.args[0])
        self.assertEqual(command[:2], ["/app/.venv/bin/python", "-c"])
        compile(command[2], "private-query-helper", "exec")
        self.assertNotIn("never-export-this-key", command[2])
        self.assertIn("ManagedIdentityCredential(client_id=os.environ.get('AZURE_CLIENT_ID'))", command[2])
        self.assertIsNone(result["observed"])
        self.assertFalse(result["queryExecution"]["operatorCredentialsExported"])
        self.assertTrue(result["queryExecution"]["completionVerified"])
        sandbox.exec.return_value = SimpleNamespace(exit_code=3, stdout="", stderr="secret-error-body")
        with self.assertRaisesRegex(RuntimeError, "exit 3"):
            query_trace_in_sandbox(configuration, "10000000-0000-0000-0000-000000000003", "a" * 32)

    @patch("azure.containerapps.sandbox.SandboxGroupClient")
    @patch("azure.identity.AzureCliCredential")
    @patch("verify_native_evidence.uuid.uuid4", return_value=SimpleNamespace(hex="e" * 32))
    def test_query_requires_correlated_completion_not_sdk_default_exit_zero(self, _, __, group):
        sandbox = group.return_value.__enter__.return_value.get_sandbox_client.return_value.__enter__.return_value
        configuration = {
            "subscription_id": "10000000-0000-0000-0000-000000000001",
            "resource_group": "test", "group": "test-query",
            "sandbox_id": "10000000-0000-0000-0000-000000000002",
        }
        response = {"queryNetwork": {"scope": "private-dns-tls", "status": "pass"}, "observed": None}
        for payload in (response, {**response, "completionNonce": "f" * 32}, []):
            with self.subTest(payload=payload):
                sandbox.exec.return_value = SimpleNamespace(exit_code=0, stdout=json.dumps(payload))
                with self.assertRaisesRegex(ValueError, "this invocation"):
                    query_trace_in_sandbox(configuration, "10000000-0000-0000-0000-000000000003", "a" * 32)
        for exit_code in (None, False):
            with self.subTest(exit_code=exit_code):
                sandbox.exec.return_value = SimpleNamespace(
                    exit_code=exit_code, stdout=json.dumps({**response, "completionNonce": "e" * 32}),
                )
                with self.assertRaisesRegex(RuntimeError, "execution failed"):
                    query_trace_in_sandbox(configuration, "10000000-0000-0000-0000-000000000003", "a" * 32)

    def test_query_failure_metadata_excludes_messages_and_response_bodies(self):
        cause = urllib.error.HTTPError("https://private.example", 403, "never-export-message", {}, None)
        error = RuntimeError("never-export-token")
        error.__context__ = cause
        result = query_failure_metadata(error, "private-query")
        self.assertEqual(result["httpStatus"], 403)
        self.assertEqual(result["causes"], ["RuntimeError", "HTTPError"])
        self.assertNotIn("never-export", json.dumps(result))
        self.assertNotIn("private.example", json.dumps(result))

    @patch("azure.containerapps.sandbox.SandboxGroupClient")
    @patch("azure.identity.AzureCliCredential")
    @patch("verify_native_evidence.uuid.uuid4", return_value=SimpleNamespace(hex="e" * 32))
    def test_correlated_query_failure_cannot_pass_even_with_sdk_exit_zero(self, _, __, group):
        sandbox = group.return_value.__enter__.return_value.get_sandbox_client.return_value.__enter__.return_value
        configuration = {
            "subscription_id": "10000000-0000-0000-0000-000000000001",
            "resource_group": "test", "group": "test-query",
            "sandbox_id": "10000000-0000-0000-0000-000000000002",
        }
        for exit_code in (0, 1):
            with self.subTest(exit_code=exit_code):
                sandbox.exec.return_value = SimpleNamespace(exit_code=exit_code, stdout=json.dumps({
                    "completionNonce": "e" * 32,
                    "failure": {"stage": "private-query", "errorType": "HTTPError", "httpStatus": 403},
                }), stderr="never-export-secret")
                with self.assertRaisesRegex(RuntimeError, "stage=private-query; class=HTTPError; HTTP=403") as failure:
                    query_trace_in_sandbox(configuration, "10000000-0000-0000-0000-000000000003", "a" * 32)
                self.assertNotIn("never-export-secret", str(failure.exception))

    @patch("network_probe.socket.getaddrinfo", return_value=[(socket.AF_INET, 1, 6, "", ("20.1.2.3", 443))])
    @patch("network_probe.request")
    def test_public_reachable_health_is_failure(self, request, _):
        response = io.BytesIO(json.dumps({"status": "ok"}).encode())
        response.status = 200
        request.return_value = response
        with self.assertRaisesRegex(AssertionError, "reachable"):
            check("https://simulator.example", public=True)

    @patch("network_probe.socket.getaddrinfo", return_value=[(socket.AF_INET, 1, 6, "", ("10.1.2.3", 443))])
    def test_public_negative_cannot_run_in_private_dns(self, _):
        with self.assertRaisesRegex(AssertionError, "outside"):
            check("https://simulator.example", public=True)

    @patch("network_probe.socket.getaddrinfo", return_value=[(socket.AF_INET, 1, 6, "", ("20.1.2.3", 443))])
    @patch("network_probe.request", side_effect=urllib.error.URLError(ConnectionResetError()))
    def test_public_tls_reset_is_recorded(self, _, __):
        result = check("https://simulator.example", public=True)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["transport_error"], "ConnectionResetError")

    @patch("network_probe.socket.getaddrinfo", return_value=[(socket.AF_INET, 1, 6, "", ("20.1.2.3", 443))])
    @patch("network_probe.request",
           side_effect=urllib.error.URLError(ssl.SSLCertVerificationError("expired")))
    def test_bad_certificate_is_not_proof_of_private_ingress(self, _, __):
        with self.assertRaises(urllib.error.URLError):
            check("https://simulator.example", public=True)

    def test_docx_contains_real_document_part_and_escaped_challenge(self):
        with tempfile.TemporaryDirectory() as directory:
            codes = create_fixtures(Path(directory), "code<&>")
            self.assertNotEqual(codes["acceptance.txt"], codes["acceptance.docx"])
            self.assertIn("code<&>", (Path(directory) / "acceptance.txt").read_text())
            with ZipFile(Path(directory) / "acceptance.docx") as docx:
                self.assertIn("word/document.xml", docx.namelist())
                self.assertIn(b"code&lt;&amp;&gt;", docx.read("word/document.xml"))
                self.assertNotIn(codes["acceptance.txt"].encode(), docx.read("word/document.xml"))

    @patch("network_probe.request", side_effect=urllib.error.URLError(ConnectionResetError()))
    def test_offline_control_is_not_a_successful_public_negative(self, _):
        with self.assertRaises(urllib.error.URLError):
            public_control("https://control.example")

    def test_origin_with_path_is_not_accepted(self):
        with self.assertRaises(ValueError):
            check("https://simulator.example/not-health")

    def test_real_restart_requires_changed_boot_identity(self):
        sandbox = Mock()
        sandbox.exec.side_effect = [
            SimpleNamespace(exit_code=0, stdout=value, stderr="")
            for value in ("10000000-0000-0000-0000-000000000001", "10000000-0000-0000-0000-000000000002")
        ]
        evidence = restart(sandbox, 1)
        self.assertTrue(evidence["verifiedReboot"])
        sandbox.begin_stop.assert_called_once()
        sandbox.begin_resume.assert_called_once()

    def test_resume_of_unchanged_guest_is_not_a_verified_restart(self):
        sandbox = Mock()
        sandbox.exec.return_value = SimpleNamespace(
            exit_code=0, stdout="10000000-0000-0000-0000-000000000001", stderr="",
        )
        with self.assertRaisesRegex(RuntimeError, "not verified"):
            restart(sandbox, 1)

    def test_pending_exit_zero_is_not_managed_execution_success(self):
        self.assertIsNone(execution_result({"executionState": "Pending", "exitCode": 0}))

    def test_execution_success_does_not_depend_on_stale_arm_creation_state(self):
        view = {"executionState": "Succeeded", "exitCode": 0, "output": "verified"}
        self.assertEqual(execution_result(view), view)
        with self.assertRaises(RuntimeError):
            execution_result({"executionState": "Succeeded", "exitCode": 1})

    @patch("network_probe.socket.getaddrinfo", return_value=[(socket.AF_INET, 1, 6, "", ("10.1.2.3", 443))])
    @patch("network_probe.request", side_effect=urllib.error.HTTPError("https://simulator.example", 503, "", {}, None))
    def test_private_service_503_cannot_be_a_readiness_pass(self, _, __):
        with self.assertRaises(urllib.error.HTTPError):
            check("https://simulator.example")

    def test_private_transport_scope_cannot_be_used_as_public_negative(self):
        with self.assertRaises(ValueError):
            check("https://simulator.example", public=True, transport_only=True)

    def test_download_validator_reads_word_text_and_utf8_txt(self):
        with tempfile.TemporaryDirectory() as directory:
            codes = create_fixtures(Path(directory), "fresh")
            for name, nonce in codes.items():
                with self.subTest(name=name):
                    result = verify_download(Path(directory) / name, nonce)
                    self.assertGreater(result["size"], 0)
                    self.assertEqual(len(result["sha256"]), 64)
                    with self.assertRaisesRegex(ValueError, "absent"):
                        verify_download(Path(directory) / name, "different-turn-nonce")

    def test_zip_comment_nonce_does_not_certify_word_document_content(self):
        with tempfile.TemporaryDirectory() as directory:
            codes = create_fixtures(Path(directory), "actual-content")
            file = Path(directory) / "acceptance.docx"
            with ZipFile(file, "a") as archive:
                archive.comment = b"comment-only-nonce"
            self.assertEqual(verify_download(file, codes[file.name])["kind"], "docx")
            with self.assertRaisesRegex(ValueError, "absent"):
                verify_download(file, "comment-only-nonce")

    def test_download_validator_rejects_non_utf8_txt(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "invalid.txt"
            file.write_bytes(b"\xfffresh")
            with self.assertRaises(UnicodeDecodeError):
                verify_download(file, "fresh")

    def test_download_validator_rejects_empty_nonce_and_empty_file(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "empty.txt"
            file.write_bytes(b"")
            with self.assertRaisesRegex(ValueError, "nonempty verification"):
                verify_download(file, "")
            with self.assertRaisesRegex(ValueError, "nonempty and at most"):
                verify_download(file, "fresh")

    def test_word_xml_alone_is_not_a_real_docx_package(self):
        with tempfile.TemporaryDirectory() as directory:
            codes = create_fixtures(Path(directory), "fresh")
            with ZipFile(Path(directory) / "acceptance.docx") as source:
                xml = source.read("word/document.xml")
            file = Path(directory) / "incomplete.docx"
            with ZipFile(file, "w") as archive:
                archive.writestr("word/document.xml", xml)
            with self.assertRaisesRegex(ValueError, "Word package"):
                verify_download(file, codes["acceptance.docx"])

    def test_native_delivery_requires_exact_activity_pair_and_browser_bytes(self):
        expected = {
            "conversationId": "conversation", "requestActivityId": "request", "replyActivityId": "reply",
            "filename": "result.txt", "sha256": "a" * 64, "size": 10, "requestedAt": 1000,
        }
        event = {
            **expected, "name": "result.txt", "activityId": "reply", "ts": 1001,
            "nativeRunId": "native-run", "traceId": "b" * 32,
        }
        self.assertEqual(select_delivery({"events": [event]}, expected, 1002)["nativeRunId"], "native-run")
        with self.assertRaisesRegex(ValueError, "browser"):
            select_delivery({"events": [{**event, "sha256": "c" * 64}]}, expected, 1002)
        with self.assertRaisesRegex(ValueError, "exact request"):
            select_delivery({"events": [{**event, "requestActivityId": "another-request"}]}, expected, 1002)
        with self.assertRaisesRegex(ValueError, "expired"):
            select_delivery({"events": [event]}, expected, 2000)

    def test_native_terminal_proof_requires_completed_run_and_matching_successful_call(self):
        payload = {
            "runId": "native-run", "status": "completed", "runtimeMetadataAvailable": True,
            "runtime": {"provider": "custom", "model": "actual-model", "route_source": "native"},
            "usage": {"input_tokens": 100, "output_tokens": 20},
            "tools": [
                {"event": "tool.started", "tool": "terminal", "toolCallId": "call-1"},
                {"event": "tool.completed", "tool": "terminal", "toolCallId": "call-1",
                 "error": False, "duration": 0.1},
            ],
        }
        result = validate_run(payload, "native-run")
        self.assertEqual(result["runtime"]["provider"], "custom")
        self.assertNotIn("foundry", result)
        for invalid in (
            {**payload, "runId": "different"},
            {**payload, "runtimeMetadataAvailable": False},
            {**payload, "tools": []},
            {**payload, "tools": [payload["tools"][0], {**payload["tools"][1], "error": True}]},
            {**payload, "tools": [payload["tools"][0], {**payload["tools"][1], "toolCallId": "different"}]},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate_run(invalid, "native-run")

    def test_foundry_requires_observed_http_and_served_model_usage_not_declared_alias(self):
        columns = [{"name": name} for name in (
            "name", "success", "resultCode", "target", "responseModel", "inputTokens", "outputTokens", "type",
        )]
        chat = ["chat", True, "", "azure-foundry", "served-model", 100, 20, "gen_ai"]
        http = ["POST /", True, "200", "account.openai.azure.com:443", "", None, None, "HTTP"]
        def response(rows):
            return {"tables": [{"columns": columns, "rows": rows}]}
        self.assertIsNone(foundry_trace(response([chat])))
        self.assertIsNone(foundry_trace(response([http])))
        self.assertIsNone(foundry_trace(response([
            chat, ["internal", False, "403", "account.openai.azure.com", "", None, None, "HTTP"],
        ])))
        self.assertIsNone(foundry_trace(response([chat, [*http[:-1], "custom"]])))
        self.assertIsNone(foundry_trace(response([chat, [*http[:-1], None]])))
        proof = foundry_trace(response([chat, http]))
        self.assertEqual(proof["observedFoundryHost"], "account.openai.azure.com")
        self.assertEqual(proof["servedModel"], "served-model")

    @patch("run_on_client.time.sleep")
    def test_cleanup_requires_actual_resource_absence_after_acceptance(self, _):
        status = Mock(side_effect=[200, 404])
        confirm_deleted(status, timeout=1)
        self.assertEqual(status.call_count, 2)
        with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
            confirm_deleted(lambda: 403, timeout=1)

    @patch("run_on_client.time.sleep")
    @patch("run_on_client.time.monotonic", side_effect=[0, 0, 2])
    def test_accepted_delete_does_not_pass_while_resource_still_exists(self, _, __):
        with self.assertRaisesRegex(TimeoutError, "resource removal is unverified"):
            confirm_deleted(lambda: 200, timeout=1)

    def test_widget_requires_selected_release_and_exact_guest_blob_bytes(self):
        commit = "a" * 40
        payload = {
            "source_commit": commit, "runtime_release": "/app/releases/native",
            "bundled_skills_path": "/app/releases/native/runtime_skills",
            "files": {
                name: {**expected, "guest_path": "/app/releases/native/" + name}
                for name, expected in EXPECTED.items()
            },
        }
        self.assertEqual(validate_attestation(payload, commit)["status"], "pass")
        for invalid in (
            {**payload, "source_commit": "b" * 40},
            {**payload, "bundled_skills_path": "/app/releases/old/runtime_skills"},
            {**payload, "files": {
                **payload["files"],
                "bridge/html_widgets.py": {**payload["files"]["bridge/html_widgets.py"], "sha256": "0" * 64},
            }},
            {**payload, "files": {}},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate_attestation(invalid, commit)

    def test_widget_followup_requires_exact_accepted_invoke_and_no_replay_turn(self):
        expected = {
            "turn": "followup", "conversationId": "chat", "replyActivityId": "followup",
            "widgetActivityId": "widget", "requestedAt": 100,
        }
        accepted = {
            "event": "widgetSubmissionAccepted", "ts": 101, "conversationId": "chat",
            "requestActivityId": "opaque-invoke", "replyToId": "widget",
            "interactionId": "interaction", "status": "accepted",
        }
        replay = {**accepted, "requestActivityId": "opaque-replay", "status": "already_submitted"}
        followup = {
            "event": "turnReplySent", "ts": 102, "conversationId": "chat",
            "requestActivityId": "opaque-invoke", "activityId": "followup",
            "replyToId": "widget", "interactionId": "interaction",
            "nativeRunId": "actual-run", "traceId": "a" * 32,
        }
        events = [replay, followup, accepted]
        selected = select_widget_turn({"events": events}, expected, 110)
        self.assertEqual(selected["acceptedInvokeId"], "opaque-invoke")
        for invalid in (
            [replay, {**followup, "requestActivityId": "browser-guessed"}, accepted],
            [followup, accepted],
            [*events, {**followup, "requestActivityId": "opaque-replay", "activityId": "second-followup"}],
            [replay, {**followup, "nativeRunId": None}, accepted],
            [*events, accepted],
            [replay, {**followup, "ts": -1000}, accepted],
        ):
            with self.subTest(events=invalid):
                with self.assertRaises(ValueError):
                    select_widget_turn({"events": invalid}, expected, 110)

    def test_widget_producing_turn_cannot_reuse_unrelated_request(self):
        expected = {
            "turn": "producing", "conversationId": "chat", "requestActivityId": "browser-request",
            "replyActivityId": "widget", "requestedAt": 100,
        }
        turn = {
            "event": "turnReplySent", "ts": 101, "conversationId": "chat",
            "requestActivityId": "browser-request", "activityId": "widget",
            "replyToId": None, "interactionId": None, "nativeRunId": "run", "traceId": "a" * 32,
        }
        self.assertEqual(select_widget_turn({"events": [turn]}, expected, 110)["nativeRunId"], "run")
        with self.assertRaises(ValueError):
            select_widget_turn({"events": [{**turn, "requestActivityId": "other"}]}, expected, 110)

    def test_widget_mode_does_not_weaken_file_terminal_requirement(self):
        payload = {
            "runId": "run", "status": "completed", "runtimeMetadataAvailable": True,
            "runtime": {"provider": "actual", "model": "served"}, "tools": [], "usage": {},
        }
        self.assertEqual(validate_run(payload, "run", require_terminal=False)["status"], "completed")
        with self.assertRaisesRegex(ValueError, "terminal"):
            validate_run(payload, "run")
        with self.assertRaises(ValueError):
            validate_run({**payload, "runtimeMetadataAvailable": False}, "run", require_terminal=False)


if __name__ == "__main__":
    unittest.main()
