import asyncio
import json
import os
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from microsoft_agents.activity import (
    Activity,
    ChannelAccount,
    ChannelId,
    Entity,
)
from microsoft_agents_a365.notifications import AgentNotificationActivity

import bridge.app as bridge_app
from bridge.app import (
    DreamRunRequest,
    InvokeRequest,
    _teams_memory,
    _teams_diag,
    bot_is_mentioned,
    delete_message_reaction,
    document_result_activity,
    format_teams_context,
    format_teams_event_prompt,
    agent_memory_record,
    memory_has_agent_message_id,
    handle_document_card_action,
    handle_teams_invoke,
    notification_prompt,
    normalize_agent365_activity_body,
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
    teams_runtime_message,
    pending_document_card_activity,
    teams_response_contract,
    teams_session_key,
    teams_signal_type,
)
from bridge.document_cards import (
    create_action_token,
    office_operation_scope,
)
from bridge.runtime.base import (
    AgentAuthContext,
    AgentResponse,
    DreamResponse,
)
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
    def test_agent365_product_info_entity_casing_is_normalized(self):
        body, changed = normalize_agent365_activity_body(
            json.dumps(
                {
                    "type": "event",
                    "entities": [
                        {
                            "type": "productInfo",
                            "productName": "Word",
                        },
                        {"type": "mention"},
                    ],
                    "channelData": {
                        "product": {
                            "type": "productInfo",
                        }
                    },
                }
            ).encode("utf-8")
        )
        payload = json.loads(body)

        self.assertTrue(changed)
        self.assertEqual(
            payload["entities"][0]["type"],
            "ProductInfo",
        )
        self.assertEqual(
            payload["entities"][1]["type"],
            "mention",
        )
        self.assertEqual(
            payload["channelData"]["product"]["type"],
            "ProductInfo",
        )

    def test_agent365_route_replays_normalized_body(self):
        original = json.dumps(
            {
                "type": "event",
                "conversation": {"id": "conversation-1"},
                "entities": [{"type": "productInfo"}],
            }
        ).encode("utf-8")

        async def receive():
            return {
                "type": "http.request",
                "body": original,
                "more_body": False,
            }

        request = bridge_app.Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "POST",
                "scheme": "https",
                "path": "/api/messages",
                "raw_path": b"/api/messages",
                "query_string": b"",
                "headers": [
                    (b"content-type", b"application/json")
                ],
                "client": ("127.0.0.1", 1),
                "server": ("test", 443),
            },
            receive,
        )
        observed = {}

        async def process(replay, *_args):
            observed.update(await replay.json())
            return None

        with patch.object(
            bridge_app,
            "start_agent_process",
            side_effect=process,
        ):
            asyncio.run(bridge_app.agent365_messages(request))

        self.assertEqual(
            observed["entities"][0]["type"],
            "ProductInfo",
        )

    def test_teams_runtime_message_keeps_commands_unwrapped(self):
        self.assertEqual(
            teams_runtime_message("/new", "formatted event"),
            "/new",
        )
        self.assertEqual(
            teams_runtime_message(
                "/learn retain this",
                "formatted event",
            ),
            "/learn retain this",
        )
        self.assertEqual(
            teams_runtime_message("ordinary work", "formatted event"),
            "formatted event",
        )

    def test_pending_document_choice_renders_predefined_card(self):
        class Adapter:
            async def pending_document_choices(self, scope):
                self.scope = scope
                return {
                    "operations": [
                        {
                            "operationId": "a" * 24,
                            "fileName": "document.docx",
                            "expiresAt": "2026-07-28T12:00:00Z",
                            "expiresAtUnix": time.time() + 300,
                        }
                    ]
                }

        adapter = Adapter()
        with patch.dict(
            os.environ,
            {
                "API_SERVER_KEY": "secret",
                "WORKER_ID": "hermes2",
            },
        ):
            activity = asyncio.run(
                pending_document_card_activity(
                    adapter=adapter,
                    conversation_id="conversation-1",
                    invoking_user_id="user-1",
                )
            )

        actions = activity.suggested_actions.actions
        self.assertIn("usually releases the lock within an hour", activity.text)
        self.assertEqual(
            [
                action.value["documentActionChoice"]
                for action in actions
            ],
            ["background", "copy"],
        )
        self.assertTrue(
            all(action.type == "Action.Submit" for action in actions)
        )

    def test_document_result_uses_accessible_link_message(self):
        activity = document_result_activity(
            {
                "status": "completed_copy",
                "contentType": (
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                "driveItem": {
                    "name": "workbook.xlsx",
                    "webUrl": "https://contoso/workbook",
                },
            }
        )

        self.assertEqual(activity.attachments, None)
        self.assertIn("https://contoso/workbook", activity.text)

    def test_background_card_action_schedules_document_retry(self):
        sent = []

        class Context:
            activity = ns(
                value=None,
                conversation=ns(
                    id="conversation-1",
                    conversation_type="personal",
                ),
                from_property=ns(id="user-1"),
            )

            async def send_activity(self, activity):
                sent.append(activity)
                return {"id": "activity-1"}

        class Adapter:
            runtime_kind = "hermes"

            async def start_document_background(self, **kwargs):
                self.started = kwargs
                return {
                    "status": "scheduled",
                    "attempt": 0,
                    "nextAttemptUnix": time.time() + 60,
                }

        adapter = Adapter()
        context = Context()
        with patch.dict(
            os.environ,
            {
                "API_SERVER_KEY": "secret",
                "WORKER_ID": "hermes2",
            },
        ):
            token = create_action_token(
                operation_id="a" * 24,
                operation_scope="scope-1",
                user_id="user-1",
                conversation_id="conversation-1",
                expires_at_unix=time.time() + 300,
            )
            context.activity.value = {
                "documentActionToken": token,
                "documentActionChoice": "background",
            }
            with (
                patch.object(
                    bridge_app,
                    "runtime_adapter",
                    return_value=adapter,
                ),
                patch.object(
                    bridge_app,
                    "agent_auth_context",
                    return_value=AgentAuthContext(
                        selected_mode="agent_identity",
                        available_modes=("agent_identity",),
                        conversation_boundary="one_to_one",
                    ),
                ),
                patch.object(
                    bridge_app,
                    "delivery_reference_metadata",
                    return_value={
                        "boundary": "one_to_one",
                        "conversation": {},
                    },
                ),
                patch.object(
                    bridge_app.schedule_sender,
                    "schedule_document_retry",
                    return_value={"sequenceNumber": 42},
                ) as schedule,
            ):
                handled = asyncio.run(
                    handle_document_card_action(context)
                )

        self.assertTrue(handled)
        self.assertEqual(
            adapter.started["recipient_identifier"],
            "user-1",
        )
        schedule.assert_called_once()
        self.assertIn("24 hours", sent[-1])

    def test_suggested_action_invoke_returns_explicit_success(self):
        class Context:
            activity = ns(
                value={},
                name="suggestedAction/submit",
            )
            turn_state = {}

        context = Context()
        asyncio.run(
            handle_teams_invoke(
                context,
                SimpleNamespace(),
            )
        )

        response = context.turn_state[
            bridge_app.TurnContext._INVOKE_RESPONSE_KEY
        ]
        self.assertEqual(response.value["status"], 200)
        self.assertEqual(
            response.value["body"]["status"],
            "ignored",
        )

    def test_attachment_final_response_uses_proactive_continuation(self):
        ctx = SimpleNamespace(activity=object())
        proactive = AsyncMock(
            return_value={"activityId": "activity-final"}
        )
        direct = AsyncMock()
        invoke_runtime = AsyncMock(
            return_value=AgentResponse(text="Completed", raw={})
        )
        attachment = SimpleNamespace(
            metadata=lambda: {"name": "form.docx"}
        )
        auth = AgentAuthContext(
            selected_mode="agent_identity",
            available_modes=("agent_identity", "agent_user"),
            conversation_boundary="one_to_one",
        )

        with (
            patch.object(
                bridge_app,
                "agent_auth_context",
                return_value=auth,
            ),
            patch.object(
                bridge_app,
                "delivery_reference_metadata",
                return_value={"conversationId": "conversation-1"},
            ),
            patch.object(
                bridge_app,
                "invoke_agent_runtime",
                invoke_runtime,
            ),
            patch.object(
                bridge_app,
                "send_proactive_activity",
                proactive,
            ),
            patch.object(
                bridge_app,
                "send_teams_response",
                direct,
            ),
            patch.object(
                bridge_app,
                "teams_runtime_source",
                return_value="teams_personal",
            ),
            patch.object(
                bridge_app,
                "user_id",
                return_value="user-1",
            ),
            patch.object(
                bridge_app,
                "teams_conversation_type",
                return_value="personal",
            ),
            patch.object(
                bridge_app,
                "supports_typing_indicators",
                return_value=False,
            ),
        ):
            asyncio.run(
                bridge_app.run_agent_runtime_for_teams(
                    ctx,
                    conversation_id="conversation-1",
                    session_key="teams:personal:conversation-1",
                    message="Fill the form",
                    attachments=[attachment],
                )
            )

        proactive.assert_awaited_once_with(
            bridge_app.agent365_adapter,
            {"conversationId": "conversation-1"},
            "Completed",
        )
        direct.assert_not_awaited()

    def test_word_notification_prompt_preserves_stable_document_context(self):
        activity = Activity(
            type="message",
            id="activity-1",
            text="Please review the deadline.",
            channel_id=ChannelId(
                channel="agents",
                sub_channel="word",
            ),
            from_property=ChannelAccount(
                id="user-1",
                name="Adele",
            ),
            entities=[
                Entity(
                    type="wpxComment",
                    documentId="document-1",
                    commentId="comment-1",
                )
            ],
        )

        identifier, prompt = notification_prompt(
            AgentNotificationActivity(activity),
            "word",
        )

        self.assertEqual(identifier, "comment-1")
        self.assertIn("Document id: document-1", prompt)
        self.assertIn("Comment id: comment-1", prompt)
        self.assertIn("Please review the deadline.", prompt)
        self.assertIn("private, untrusted data", prompt)
        self.assertIn("primary review surface", prompt)
        self.assertIn("exact proposed text", prompt)
        self.assertIn("document was not changed", prompt)
        self.assertIn(
            (
                f"mention {bridge_app.runtime_display_name()} again "
                'with "retry original"'
            ),
            prompt,
        )

    def test_word_notification_handler_supplies_operation_scope(self):
        activity = Activity(
            type="message",
            id="activity-1",
            text="Fill this section.",
            channel_id=ChannelId(
                channel="agents",
                sub_channel="word",
            ),
            from_property=ChannelAccount(
                id="user-1",
                name="Adele",
            ),
            entities=[
                Entity(
                    type="wpxComment",
                    documentId="document-1",
                    commentId="comment-1",
                )
            ],
        )
        notification = AgentNotificationActivity(activity)
        captured = {}

        async def invoke(**kwargs):
            captured.update(kwargs)
            return AgentResponse(text="done", raw={})

        with (
            patch.object(
                bridge_app,
                "invoke_agent_runtime",
                side_effect=invoke,
            ),
            patch.object(
                bridge_app,
                "agent_auth_context",
                return_value=AgentAuthContext(
                    selected_mode="agent_identity",
                    available_modes=("agent_identity",),
                    conversation_boundary="unknown",
                ),
            ),
        ):
            asyncio.run(
                bridge_app.handle_agent_notification(
                    SimpleNamespace(activity=activity),
                    notification,
                    "word",
                )
            )

        self.assertIn(
            "Private document operation scope",
            captured["message"],
        )
        self.assertIn(
            office_operation_scope(
                "notification:word:comment-1",
                "user-1",
            ),
            captured["message"],
        )

    def test_operator_invoke_propagates_persistence_disabled_boundary(self):
        request = InvokeRequest.model_validate(
            {
                "conversationId": "private-smoke",
                "message": "validate",
                "persistenceDisabled": True,
            }
        )
        invoke_runtime = AsyncMock(
            return_value=AgentResponse(text="ok", raw={})
        )

        with patch.object(
            bridge_app,
            "invoke_agent_runtime",
            invoke_runtime,
        ):
            response = asyncio.run(bridge_app.invoke(request))

        self.assertEqual(response.response, "ok")
        self.assertEqual(
            invoke_runtime.await_args.kwargs["metadata"],
            {"persistenceDisabled": True},
        )

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
