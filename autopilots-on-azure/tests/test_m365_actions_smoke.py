import json
import unittest

from scripts.m365_actions_smoke import (
    TEAMS_RESULT_PREFIX,
    create_chat_prompt,
    parse_teams_result,
    proactive_message,
)


class M365ActionsSmokeTests(unittest.TestCase):
    def test_prompt_contains_recipient_and_hidden_marker(self):
        prompt = create_chat_prompt(
            "adele@example.com",
            "A14-TEAMS-test",
        )

        self.assertIn("adele@example.com", prompt)
        self.assertIn(proactive_message("A14-TEAMS-test"), prompt)
        self.assertIn(TEAMS_RESULT_PREFIX, prompt)

    def test_parse_requires_chat_and_message_ids(self):
        result = parse_teams_result(
            TEAMS_RESULT_PREFIX
            + json.dumps(
                {
                    "chatId": "chat-1",
                    "messageId": "message-1",
                }
            )
        )

        self.assertEqual(
            result,
            {
                "chatId": "chat-1",
                "messageId": "message-1",
            },
        )
        with self.assertRaisesRegex(ValueError, "incomplete"):
            parse_teams_result(
                TEAMS_RESULT_PREFIX
                + json.dumps({"chatId": "chat-1"})
            )


if __name__ == "__main__":
    unittest.main()
