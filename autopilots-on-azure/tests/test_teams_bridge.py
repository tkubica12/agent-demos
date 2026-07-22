import asyncio
import os
from types import SimpleNamespace
import unittest

import bridge.app as bridge_app
from bridge.app import (
    DreamRunRequest,
    _teams_memory,
    _teams_diag,
    bot_is_mentioned,
    delete_message_reaction,
    format_teams_context,
    format_teams_event_prompt,
    agent_memory_record,
    memory_has_agent_message_id,
    reacted_message_id,
    response_has_visible_text,
    remember_teams_event,
    response_should_be_suppressed,
    send_typing_indicators,
    send_message_reaction,
    teams_reaction_path,
    should_acknowledge_with_reaction,
    should_add_processing_reaction,
    should_add_status_reaction,
    should_quote_group_responses,
    split_teams_response_instructions,
    supports_streaming_response,
    supports_typing_indicators,
    teams_event_memory_record,
    teams_is_targeted,
    teams_prompt_text,
    teams_response_contract,
    teams_session_key,
    teams_signal_type,
)
from bridge.runtime.base import AgentResponse, DreamResponse
from scripts.sandbox_runtime import existing_gateway_sandbox
from scripts.sandbox_runtime import private_incidents_mcp_server_config


def ns(**values):
    return SimpleNamespace(**values)


class FakeReactionResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeReactionSession:
    def __init__(self):
        self.calls = []

    def put(self, path):
        self.calls.append(("PUT", path))
        return FakeReactionResponse()

    def delete(self, path):
        self.calls.append(("DELETE", path))
        return FakeReactionResponse()


class FakeTypingContext:
    def __init__(self, conversation_type, done):
        self.activity = ns(conversation=ns(conversation_type=conversation_type))
        self.done = done
        self.activities = []

    async def send_activity(self, activity):
        self.activities.append(activity)
        self.done.set()


