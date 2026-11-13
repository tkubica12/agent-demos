import io
import json
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
from run_on_client import execution_result
from verify_download import verify_download


class PrivateProbeTests(unittest.TestCase):
    def test_only_rfc1918_is_private(self):
        for value in ("10.1.2.3", "172.20.1.2", "192.168.3.4"):
            self.assertTrue(private_address(value))
        for value in ("127.0.0.1", "169.254.169.254", "192.0.2.3", "100.65.0.1", "20.1.2.3"):
            self.assertFalse(private_address(value))

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


if __name__ == "__main__":
    unittest.main()
