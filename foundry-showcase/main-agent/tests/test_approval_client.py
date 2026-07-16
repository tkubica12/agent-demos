from __future__ import annotations

import unittest

from agent_framework import Content, Message

from main import ApprovalContinuationFoundryChatClient


class ApprovalContinuationFoundryChatClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = object.__new__(ApprovalContinuationFoundryChatClient)
        self.function_call = Content.from_function_call(
            call_id="approval-1",
            name="case-write___apply_case_update",
            arguments='{"proposal_id":"proposal-1"}',
        )

    def test_preserves_current_approval_response_on_continuation(self) -> None:
        approval = Content.from_function_approval_response(
            approved=True,
            id="approval-1",
            function_call=self.function_call,
        )

        prepared = self.client._prepare_messages_for_openai(
            [Message(role="user", contents=[approval])],
            request_uses_service_side_storage=True,
        )

        self.assertEqual(
            prepared,
            [
                {
                    "type": "mcp_approval_response",
                    "approval_request_id": "approval-1",
                    "approve": True,
                }
            ],
        )

    def test_drops_replayed_approval_response_on_continuation(self) -> None:
        approval = Content.from_function_approval_response(
            approved=True,
            id="approval-1",
            function_call=self.function_call,
        )

        prepared = self.client._prepare_messages_for_openai(
            [
                Message(
                    role="user",
                    contents=[approval],
                    additional_properties={"_attribution": "history"},
                )
            ],
            request_uses_service_side_storage=True,
        )

        self.assertEqual(prepared, [])


if __name__ == "__main__":
    unittest.main()
