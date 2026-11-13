import io
import json
import unittest
from unittest.mock import MagicMock, patch

from scripts import collective_learning, schedule_recovery


class ScheduleRecoveryCliTests(unittest.TestCase):
    def test_known_result_cli_keeps_original_delivery_and_verified_output_identity(self):
        with (
            patch("sys.argv", [
                "schedule_recovery", "--state-name", "hermes2", "resume-known-result",
                "--queue", "queue", "--message-id", "original", "--execution-id", "native",
                "--output-sha256", "a" * 64, "--reason", "verified",
            ]),
            patch.object(schedule_recovery, "_operator_request", return_value={"status": "completed"}) as request,
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            schedule_recovery.main()
        self.assertEqual(request.call_args.args[0], "/internal/transport/resume-known-result")
        self.assertEqual(request.call_args.kwargs["body"]["messageId"], "original")
        self.assertEqual(request.call_args.kwargs["body"]["executionId"], "native")

    def test_repair_cli_uses_existing_delegated_operator_app_never_runtime_key(self):
        credential = MagicMock()
        credential.__enter__.return_value = credential
        credential.get_token.return_value.token = "owner-bearer"
        config = {
            "agent365_tenant_id": "tenant", "collective_learning_operator_client_id": "operator-app",
            "api_server_key": "runtime-key-must-not-authorize-repair",
        }
        with (
            patch("sys.argv", [
                "schedule_recovery", "--state-name", "worker", "rearm",
                "--job-id", "job", "--revision", "revision", "--repair-id", "ticket", "--reason", "verified preclaim",
            ]),
            patch.object(collective_learning, "_load_json", side_effect=[{"bridge_url": "https://gateway"}, config]),
            patch.object(collective_learning, "AzureCliCredential", return_value=credential),
            patch.object(collective_learning.urllib.request, "urlopen", return_value=io.BytesIO(b'{"status":"rearmed"}')) as send,
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            schedule_recovery.main()
        credential.get_token.assert_called_once_with("api://operator-app/.default")
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, "https://gateway/internal/transport/rearm-preclaim")
        self.assertEqual(request.get_header("Authorization"), "Bearer owner-bearer")
        self.assertIsNone(request.get_header("X-autopilot-key"))
        self.assertNotIn("operator", json.loads(request.data))


if __name__ == "__main__":
    unittest.main()
