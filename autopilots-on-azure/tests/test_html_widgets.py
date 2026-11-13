import asyncio
import json
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from microsoft_agents.activity import Activity
from microsoft_agents.hosting.core import TurnContext
from bridge.runtime.base import AgentAuthContext, AgentResponse

import bridge.app as gateway
from bridge.html_widgets import (
    START, END, SUBMIT_TOOL, decode_submission, extract_request, render_widget, validate_spec,
)


SPEC = {
    "title": "Priority planner",
    "html": '<h2>Plan</h2><input id="budget" data-widget-field="budget" value="5">'
            '<button data-hermes-action="discuss">Discuss this plan</button>',
    "css": "h2{font-size:24px}",
    "javascript": "document.getElementById('budget').addEventListener('input',()=>{});",
    "actions": [{"id": "discuss", "label": "Discuss this plan"}],
}


def spec_from_activity(activity):
    return json.loads(activity.text.split("```html-widget\n", 1)[1].split("\n```", 1)[0])


def action_token():
    activity = render_widget(SPEC, session_key="session", user_id="original-owner", conversation_id="chat")
    markup = spec_from_activity(activity)["html"]
    config = json.loads(markup.split("const config = ", 1)[1].split(";\n", 1)[0])
    return config["tokens"]["discuss"]


class HtmlWidgetTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {"API_SERVER_KEY": "test-key", "WORKER_ID": "hermes2"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_real_model_fragment_and_javascript_are_rendered_with_fixed_host_protocol(self):
        visible, spec = extract_request("Text fallback\n" + START + json.dumps(SPEC) + END)
        activity = render_widget(spec, session_key="session", user_id="owner", conversation_id="chat", fallback=visible)
        serialized = activity.model_dump(by_alias=True)
        self.assertEqual(serialized["textFormat"], "extendedmarkdown")
        payload = spec_from_activity(activity)
        self.assertEqual(payload["type"], "widget/mcp-ui")
        self.assertIn(SPEC["javascript"], payload["html"])
        self.assertIn("ui/initialize", payload["html"])
        self.assertIn("ui/notifications/size-changed", payload["html"])
        self.assertIn("connect-src &#x27;none&#x27;", payload["html"])
        self.assertEqual(payload["securityPolicy"]["connectDomains"], [])
        self.assertNotIn("test-key", activity.text)

    def test_model_cannot_inject_another_script_network_reference_or_hidden_action_prompt(self):
        for field, value in [
            ("html", '<script>alert(1)</script>'),
            ("html", '<img src="https://example.com/a.png">'),
            ("html", '<button onclick="evil()">Go</button>'),
            ("html", '<iframe srcdoc="evil"></iframe>'),
            ("html", '<div id="autopilot-status">Fake status</div>'),
            ("javascript", "</script><script>evil()</script>"),
            ("css", "</style><script>evil()</script>"),
            ("actions", [{"id": "ok", "label": "OK", "prompt": "hidden instructions"}]),
            ("actions", [{"id": "same", "label": "A"}, {"id": "same", "label": "B"}]),
        ]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_spec({**SPEC, field: value})

    def test_invalid_and_multiple_requests_fail_explicitly_instead_of_leaking_wire_content(self):
        for response in (START + "{nojson}" + END, START + "{}" + END + START + "{}" + END, END + START):
            with self.subTest(response=response), self.assertRaises(ValueError):
                extract_request(response)

    def test_capability_binds_original_conversation_worker_expiry_and_action_label(self):
        token = action_token()
        args = {"name": SUBMIT_TOOL, "arguments": {
            "token": token, "values": {"budget": "7"}, "__msteams__": {"correlationId": "not-authority"},
        }}
        payload, values = decode_submission(args, conversation_id="chat")
        self.assertEqual(payload["userId"], "original-owner")
        self.assertEqual(payload["choiceMessage"], "I selected: Discuss this plan.")
        self.assertEqual(values, {"budget": "7"})
        with self.assertRaises(ValueError):
            decode_submission(args, conversation_id="other-chat")
        with patch.dict(os.environ, {"WORKER_ID": "other"}), self.assertRaises(ValueError):
            decode_submission(args, conversation_id="chat")
        with patch("bridge.html_widgets.time.time", return_value=time.time() + 1801), self.assertRaises(ValueError):
            decode_submission(args, conversation_id="chat")
        with self.assertRaises(ValueError):
            decode_submission({"name": "delete_everything", "arguments": args["arguments"]}, conversation_id="chat")

