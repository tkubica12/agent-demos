import os
import time
import unittest
from unittest.mock import patch

from bridge.document_cards import (
    ACTION_CHOICE_FIELD,
    ACTION_TOKEN_FIELD,
    action_data,
    create_action_token,
    decode_action_token,
    office_operation_scope,
)


class DocumentCardTests(unittest.TestCase):
    def test_action_token_is_confidential_bound_and_expiring(self):
        with patch.dict(
            os.environ,
            {"WORKER_ID": "hermes2"},
        ):
            token = create_action_token(
                operation_id="a" * 24,
                operation_scope="scope-1",
                user_id="user-1",
                conversation_id="conversation-1",
                expires_at_unix=time.time() + 300,
                secret="secret",
            )
            decoded = decode_action_token(
                token,
                user_id="user-1",
                conversation_id="conversation-1",
                secret="secret",
            )

        self.assertNotIn("a" * 24, token)
        self.assertEqual(decoded["operationId"], "a" * 24)
        with self.assertRaisesRegex(ValueError, "another user"):
            decode_action_token(
                token,
                user_id="user-2",
                conversation_id="conversation-1",
                secret="secret",
            )

    def test_action_data_supports_submit_and_invoke_shapes(self):
        direct = {
            ACTION_TOKEN_FIELD: "token",
            ACTION_CHOICE_FIELD: "background",
        }
        invoke = {"action": {"data": direct}}

        self.assertEqual(action_data(direct)["choice"], "background")
        self.assertEqual(action_data(invoke)["token"], "token")
        self.assertIsNone(
            action_data(
                {
                    ACTION_TOKEN_FIELD: "token",
                    ACTION_CHOICE_FIELD: "unknown",
                }
            )
        )

    def test_operation_scope_is_stable_and_user_isolated(self):
        self.assertEqual(
            office_operation_scope(
                "conversation-1",
                "user-1",
            ),
            office_operation_scope(
                "conversation-1",
                "user-1",
            ),
        )
        self.assertNotEqual(
            office_operation_scope(
                "conversation-1",
                "user-1",
            ),
            office_operation_scope(
                "conversation-1",
                "user-2",
            ),
        )


if __name__ == "__main__":
    unittest.main()
