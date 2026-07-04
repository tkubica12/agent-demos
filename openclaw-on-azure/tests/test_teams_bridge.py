from types import SimpleNamespace
import unittest

from bridge.app import (
    _teams_memory,
    bot_is_mentioned,
    format_teams_context,
    format_teams_event_prompt,
    openclaw_memory_record,
    remember_teams_event,
    response_should_be_suppressed,
    should_add_processing_reaction,
    supports_streaming_response,
    teams_event_memory_record,
    teams_is_targeted,
    teams_prompt_text,
    teams_response_contract,
    teams_session_key,
    teams_signal_type,
)
from scripts.sandbox_gateway import existing_gateway_sandbox


def ns(**values):
    return SimpleNamespace(**values)


class TeamsBridgeTests(unittest.TestCase):
    def tearDown(self):
        _teams_memory.clear()

    def test_groupchat_prompt_strips_bot_mention(self):
        activity = ns(
            text="<at>OpenClaw</at> list services",
            recipient=ns(id="bot-1", name="OpenClaw"),
            conversation=ns(conversation_type="groupchat", id="group-1"),
            entities=[
                ns(
                    type="mention",
                    text="<at>OpenClaw</at>",
                    mentioned=ns(id="bot-1", name="OpenClaw"),
                )
            ],
        )

        self.assertTrue(bot_is_mentioned(activity))
        self.assertEqual(teams_prompt_text(activity), "list services")
        self.assertEqual(teams_session_key(activity), "teams:groupchat:group-1")

    def test_channel_session_key_preserves_thread(self):
        activity = ns(
            id="message-1",
            reply_to_id="root-message",
            conversation=ns(conversation_type="channel", id="conversation-1"),
            channel_data={
                "team": {"id": "team-1"},
                "channel": {"id": "channel-1"},
            },
        )

        self.assertEqual(
            teams_session_key(activity),
            "teams:channel:conversation-1:team:team-1:channel:channel-1:thread:root-message",
        )

    def test_targeted_message_detection(self):
        activity = ns(recipient=ns(is_targeted=True))

        self.assertTrue(teams_is_targeted(activity))

    def test_weak_signal_prompt_allows_suppression(self):
        activity = ns(
            id="message-1",
            text="The production database migration can run during peak hours.",
            from_=ns(name="Adele"),
            recipient=ns(id="bot-1", name="OpenClaw"),
            conversation=ns(conversation_type="groupchat", id="group-1"),
            entities=[],
        )

        prompt = format_teams_event_prompt(activity, teams_prompt_text(activity), event="message")

        self.assertIn("Signal type: undirected_message", prompt)
        self.assertIn("Response contract: observe_then_maybe_answer", prompt)
        self.assertIn("weak signal context", prompt)
        self.assertIn("return exactly NO_RESPONSE", prompt)
        self.assertTrue(response_should_be_suppressed(" NO_RESPONSE "))
        self.assertFalse(response_should_be_suppressed("I should jump in."))

    def test_context_window_includes_recent_events_and_openclaw_answer(self):
        session_key = "teams:groupchat:group-1"
        activity = ns(
            id="message-2",
            reply_to_id="message-1",
            text="Can it also check incidents?",
            from_=ns(name="Adele"),
            recipient=ns(id="bot-1", name="OpenClaw"),
            conversation=ns(conversation_type="groupchat", id="group-1"),
            entities=[],
        )
        root = ns(
            id="message-1",
            text="Initial question",
            from_=ns(name="Diego"),
            recipient=ns(id="bot-1", name="OpenClaw"),
            conversation=ns(conversation_type="groupchat", id="group-1"),
            entities=[],
        )
        remember_teams_event(
            session_key,
            teams_event_memory_record(
                root,
                event="message",
                message=teams_prompt_text(root),
                signal_type=teams_signal_type(root),
                response_contract=teams_response_contract(root, teams_signal_type(root)),
            ),
        )
        remember_teams_event(session_key, openclaw_memory_record("Previous OpenClaw answer"))

        context = format_teams_context(
            session_key,
            signal_type=teams_signal_type(activity),
            response_contract=teams_response_contract(activity, teams_signal_type(activity)),
            reply_to_id="message-1",
        )

        self.assertIn("Bridge-observed context window", context)
        self.assertIn("Initial question", context)
        self.assertIn("Previous OpenClaw answer", context)

    def test_prompt_contains_context_block(self):
        activity = ns(
            id="message-1",
            text="<at>OpenClaw</at> help",
            from_=ns(name="Adele"),
            recipient=ns(id="bot-1", name="OpenClaw"),
            conversation=ns(conversation_type="groupchat", id="group-1"),
            entities=[
                ns(
                    type="mention",
                    text="<at>OpenClaw</at>",
                    mentioned=ns(id="bot-1", name="OpenClaw"),
                )
            ],
        )

        prompt = format_teams_event_prompt(activity, teams_prompt_text(activity), event="message", context="recent context")

        self.assertIn("Context available to you:\nrecent context", prompt)

    def test_streaming_is_personal_chat_only(self):
        self.assertTrue(supports_streaming_response(ns(activity=ns(conversation=ns(conversation_type="personal")))))
        self.assertFalse(supports_streaming_response(ns(activity=ns(conversation=ns(conversation_type="channel")))))

    def test_processing_reactions_default_off(self):
        self.assertFalse(should_add_processing_reaction())

    def test_event_prompt_marks_mention_as_must_answer(self):
        activity = ns(
            id="message-1",
            text="<at>OpenClaw</at> help",
            from_=ns(name="Adele"),
            recipient=ns(id="bot-1", name="OpenClaw"),
            conversation=ns(conversation_type="groupchat", id="group-1"),
            entities=[
                ns(
                    type="mention",
                    text="<at>OpenClaw</at>",
                    mentioned=ns(id="bot-1", name="OpenClaw"),
                )
            ],
        )

        prompt = format_teams_event_prompt(activity, teams_prompt_text(activity), event="message")

        self.assertIn("Signal type: explicit_bot_mention", prompt)
        self.assertIn("Response contract: must_answer", prompt)

    def test_event_prompt_marks_reply_without_mention(self):
        activity = ns(
            id="message-2",
            reply_to_id="message-1",
            text="Thanks, but can it also check incidents?",
            from_=ns(name="Adele"),
            recipient=ns(id="bot-1", name="OpenClaw"),
            conversation=ns(conversation_type="channel", id="conversation-1"),
            entities=[],
        )

        prompt = format_teams_event_prompt(activity, teams_prompt_text(activity), event="message")

        self.assertIn("Signal type: reply_in_thread_without_bot_mention", prompt)
        self.assertIn("Response contract: observe_then_maybe_answer", prompt)

    def test_plain_text_openclaw_name_is_must_answer(self):
        activity = ns(
            id="message-1",
            text="Možná by mohl OpenClaw říct ahoj, i když ho netaguji, ne?",
            from_=ns(name="Adele"),
            recipient=ns(id="bot-1", name="OpenClaw"),
            conversation=ns(conversation_type="channel", id="conversation-1"),
            entities=[],
        )
        message = teams_prompt_text(activity)
        signal_type = teams_signal_type(activity, message=message)
        response_contract = teams_response_contract(activity, signal_type)

        self.assertEqual(signal_type, "textual_bot_name_mention")
        self.assertEqual(response_contract, "must_answer")

    def test_channel_thread_reply_after_openclaw_answer_is_must_answer(self):
        activity = ns(
            id="reply-message",
            text="dobře, co umíš?",
            from_=ns(name="Adele"),
            recipient=ns(id="bot-1", name="OpenClaw"),
            conversation=ns(conversation_type="channel", id="conversation-1;messageid=root-message"),
            entities=[],
        )
        session_key = teams_session_key(activity)
        remember_teams_event(session_key, openclaw_memory_record("Ahoj, slyším tě."))
        signal_type = teams_signal_type(activity, message=teams_prompt_text(activity))
        response_contract = teams_response_contract(activity, signal_type, session_key=session_key)

        self.assertEqual(session_key, "teams:channel:conversation-1;messageid=root-message:thread:root-message")
        self.assertEqual(signal_type, "reply_in_thread_without_bot_mention")
        self.assertEqual(response_contract, "must_answer")

    def test_existing_gateway_sandbox_reuses_attached_volume_after_image_rebuild(self):
        test_case = self

        class Client:
            _group_path = "/groups/test"

            def _dp_get(self, path):
                test_case.assertEqual(path, "/groups/test/sandboxes")
                return [
                    {
                        "id": "sandbox-1",
                        "labels": {"app": "openclaw-on-azure"},
                        "sourcesRef": {"diskImage": {"id": "old-disk"}},
                        "volumes": [{"volumeName": "openclaw-data"}],
                    }
                ]

        self.assertEqual(existing_gateway_sandbox(Client(), "openclaw-data")["id"], "sandbox-1")


if __name__ == "__main__":
    unittest.main()
