import json
import os
import unittest
from unittest.mock import patch

from bridge.generated_app_cards import (
    GENERATED_APP_ACTION,
    GENERATED_APP_ACTION_TOKEN,
    decode_generated_app_action_token,
    extract_generated_app_card_request,
    generated_app_action_data,
    generated_apps_card,
)


class GeneratedAppCardTests(unittest.TestCase):
    def test_card_has_open_retention_and_delete_actions(self):
        with patch.dict(
            os.environ,
            {"API_SERVER_KEY": "secret", "WORKER_ID": "hermes2"},
        ):
            card = generated_apps_card(
                [
                    {
                        "appId": "a" * 24,
                        "name": "Status site",
                        "state": "Stopped",
                        "url": "https://generated.example",
                        "retentionSeconds": 86400,
                    }
                ],
                user_id="user-1",
                conversation_id="conversation-1",
            )
            rendered = json.dumps(card)
            delete_action = card["actions"][-1]
            token = decode_generated_app_action_token(
                delete_action["data"][GENERATED_APP_ACTION_TOKEN],
                user_id="user-1",
                conversation_id="conversation-1",
            )

        self.assertIn("[Open site]", rendered)
        self.assertIn("Keep 72h", rendered)
        self.assertEqual(token["action"], "delete")
        self.assertEqual(token["appId"], "a" * 24)

    def test_marker_requests_owner_scoped_app_card(self):
        visible, request = extract_generated_app_card_request(
            "Your site is ready.\n"
            "<GENERATED_APP_CARD_REQUEST>"
            '{"mode":"app","appId":"aaaaaaaaaaaaaaaaaaaaaaaa"}'
            "</GENERATED_APP_CARD_REQUEST>"
        )

        self.assertEqual(visible, "Your site is ready.")
        self.assertEqual(request["mode"], "app")

    def test_action_data_accepts_execute_payload(self):
        result = generated_app_action_data(
            {
                "action": {
                    "data": {
                        GENERATED_APP_ACTION_TOKEN: "token",
                        GENERATED_APP_ACTION: "renew",
                    }
                }
            }
        )

        self.assertEqual(
            result,
            {"token": "token", "action": "renew"},
        )

    def test_token_is_bound_to_user_and_conversation(self):
        with patch.dict(
            os.environ,
            {"API_SERVER_KEY": "secret", "WORKER_ID": "hermes2"},
        ):
            card = generated_apps_card(
                [
                    {
                        "appId": "a" * 24,
                        "name": "Status site",
                        "state": "Running",
                        "url": "https://generated.example",
                        "retentionSeconds": 86400,
                    }
                ],
                user_id="user-1",
                conversation_id="conversation-1",
            )
            action = card["actions"][0]
            with self.assertRaisesRegex(
                ValueError,
                "another user",
            ):
                decode_generated_app_action_token(
                    action["data"][GENERATED_APP_ACTION_TOKEN],
                    user_id="user-2",
                    conversation_id="conversation-1",
                )


if __name__ == "__main__":
    unittest.main()