class TeamsBridgeTests(unittest.TestCase):
    def tearDown(self):
        _teams_memory.clear()
        _teams_diag.clear()

    def test_runtime_display_name_is_used_for_hermes_memory(self):
        previous = {
            "AGENT_RUNTIME": os.environ.get("AGENT_RUNTIME"),
            "AUTOPILOT_TEAMS_NAME": os.environ.get("AUTOPILOT_TEAMS_NAME"),
        }
        os.environ["AGENT_RUNTIME"] = "hermes"
        os.environ["AUTOPILOT_TEAMS_NAME"] = "Hermes 2"
        try:
            record = agent_memory_record("Ahoj")
            self.assertEqual(bridge_app.runtime_display_name(), "Hermes 2")
            self.assertEqual(record["role"], "agent")
            self.assertEqual(record["sender"], "Hermes 2")
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_internal_dream_requires_operator_key_and_returns_packet(self):
        class Adapter:
            runtime_kind = "hermes"

            async def dream(self, request):
                self.request = request
                return DreamResponse(
                    agent=AgentResponse(
                        text="Dream complete",
                        raw={
                            "sandboxId": "sandbox-1",
                            "gatewayUrl": "https://hermes.example",
                            "reusedExistingSandbox": True,
                        },
                    ),
                    learning_status={"statusVersion": "2.0", "records": []},
                )

        adapter = Adapter()
        original_adapter = bridge_app.runtime_adapter
        previous_key = os.environ.get("API_SERVER_KEY")
        previous_worker = os.environ.get("WORKER_ID")
        os.environ["API_SERVER_KEY"] = "operator-key"
        os.environ["WORKER_ID"] = "worker-1"
        bridge_app.runtime_adapter = lambda: adapter
        request = ns(headers={"x-autopilot-key": "operator-key"})
        try:
            result = asyncio.run(
                bridge_app.dream(
                    DreamRunRequest(focus="recent delivery work", maxRecords=2),
                    request,
                )
            )
        finally:
            bridge_app.runtime_adapter = original_adapter
            if previous_key is None:
                os.environ.pop("API_SERVER_KEY", None)
            else:
                os.environ["API_SERVER_KEY"] = previous_key
            if previous_worker is None:
                os.environ.pop("WORKER_ID", None)
            else:
                os.environ["WORKER_ID"] = previous_worker

        self.assertTrue(adapter.request.session_id.startswith("dream:worker-1:"))
        self.assertEqual(result.learning_status["statusVersion"], "2.0")
        self.assertEqual(result.sandbox_id, "sandbox-1")

    def test_internal_dream_rejects_wrong_operator_key(self):
        previous = os.environ.get("API_SERVER_KEY")
        os.environ["API_SERVER_KEY"] = "expected"
        try:
            with self.assertRaises(Exception) as raised:
                bridge_app.require_operator_key(ns(headers={"x-autopilot-key": "wrong"}))
        finally:
            if previous is None:
                os.environ.pop("API_SERVER_KEY", None)
            else:
                os.environ["API_SERVER_KEY"] = previous

        self.assertEqual(raised.exception.status_code, 401)

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
            text="We should discuss the quarterly planning notes tomorrow.",
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
        remember_teams_event(session_key, agent_memory_record("Previous OpenClaw answer"))

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

    def test_streaming_is_disabled_for_agent365(self):
        self.assertFalse(supports_streaming_response(ns(activity=ns(conversation=ns(conversation_type="personal")))))
        self.assertFalse(supports_streaming_response(ns(activity=ns(conversation=ns(conversation_type="channel")))))

    def test_typing_is_supported_only_in_personal_and_group_chats(self):
        self.assertTrue(supports_typing_indicators(ns(activity=ns(conversation=ns(conversation_type="personal")))))
        self.assertTrue(supports_typing_indicators(ns(activity=ns(conversation=ns(conversation_type="groupchat")))))
        self.assertFalse(supports_typing_indicators(ns(activity=ns(conversation=ns(conversation_type="channel")))))

    def test_typing_indicator_is_sent_for_personal_chat(self):
        async def run():
            done = asyncio.Event()
            ctx = FakeTypingContext("personal", done)
            await send_typing_indicators(ctx, "conversation-1", done)
            return ctx

        ctx = asyncio.run(run())

        self.assertEqual(len(ctx.activities), 1)
        self.assertEqual(ctx.activities[0].type, "typing")
        self.assertEqual(_teams_diag[0]["event"], "typingSent")

    def test_typing_indicator_is_skipped_for_channels(self):
        async def run():
            done = asyncio.Event()
            ctx = FakeTypingContext("channel", done)
            await send_typing_indicators(ctx, "conversation-1", done)
            return ctx

        ctx = asyncio.run(run())

        self.assertEqual(ctx.activities, [])
        self.assertEqual(_teams_diag[0]["event"], "typingSkipped")

    def test_processing_reactions_and_quoted_replies_default_on(self):
        self.assertTrue(should_add_processing_reaction())
        self.assertTrue(should_quote_group_responses())

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

    def test_risky_undirected_message_remains_agent_decision(self):
        activity = ns(
            id="message-1",
            text="Navrhuji spustit produkční migraci databáze během špičky bez rollback plánu.",
            from_=ns(name="Adele"),
            recipient=ns(id="bot-1", name="OpenClaw"),
            conversation=ns(conversation_type="channel", id="conversation-1"),
            entities=[],
        )
        message = teams_prompt_text(activity)
        signal_type = teams_signal_type(activity, message=message)
        response_contract = teams_response_contract(activity, signal_type)
        prompt = format_teams_event_prompt(activity, message, event="message", signal_type=signal_type, response_contract=response_contract)

        self.assertEqual(signal_type, "undirected_message")
        self.assertEqual(response_contract, "observe_then_maybe_answer")
        self.assertIn("You decide whether to answer or return NO_RESPONSE", prompt)
        self.assertNotIn("high_risk_undirected_message", prompt)

    def test_thanks_in_active_thread_can_be_acknowledged_with_reaction(self):
        session_key = "teams:channel:conversation-1;messageid=root-message:thread:root-message"
        remember_teams_event(session_key, agent_memory_record("Ahoj, slyším tě."))

        self.assertTrue(should_acknowledge_with_reaction("díky!", "reply_in_thread_without_bot_mention", session_key))
        self.assertFalse(should_acknowledge_with_reaction("díky OpenClaw", "textual_bot_name_mention", session_key))
        self.assertFalse(should_acknowledge_with_reaction("díky!", "reply_in_thread_without_bot_mention", "teams:channel:other"))

    def test_agent_can_request_teams_reaction_control_line(self):
        visible, reaction = split_teams_response_instructions("TEAMS_REACTION: shocked\nTohle vypadá riskantně.")

        self.assertEqual(visible, "Tohle vypadá riskantně.")
        self.assertEqual(reaction, "surprised")

    def test_agent_can_request_reaction_with_no_public_response(self):
        visible, reaction = split_teams_response_instructions("NO_RESPONSE\nTEAMS_REACTION: heart")

        self.assertEqual(visible, "NO_RESPONSE")
        self.assertEqual(reaction, "heart")
        self.assertTrue(response_should_be_suppressed("NO_RESPONSE\nTEAMS_REACTION: heart"))
        self.assertFalse(response_has_visible_text("NO_RESPONSE\nTEAMS_REACTION: heart"))

    def test_agent_reaction_only_output_has_no_visible_text(self):
        visible, reaction = split_teams_response_instructions("TEAMS_REACTION: heart")

        self.assertEqual(visible, "")
        self.assertEqual(reaction, "heart")
        self.assertFalse(response_has_visible_text("TEAMS_REACTION: heart"))

    def test_agent_can_request_reaction_and_visible_message(self):
        visible, reaction = split_teams_response_instructions("TEAMS_REACTION: surprised\nTohle bych nedělal bez rollback plánu.")

        self.assertEqual(visible, "Tohle bych nedělal bez rollback plánu.")
        self.assertEqual(reaction, "surprised")
        self.assertTrue(response_has_visible_text("TEAMS_REACTION: surprised\nTohle bych nedělal bez rollback plánu."))

    def test_event_prompt_teaches_agent_reaction_vocabulary(self):
        activity = ns(
            id="message-1",
            text="Navrhuji spustit produkční migraci databáze během špičky bez rollback plánu.",
            from_=ns(name="Adele"),
            recipient=ns(id="bot-1", name="OpenClaw"),
            conversation=ns(conversation_type="channel", id="conversation-1"),
            entities=[],
        )

        prompt = format_teams_event_prompt(activity, teams_prompt_text(activity), event="message")

        self.assertIn("TEAMS_REACTION: <name>", prompt)
        self.assertIn("surprised=risky or alarming proposal", prompt)

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
        remember_teams_event(session_key, agent_memory_record("Ahoj, slyším tě."))
        signal_type = teams_signal_type(activity, message=teams_prompt_text(activity))
        response_contract = teams_response_contract(activity, signal_type, session_key=session_key)

        self.assertEqual(session_key, "teams:channel:conversation-1;messageid=root-message:thread:root-message")
        self.assertEqual(signal_type, "reply_in_thread_without_bot_mention")
        self.assertEqual(response_contract, "must_answer")

    def test_agent_message_id_memory_filters_reactions(self):
        session_key = "teams:channel:conversation-1;messageid=root-message:thread:root-message"
        remember_teams_event(session_key, agent_memory_record("Ahoj, slyším tě.", "bot-message-1"))

        self.assertTrue(memory_has_agent_message_id(session_key, "bot-message-1"))
        self.assertFalse(memory_has_agent_message_id(session_key, "human-message-1"))

    def test_reaction_target_uses_reply_to_id(self):
        activity = ns(reply_to_id="bot-message-1")

        self.assertEqual(reacted_message_id(activity), "bot-message-1")

    def test_reaction_path_uses_preview_connector_endpoint(self):
        self.assertEqual(
            teams_reaction_path("conversation-1", "message-1", "1f440_eyes"),
            "v3/conversations/conversation-1/activities/message-1/reactions/1f440_eyes",
        )

    def test_send_and_delete_message_reaction_use_connector_client(self):
        session = FakeReactionSession()
        ctx = ns(
            activity=ns(conversation=ns(id="conversation-1")),
            turn_state={"ConnectorClient": ns(client=session)},
        )

        asyncio.run(send_message_reaction(ctx, "message-1", "1f440_eyes"))
        asyncio.run(delete_message_reaction(ctx, "message-1", "1f440_eyes"))

        self.assertEqual(
            session.calls,
            [
                ("PUT", "v3/conversations/conversation-1/activities/message-1/reactions/1f440_eyes"),
                ("DELETE", "v3/conversations/conversation-1/activities/message-1/reactions/1f440_eyes"),
            ],
        )
        self.assertEqual(_teams_diag[1]["event"], "reactionSent")
        self.assertEqual(_teams_diag[0]["event"], "reactionDeleted")

    def test_channel_reaction_session_key_prefers_thread_root_over_reacted_message(self):
        activity = ns(
            id="reaction-1",
            reply_to_id="bot-message-1",
            conversation=ns(conversation_type="channel", id="conversation-1;messageid=root-message"),
            channel_data={"team": {"id": "team-1"}, "channel": {"id": "channel-1"}},
        )

        self.assertEqual(
            teams_session_key(activity),
            "teams:channel:conversation-1;messageid=root-message:team:team-1:channel:channel-1:thread:root-message",
        )

    def test_status_reaction_is_only_for_public_forwarded_messages(self):
        self.assertTrue(
            should_add_status_reaction(ns(id="message-1", conversation=ns(conversation_type="channel"), recipient=ns(is_targeted=False)))
        )
        self.assertFalse(should_add_status_reaction(ns(id="message-1", conversation=ns(conversation_type="personal"), recipient=ns(is_targeted=False))))
        self.assertFalse(should_add_status_reaction(ns(id="message-1", conversation=ns(conversation_type="channel"), recipient=ns(is_targeted=True))))

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

    def test_private_incidents_mcp_config_uses_local_identity_adapter(self):
        config = private_incidents_mcp_server_config(url="http://127.0.0.1:18081/servers/private-incidents")

        self.assertEqual(config["url"], "http://127.0.0.1:18081/servers/private-incidents")
        self.assertNotIn("headers", config)


if __name__ == "__main__":
    unittest.main()
