import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from autopilots_identity.interaction_actions import claim_interaction
from bridge.interactions import (
    ACTION_CHOICE_FIELD,
    ACTION_TOKEN_FIELD,
    CARD_CONTENT_TYPE,
    decode_interaction_token,
    extract_interaction_request,
    interaction_action_data,
    render_card,
    validate_interaction_spec,
)


class InteractionTests(unittest.TestCase):
    def test_display_dsl_renders_bounded_adaptive_card(self):
        spec = validate_interaction_spec(
            {
                "kind": "display",
                "title": "Project status",
                "summary": "Two items need attention.",
                "status": {
                    "label": "At risk",
                    "tone": "warning",
                },
                "facts": [
                    {"label": "Owner", "value": "Adele"},
                ],
                "table": {
                    "columns": ["Item", "State"],
                    "rows": [["Design", "Ready"]],
                },
            }
        )

        activity, interaction_id = render_card(
            spec,
            session_key="teams:personal:conversation-1",
            user_id="user-1",
            conversation_id="conversation-1",
            expires_at_unix=time.time() + 60,
        )

        self.assertEqual(len(interaction_id), 32)
        self.assertEqual(
            activity.attachments[0].content_type,
            CARD_CONTENT_TYPE,
        )
        card = activity.attachments[0].content
        self.assertEqual(card["version"], "1.4")
        self.assertNotIn("actions", card)
        self.assertIn("FactSet", json.dumps(card))
        self.assertIn("ColumnSet", json.dumps(card))

    def test_choice_card_uses_execute_with_submit_fallback(self):
        spec = validate_interaction_spec(
            {
                "kind": "choice",
                "title": "Choose an option",
                "choices": [
                    {
                        "id": "approve",
                        "label": "Approve",
                    },
                    {
                        "id": "revise",
                        "label": "Revise",
                    },
                ],
            }
        )
        with patch.dict(
            os.environ,
            {"API_SERVER_KEY": "secret", "WORKER_ID": "hermes2"},
        ):
            activity, _interaction_id = render_card(
                spec,
                session_key="teams:personal:conversation-1",
                user_id="user-1",
                conversation_id="conversation-1",
                expires_at_unix=time.time() + 60,
            )
            action = activity.attachments[0].content["actions"][0]
            token = decode_interaction_token(
                action["data"][ACTION_TOKEN_FIELD],
                user_id="user-1",
                conversation_id="conversation-1",
            )

        self.assertEqual(action["type"], "Action.Execute")
        self.assertEqual(action["fallback"]["type"], "Action.Submit")
        self.assertEqual(token["choiceId"], "approve")
        self.assertEqual(token["choiceMessage"], "I selected: Approve.")

    def test_request_markers_are_removed_from_visible_text(self):
        visible, spec = extract_interaction_request(
            "Choose one.\n"
            "<ADAPTIVE_CARD_REQUEST>"
            '{"kind":"confirm","title":"Proceed?"}'
            "</ADAPTIVE_CARD_REQUEST>"
        )

        self.assertEqual(visible, "Choose one.")
        self.assertEqual(spec["kind"], "confirm")
        self.assertEqual(len(spec["choices"]), 2)

    def test_display_card_rejects_model_authored_choices(self):
        with self.assertRaisesRegex(
            ValueError,
            "Display cards cannot contain choices",
        ):
            validate_interaction_spec(
                {
                    "kind": "display",
                    "title": "Unsafe",
                    "choices": [
                        {
                            "id": "run",
                            "label": "Run",
                        },
                        {
                            "id": "stop",
                            "label": "Stop",
                        },
                    ],
                }
            )

    def test_action_data_accepts_execute_payload(self):
        payload = interaction_action_data(
            {
                "action": {
                    "data": {
                        ACTION_TOKEN_FIELD: "token",
                        ACTION_CHOICE_FIELD: "approve",
                    }
                }
            }
        )

        self.assertEqual(
            payload,
            {"token": "token", "choice": "approve"},
        )

    def test_interaction_claim_is_durable_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            first = claim_interaction(
                home,
                interaction_id="a" * 32,
                choice_id="approve",
                expires_at_unix=time.time() + 60,
            )
            second = claim_interaction(
                home,
                interaction_id="a" * 32,
                choice_id="revise",
                expires_at_unix=time.time() + 60,
            )

        self.assertTrue(first["claimed"])
        self.assertFalse(second["claimed"])
        self.assertEqual(second["choiceId"], "approve")

    def test_multiple_marker_blocks_degrade_to_visible_text(self):
        visible, spec = extract_interaction_request(
            "Safe answer.\n"
            "<ADAPTIVE_CARD_REQUEST>"
            '{"kind":"display","title":"One"}'
            "</ADAPTIVE_CARD_REQUEST>\n"
            "<ADAPTIVE_CARD_REQUEST>"
            '{"kind":"display","title":"Two"}'
            "</ADAPTIVE_CARD_REQUEST>"
        )

        self.assertEqual(visible, "Safe answer.")
        self.assertIsNone(spec)

    def test_confirm_rejects_model_authored_choices(self):
        with self.assertRaisesRegex(
            ValueError,
            "bridge-owned Confirm and Cancel",
        ):
            validate_interaction_spec(
                {
                    "kind": "confirm",
                    "title": "Proceed?",
                    "choices": [
                        {"id": "yes", "label": "Cancel"},
                        {"id": "no", "label": "Proceed"},
                    ],
                }
            )


if __name__ == "__main__":
    unittest.main()
