import asyncio
import json
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from bridge.runtime.base import AgentRequest
from bridge.runtime.hermes import HermesRuntimeAdapter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtimes" / "hermes"))
import cron_origin


class CronOriginTests(unittest.TestCase):
    def test_gateway_registers_exact_transcript_before_admission_without_inventory_diff(self):
        requests = []

        def handle(request):
            requests.append(request)
            if request.url.path == "/v1/runs" and request.method == "POST":
                return httpx.Response(202, json={"run_id": "run-1"})
            if request.url.path == "/v1/runs/run-1":
                return httpx.Response(200, json={"run_id": "run-1", "status": "completed", "output": "done"})
            if request.url.path == "/api/sessions":
                return httpx.Response(200, json={"data": []})
            return httpx.Response(200, json={"session": {}})

        adapter = HermesRuntimeAdapter(
            runtime_url_factory=lambda: "http://runtime",
            client_factory=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handle), **kwargs),
        )
        request = AgentRequest(
            prompt="remind me", conversation_id="teams:chat-a", user_id="owner", source="teams_personal",
            must_answer=True,
            metadata={"deliveryReference": {
                "referenceKey": "ref-a", "boundary": "one_to_one", "conversation": {},
            }},
        )
        with patch.dict(os.environ, {"API_SERVER_KEY": "test-key", "USER_SCHEDULING_ENABLED": "true"}):
            asyncio.run(adapter.invoke(request))
        origin = next(r for r in requests if r.url.path == "/internal/cron/session-origin")
        admission = next(r for r in requests if r.url.path == "/v1/runs")
        self.assertEqual(json.loads(origin.content)["sessionId"], json.loads(admission.content)["session_id"])
        self.assertLess(requests.index(origin), requests.index(admission))
        self.assertFalse(any(r.url.path in {
            "/internal/cron/jobs", "/internal/cron/bind-delivery", "/internal/cron/reconcile",
        } for r in requests))

    def test_two_conversations_get_atomic_native_origins_without_model_supplied_routing(self):
        bound = ContextVar("bound", default=None)
        native = SimpleNamespace(set_session_vars=lambda **kwargs: bound.set(kwargs))
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"HERMES_HOME": directory}),
            patch.dict(sys.modules, {"gateway.session_context": native}),
        ):
            home = Path(directory)
            for session, reference in (("session-a", "ref-a"), ("session-b", "ref-b")):
                cron_origin.register_origin(
                    home, session_id=session, canonical_session_id=session + "-tip", delivery_reference={
                    "referenceKey": reference, "boundary": "one_to_one", "conversation": {"id": reference},
                })

            def invoke(session):
                return cron_origin.tool_execution(
                    tool_name="cronjob_manage", session_id="compressed", task_id=session,
                    args={"action": "create", "deliver": "attacker-destination"},
                    next_call=lambda args: (bound.get()["chat_id"], args["deliver"]),
                )

            with ThreadPoolExecutor() as pool:
                self.assertEqual(list(pool.map(invoke, ["session-a-tip", "session-b-tip"])), [
                    ("ref-a", "local"), ("ref-b", "local"),
                ])
            self.assertIsNone(bound.get())
            with self.assertRaisesRegex(ValueError, "another delivery"):
                cron_origin.register_origin(home, session_id="session-a", delivery_reference={
                    "referenceKey": "ref-b", "boundary": "one_to_one", "conversation": {},
                })

    def test_unbound_turn_cannot_create_a_job_with_model_supplied_origin(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"HERMES_HOME": directory}):
            result = cron_origin.tool_execution(
                tool_name="cronjob_manage", args={"action": "create", "origin": "ref-a"},
                session_id="unregistered", next_call=lambda args: self.fail("Untrusted origin admitted"),
            )
        self.assertFalse(json.loads(result)["success"])
