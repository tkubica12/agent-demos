import asyncio
import json
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

import bridge.app as bridge_app
from bridge.learning_cards import (
    LEARNING_ACTION,
    LEARNING_ACTION_TOKEN,
    create_learning_action_token,
    decode_learning_action_token,
    learning_digest_card,
)
from bridge.runtime.base import AgentAuthContext, AgentResponse


OWNER = "4b1f6f5e-2f6c-4a7e-9d0c-1f2e3d4c5b6a"
CONVERSATION = "a:personal-owner-chat"
ENVIRONMENT = {"API_SERVER_KEY": "runtime-key", "WORKER_ID": "hermes2", "WORKER_OWNER_ID": OWNER.upper()}


def digest(new=True):
    return {
        "strictMode": True,
        "newCount": 4 if new else 0,
        "changes": [
            {"entryId": "abcdef123456", "skill": "status-report", "artifactPath": "skills/role/status-report",
             "namespace": "role", "origin": "background", "action": "patch", "ts": "2026-10-31T07:00:00+00:00",
             "diff": "--- a/x\n+++ b/x\n-Report weekly.\n+Report weekly with risks.", "diffTruncated": False,
             "undoable": True, "undoBlockedReason": None, "new": new},
            {"entryId": "bbbbbb123456", "skill": "acme-escalation", "artifactPath": "skills/private/acme-escalation",
             "namespace": "private", "origin": "foreground", "action": "create", "ts": "2026-10-30T07:00:00+00:00",
             "diff": "+Call the duty manager.", "diffTruncated": True, "undoable": False,
             "undoBlockedReason": "The skill changed again later; undo the newer change first.", "new": False},
        ],
        "pending": [
            {"pendingId": "1234abcd", "skill": "deadline-check", "namespace": "candidates",
             "summary": "create 'deadline-check'", "origin": "background_review", "diff": "+Confirm timezones.",
             "diffTruncated": False, "new": new},
        ],
        "governed": [
            {"artifactPath": "skills/role/status-report", "skill": "status-report", "namespace": "role",
             "artifactHash": "a" * 64, "choice": None, "traceComplete": True, "traceReason": None, "new": new},
            {"artifactPath": "skills/candidates/edited", "skill": "edited", "namespace": "candidates",
             "artifactHash": "b" * 64, "choice": None, "traceComplete": False,
             "traceReason": "The native ledger does not trace skills/candidates/edited back to the Role Release baseline.",
             "new": new},
        ],
    }


def actions(card):
    found = []

    def walk(value):
        if isinstance(value, dict):
            if value.get("type") == "Action.Execute":
                found.append((value["title"], value["data"][LEARNING_ACTION], value["data"][LEARNING_ACTION_TOKEN]))
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(card)
    return found


class FakeAdapter:
    def __init__(self, current=None):
        self.current = current or digest()
        self.calls = []
        self.reference = None
        self.failure = None

    async def remember_owner_delivery(self, reference):
        self.calls.append(("remember", reference))
        return {"referenceKey": "learning-owner-personal-chat"}

    async def learning_digest(self, *, mark_presented=False, limit=20):
        self.calls.append(("digest", mark_presented))
        return self.current

    async def owner_delivery_reference(self):
        return self.reference

    async def undo_learning(self, **kwargs):
        self.calls.append(("undo", kwargs))
        return {"action": "patch", "artifactPath": "skills/role/status-report", "duplicate": False}

    async def decide_learning_pending(self, **kwargs):
        self.calls.append(("pending", kwargs))
        return {"skill": "deadline-check", "duplicate": True}

    async def share_learning(self, **kwargs):
        self.calls.append(("share", kwargs))
        if self.failure:
            raise self.failure
        return {"artifactPath": kwargs["skill"], "privatePlaybook": "skills/private/status-report-playbook",
                "duplicate": False}


class Context:
    def __init__(self, *, text="", value=None, user=OWNER, conversation_type="personal"):
        self.activity = SimpleNamespace(
            type="message", text=text, value=value, name="adaptiveCard/action", id="activity-1",
            reply_to_id=None, attachments=None, entities=[],
            conversation=SimpleNamespace(id=CONVERSATION, conversation_type=conversation_type),
            from_property=SimpleNamespace(aad_object_id=user, name="Owner"),
            recipient=SimpleNamespace(id="bot", name="Hermes"),
        )
        self.sent = []
        self.turn_state = {}

    async def send_activity(self, activity):
        self.sent.append(activity)
        return SimpleNamespace(id=f"sent-{len(self.sent)}")


class LearningDigestBridgeTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, ENVIRONMENT)
        environment.start()
        self.addCleanup(environment.stop)
        self.reference = {"referenceKey": "teams-owner", "boundary": "one_to_one", "conversation": {}}
        for name, value in (
            ("delivery_reference_metadata", self.reference),
            ("agent_auth_context", AgentAuthContext(
                selected_mode="agent_identity", available_modes=("agent_identity",), conversation_boundary="one_to_one",
            )),
        ):
            patcher = patch.object(bridge_app, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(bridge_app._teams_diag.clear)

    def token(self, action, target, *, artifact_hash="", **overrides):
        return create_learning_action_token(
            action=action, target=target, artifact_hash=artifact_hash,
            user_id=overrides.get("user_id", OWNER), conversation_id=overrides.get("conversation_id", CONVERSATION),
            expires_at_unix=overrides.get("expires_at_unix"),
        )

    def test_tokens_are_typed_expiring_and_bound_to_owner_conversation_and_worker(self):
        payload = decode_learning_action_token(self.token("undo", "abcdef123456"), user_id=OWNER,
                                               conversation_id=CONVERSATION)
        self.assertEqual((payload["action"], payload["target"]), ("undo", "abcdef123456"))
        self.assertNotIn(OWNER, json.dumps(payload))
        for message, token, user, conversation in (
            ("Worker owner", self.token("undo", "abcdef123456"), "intruder", CONVERSATION),
            ("expired", self.token("undo", "abcdef123456", expires_at_unix=time.time() - 1), OWNER, CONVERSATION),
            ("another conversation", self.token("undo", "abcdef123456"), OWNER, "a:group"),
            ("invalid", "not-a-token", OWNER, CONVERSATION),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                decode_learning_action_token(token, user_id=user, conversation_id=conversation)
        with patch.dict(os.environ, {"WORKER_OWNER_ID": "someone-else"}), self.assertRaisesRegex(ValueError, "owner"):
            decode_learning_action_token(self.token("undo", "abcdef123456"), user_id=OWNER, conversation_id=CONVERSATION)
        token = self.token("undo", "abcdef123456")
        with patch.dict(os.environ, {"WORKER_ID": "other-worker"}), self.assertRaisesRegex(ValueError, "another Worker"):
            decode_learning_action_token(token, user_id=OWNER, conversation_id=CONVERSATION)
        with self.assertRaises(ValueError):
            self.token("publish", "skills/role/status-report")

    def test_digest_card_offers_undo_approval_and_export_choices_only_where_valid(self):
        card = learning_digest_card(digest(), user_id=OWNER, conversation_id=CONVERSATION)
        found = [(title, action) for title, action, _ in actions(card)]
        self.assertEqual(found, [
            ("Undo", "undo"), ("Approve", "approve"), ("Reject", "reject"),
            ("Share", "share"), ("Keep only for me", "keep"), ("Discard", "discard"),
            ("Keep only for me", "keep"), ("Discard", "discard"),
        ])
        serialized = json.dumps(card)
        self.assertIn("Undo unavailable: The skill changed again later", serialized)
        self.assertIn("Native trace incomplete", serialized)
        self.assertIn("Strict approval is on", serialized)
        self.assertIn("+Report weekly with risks.", serialized)
        self.assertLess(len(serialized.encode("utf-8")), 28_000)
        only_new = learning_digest_card(digest(), user_id=OWNER, conversation_id=CONVERSATION, only_new=True)
        self.assertNotIn("acme-escalation", json.dumps(only_new))
        crowded = digest()
        crowded["changes"] = crowded["changes"][:1] * 20
        crowded["pending"] = crowded["pending"] * 10
        crowded["governed"] = crowded["governed"][:1] * 10
        large = json.dumps(learning_digest_card(crowded, user_id=OWNER, conversation_id=CONVERSATION))
        self.assertLess(len(large.encode("utf-8")), 28_000)
        self.assertIn("15 more", large)

    def test_learning_command_shows_digest_only_to_the_owner_in_personal_chat(self):
        adapter = FakeAdapter()
        with patch.object(bridge_app, "runtime_adapter", return_value=adapter):
            owner = Context(text="/learning")
            asyncio.run(bridge_app.handle_teams_message(owner, SimpleNamespace()))
            intruder = Context(text="/learning", user="intruder")
            asyncio.run(bridge_app.handle_teams_message(intruder, SimpleNamespace()))
            group = Context(text="/learning", conversation_type="groupchat")
            with patch.object(bridge_app, "bot_is_mentioned", return_value=True):
                asyncio.run(bridge_app.handle_teams_message(group, SimpleNamespace()))
            with patch.dict(os.environ, {"WORKER_OWNER_ID": ""}):
                unconfigured = Context(text="/learning")
                asyncio.run(bridge_app.handle_teams_message(unconfigured, SimpleNamespace()))

        self.assertEqual(adapter.calls, [("remember", self.reference), ("digest", True)])
        card = owner.sent[0].attachments[0].content
        self.assertEqual(len(actions(card)), 8)
        self.assertIn("Only the Worker owner", intruder.sent[0])
        self.assertIn("personal chat", group.sent[0])
        self.assertIn("WORKER_OWNER_ID", unconfigured.sent[0])

    def run_owner_turn(self, adapter, *, user=OWNER, attachments=None):
        ctx = Context(text="Plan the week", user=user)
        with (
            patch.object(bridge_app, "runtime_adapter", return_value=adapter),
            patch.object(bridge_app, "invoke_agent_runtime", AsyncMock(return_value=AgentResponse(text="Done."))),
            patch.object(bridge_app, "send_teams_response", AsyncMock(return_value="reply-1")),
            patch.object(bridge_app, "send_proactive_activity", AsyncMock(return_value={"activityId": "p-1"})),
            patch.object(bridge_app, "pending_document_card_activity", AsyncMock(return_value=None)),
            patch.object(bridge_app, "supports_typing_indicators", return_value=False),
        ):
            asyncio.run(bridge_app.run_agent_runtime_for_teams(
                ctx, conversation_id=CONVERSATION, session_key=f"teams:personal:{CONVERSATION}",
                message="Plan the week", attachments=attachments,
            ))
        return ctx

    def test_owner_interaction_pulls_new_learning_after_the_reply(self):
        adapter = FakeAdapter()
        ctx = self.run_owner_turn(adapter)
        self.assertEqual(adapter.calls, [("remember", self.reference), ("digest", True)])
        self.assertEqual([title for title, _, _ in actions(ctx.sent[0].attachments[0].content)][:1], ["Undo"])

        quiet = FakeAdapter(digest(new=False))
        self.assertEqual(self.run_owner_turn(quiet).sent, [])
        other = FakeAdapter()
        self.assertEqual(self.run_owner_turn(other, user="colleague").sent, [])
        self.assertEqual(other.calls, [])
        private = FakeAdapter()
        self.run_owner_turn(private, attachments=[SimpleNamespace(metadata=lambda: {"name": "contract.docx"})])
        self.assertEqual(private.calls, [])

    def click(self, adapter, value, *, user=OWNER):
        ctx = Context(value=value, user=user)
        proactive = AsyncMock(return_value={"activityId": "p-1"})

        async def run():
            await bridge_app.handle_teams_invoke(ctx, SimpleNamespace())
            await asyncio.gather(*list(bridge_app.background_turns))

        with (
            patch.object(bridge_app, "runtime_adapter", return_value=adapter),
            patch.object(bridge_app, "send_proactive_activity", proactive),
        ):
            asyncio.run(run())
        return ctx.turn_state[bridge_app.TurnContext._INVOKE_RESPONSE_KEY].value["body"], proactive

    def test_owner_card_action_runs_native_decision_and_returns_refreshed_digest(self):
        adapter = FakeAdapter()
        body, proactive = self.click(adapter, {
            LEARNING_ACTION_TOKEN: self.token("undo", "abcdef123456"), LEARNING_ACTION: "undo",
        })
        self.assertEqual(body["statusCode"], 200)
        self.assertIn("Undo change abcdef123456", json.dumps(body["value"]))
        self.assertEqual(adapter.calls[0], ("undo", {"entry_id": "abcdef123456", "decided_by": OWNER}))
        self.assertEqual(adapter.calls[1], ("digest", True))
        sent = proactive.await_args.kwargs["activity"]
        self.assertEqual(sent.text, "Undone: patch of skills/role/status-report.")
        self.assertEqual(sent.attachments[0].content["body"][0]["text"], "What I learned")

        _, proactive = self.click(adapter, {"action": {"data": {
            LEARNING_ACTION_TOKEN: self.token("approve", "1234abcd"), LEARNING_ACTION: "approve",
        }}})
        self.assertEqual(adapter.calls[2], ("pending", {"pending_id": "1234abcd", "approve": True, "decided_by": OWNER}))
        self.assertTrue(proactive.await_args.kwargs["activity"].text.startswith("Already done earlier."))

        _, proactive = self.click(adapter, {
            LEARNING_ACTION_TOKEN: self.token("keep", "skills/role/status-report", artifact_hash="a" * 64),
            LEARNING_ACTION: "keep",
        })
        self.assertEqual(adapter.calls[4][1]["artifact_hash"], "a" * 64)
        self.assertIn("Private Playbook skills/private/status-report-playbook", proactive.await_args.kwargs["activity"].text)

    def test_runtime_conflict_is_reported_explicitly_to_the_owner(self):
        adapter = FakeAdapter()
        request = httpx.Request("POST", "https://runtime.test/internal/learning/share")
        adapter.failure = httpx.HTTPStatusError("409", request=request, response=httpx.Response(
            409, json={"detail": "skills/role/status-report changed after the digest was shown; review the new digest."},
            request=request,
        ))
        _, proactive = self.click(adapter, {
            LEARNING_ACTION_TOKEN: self.token("share", "skills/role/status-report", artifact_hash="a" * 64),
            LEARNING_ACTION: "share",
        })
        self.assertIn("could not apply that learning decision: skills/role/status-report changed after the digest",
                      proactive.await_args.kwargs["activity"].text)

    def test_non_owner_or_mismatched_action_is_rejected_before_any_runtime_call(self):
        adapter = FakeAdapter()
        body, proactive = self.click(adapter, {
            LEARNING_ACTION_TOKEN: self.token("discard", "skills/role/status-report"), LEARNING_ACTION: "discard",
        }, user="intruder")
        self.assertEqual(body["statusCode"], 400)
        self.assertIn("Worker owner", body["value"]["message"])
        body, _ = self.click(adapter, {
            LEARNING_ACTION_TOKEN: self.token("discard", "skills/role/status-report"), LEARNING_ACTION: "share",
        })
        self.assertEqual(body["statusCode"], 400)
        self.assertEqual(adapter.calls, [])
        proactive.assert_not_awaited()

    def test_after_dreaming_new_learning_is_pushed_to_the_owner_chat(self):
        adapter = FakeAdapter()
        proactive = AsyncMock(return_value={"activityId": "p-1"})
        with (
            patch.object(bridge_app, "runtime_adapter", return_value=adapter),
            patch.object(bridge_app, "send_proactive_activity", proactive),
            patch.object(bridge_app, "stored_conversation_id", return_value=CONVERSATION),
        ):
            self.assertEqual(asyncio.run(bridge_app.deliver_learning_digest_to_owner()),
                             {"status": "owner_chat_unknown"})
            adapter.reference = self.reference
            self.assertEqual(asyncio.run(bridge_app.deliver_learning_digest_to_owner()),
                             {"status": "sent", "newCount": 4})
            adapter.current = digest(new=False)
            self.assertEqual(asyncio.run(bridge_app.deliver_learning_digest_to_owner()), {"status": "no_news"})
        proactive.assert_awaited_once()
        self.assertIs(proactive.await_args.args[1], self.reference)
        card = proactive.await_args.kwargs["activity"].attachments[0].content
        token = actions(card)[0][2]
        self.assertEqual(
            decode_learning_action_token(token, user_id=OWNER, conversation_id=CONVERSATION)["target"], "abcdef123456",
        )


if __name__ == "__main__":
    unittest.main()