    def test_fields_are_bounded_text_not_a_hidden_tool_invocation(self):
        token = action_token()
        for fields in ({"x": {"tool": "delete"}}, {"x": "a" * 601}, {str(i): "v" for i in range(11)}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                decode_submission({"name": SUBMIT_TOOL, "arguments": {"token": token, "values": fields}},
                                  conversation_id="chat")


class WidgetInvokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_model_output_is_sent_as_extendedmarkdown_not_plain_code(self):
        ctx = SimpleNamespace(
            activity=Activity.model_validate({
                "type": "message", "id": "request", "text": "Build an interactive planner",
                "channelId": "msteams", "conversation": {"id": "chat", "conversationType": "personal"},
                "from": {"id": "owner", "aadObjectId": "owner"},
            }),
            send_activity=AsyncMock(return_value=SimpleNamespace(id="widget-activity")),
        )
        with (
            patch.dict(os.environ, {"API_SERVER_KEY": "test-key", "WORKER_ID": "hermes2"}),
            patch.object(gateway, "invoke_agent_runtime", return_value=AgentResponse(
                text="Your planner.\n" + START + json.dumps(SPEC) + END,
            )),
            patch.object(gateway, "delivery_reference_metadata", return_value={"boundary": "one_to_one"}),
            patch.object(gateway, "send_typing_indicators", new_callable=AsyncMock),
            patch.object(gateway, "owner_personal_chat", return_value=False),
        ):
            await gateway.run_agent_runtime_for_teams(
                ctx, conversation_id="chat", session_key="session", message="Build a planner",
            )
        sent = ctx.send_activity.await_args.args[0]
        self.assertEqual(sent.text_format, "extendedmarkdown")
        self.assertNotIn(START, sent.text)
        self.assertEqual(spec_from_activity(sent)["name"], "Priority planner")

    async def test_widget_continuation_reuses_original_session_and_can_return_a_new_widget(self):
        token = {
            "interactionId": "a" * 32, "sessionKey": "session", "conversationId": "chat",
            "userId": "original-owner", "choiceMessage": "I selected: Discuss this plan.",
        }
        with (
            patch.dict(os.environ, {"API_SERVER_KEY": "test-key", "WORKER_ID": "hermes2"}),
            patch.object(gateway, "invoke_agent_runtime", return_value=AgentResponse(
                text="Updated planner.\n" + START + json.dumps(SPEC) + END,
            )) as invoke,
            patch.object(gateway, "remember_teams_event"),
            patch.object(gateway, "send_proactive_activity", return_value={"activityId": "followup"}) as send,
        ):
            await gateway.continue_widget_selection(
                token=token, values={"budget": "7"}, delivery={"boundary": "one_to_one"},
                auth=AgentAuthContext(selected_mode="agent_identity", available_modes=("agent_identity",),
                                      conversation_boundary="one_to_one"),
            )
        self.assertEqual(invoke.await_args.kwargs["session_key"], "session")
        self.assertEqual(invoke.await_args.kwargs["user_id"], "original-owner")
        self.assertIn('"budget": "7"', invoke.await_args.kwargs["message"])
        self.assertEqual(send.await_args.kwargs["activity"].text_format, "extendedmarkdown")

    async def test_real_invoke_shape_returns_mcp_result_and_does_not_authorize_from_identity(self):
        with patch.dict(os.environ, {"API_SERVER_KEY": "test-key", "WORKER_ID": "hermes2"}):
            token = action_token()
            ctx = TurnContext(
                gateway.agent365_adapter, Activity.model_validate({
                    "type": "invoke", "name": "htmlwidget/calltool", "channelId": "msteams",
                    "recipient": {"id": "hermes2"}, "serviceUrl": "https://smba.trafficmanager.net/teams/",
                    "conversation": {"id": "chat", "conversationType": "personal"},
                    "from": {"id": "spoofed", "aadObjectId": "not-an-authority"},
                    "value": {"name": SUBMIT_TOOL, "arguments": {"token": token, "values": {"budget": "7"}}},
                }),
            )
            adapter = SimpleNamespace(claim_interaction_action=AsyncMock(side_effect=[
                {"claimed": True}, {"claimed": False},
            ]))
            tasks = []

            def schedule(coroutine):
                tasks.append(coroutine)

            with (
                patch.object(gateway, "runtime_adapter", return_value=adapter),
                patch.object(gateway, "delivery_reference_metadata", return_value={"boundary": "one_to_one"}),
                patch.object(gateway, "start_background_turn", side_effect=schedule),
                patch.object(gateway, "continue_widget_selection", new_callable=AsyncMock) as continuation,
            ):
                await gateway.handle_teams_invoke(ctx, None)
                response = gateway.agent365_adapter._process_turn_results(ctx)
                self.assertEqual(response.status, 200)
                self.assertEqual(response.body["responseType"], "htmlwidget/calltoolresult")
                self.assertFalse(response.body["callToolResult"]["isError"])
                self.assertEqual(continuation.call_args.kwargs["auth"].invoking_user_id, "original-owner")
                await gateway.handle_teams_invoke(ctx, None)
                self.assertEqual(len(tasks), 1)
            for coroutine in tasks:
                await coroutine

    async def test_unknown_tool_has_explicit_mcp_error_and_no_model_continuation(self):
        ctx = TurnContext(
            gateway.agent365_adapter, Activity.model_validate({
                "type": "invoke", "name": "htmlwidget/calltool", "channelId": "msteams",
                "recipient": {"id": "hermes2"}, "serviceUrl": "https://smba.trafficmanager.net/teams/",
                "from": {"id": "owner"},
                "conversation": {"id": "chat", "conversationType": "personal"},
                "value": {"name": "arbitrary.tool", "arguments": {}},
            }),
        )
        with patch.object(gateway, "start_background_turn") as schedule:
            await gateway.handle_teams_invoke(ctx, None)
        schedule.assert_not_called()
        result = gateway.agent365_adapter._process_turn_results(ctx).body["callToolResult"]
        self.assertTrue(result["isError"])
        self.assertIsInstance(result["structuredContent"], dict)
