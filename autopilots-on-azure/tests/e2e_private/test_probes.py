import io
import json
import socket
import ssl
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from fixtures import create_fixtures
from network_probe import check, private_address, public_control


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


if __name__ == "__main__":
    unittest.main()
