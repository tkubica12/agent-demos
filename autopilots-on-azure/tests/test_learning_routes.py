import asyncio
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

RUNTIME_DIR = Path(__file__).resolve().parents[1] / "runtimes" / "hermes"
sys.path.insert(0, str(RUNTIME_DIR))

from runtime_routes import register_routes  # noqa: E402
from tests.native_hermes_fakes import (  # noqa: E402
    FakeSkillLedger,
    FakeSkillManager,
    FakeWriteApproval,
    skill_text,
    write_profile,
)


class LearningRouteTests(unittest.TestCase):
    """The four owner-decision routes run against Hermes' native modules, supplied here through sys.modules."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.profile = Path(directory.name) / "profile"
        write_profile(self.profile, {"status-report": "Report status weekly."})
        self.ledger = FakeSkillLedger(self.profile)
        self.approvals = FakeWriteApproval()
        self.manager = FakeSkillManager(self.profile, self.ledger)
        tools = types.ModuleType("tools")
        modules = {
            "tools": tools,
            "tools.skill_ledger": self.ledger,
            "tools.write_approval": self.approvals,
            "tools.skill_manager_tool": self.manager,
        }
        native = patch.dict(sys.modules, modules)
        native.start()
        self.addCleanup(native.stop)
        environment = patch.dict(os.environ, {"API_SERVER_KEY": "runtime-key"})
        environment.start()
        self.addCleanup(environment.stop)
        skill = self.profile / "skills" / "role" / "status-report" / "SKILL.md"
        self.entry_id = self.ledger.record(
            "patch", "status-report", skill.parent,
            lambda: skill.write_text(skill_text("status-report", "Report status weekly with risks."), encoding="utf-8"),
            actor="curator",
        )
        self.pending_id = self.approvals.stage(
            {"action": "create", "name": "deadline-check", "category": "candidates",
             "content": skill_text("deadline-check", "Confirm timezones.")},
            summary="create 'deadline-check'",
        )

    def run_client(self, scenario):
        async def run():
            app = web.Application()
            register_routes(app, self.profile)
            async with TestClient(TestServer(app)) as client:
                return await scenario(client)

        return asyncio.run(run())

    def test_routes_require_the_runtime_key_and_return_native_digest(self):
        async def scenario(client):
            denied = await client.get("/internal/learning/digest")
            self.assertEqual(denied.status, 401)
            response = await client.get(
                "/internal/learning/digest?limit=5&markPresented=true", headers={"X-Autopilot-Key": "runtime-key"},
            )
            self.assertEqual(response.status, 200)
            digest = await response.json()
            self.assertEqual(digest["changes"][0]["entryId"], self.entry_id)
            self.assertEqual(digest["pending"][0]["pendingId"], self.pending_id)
            self.assertEqual(digest["governed"][0]["artifactPath"], "skills/role/status-report")
            again = await client.get("/internal/learning/digest", headers={"X-Autopilot-Key": "runtime-key"})
            self.assertEqual((await again.json())["newCount"], 0)
            invalid = await client.get("/internal/learning/digest?limit=0", headers={"X-Autopilot-Key": "runtime-key"})
            self.assertEqual(invalid.status, 400)

        self.run_client(scenario)

    def test_decisions_map_validation_and_conflicts_to_explicit_errors(self):
        headers = {"X-Autopilot-Key": "runtime-key"}

        async def scenario(client):
            approve = await client.post("/internal/learning/pending/decide", headers=headers, json={
                "pendingId": self.pending_id, "approve": True, "decidedBy": "owner-1",
            })
            self.assertEqual(approve.status, 200, await approve.text())
            self.assertTrue((self.profile / "skills" / "candidates" / "deadline-check" / "SKILL.md").is_file())
            missing_flag = await client.post("/internal/learning/pending/decide", headers=headers, json={
                "pendingId": self.pending_id, "decidedBy": "owner-1",
            })
            self.assertEqual(missing_flag.status, 400)

            undo = await client.post("/internal/learning/undo", headers=headers, json={
                "entryId": self.entry_id, "decidedBy": "owner-1",
            })
            self.assertEqual(undo.status, 200, await undo.text())
            self.assertEqual((await undo.json())["kind"], "undo")

            share = await client.post("/internal/learning/share", headers=headers, json={
                "skill": "skills/candidates/deadline-check", "choice": "share", "decidedBy": "owner-1",
                "artifactHash": "0" * 64,
            })
            self.assertEqual(share.status, 409)
            self.assertIn("changed after the digest", (await share.json())["detail"])
            discard = await client.post("/internal/learning/share", headers=headers, json={
                "skill": "skills/candidates/deadline-check", "choice": "discard", "decidedBy": "owner-1",
            })
            self.assertEqual(discard.status, 200, await discard.text())
            invalid = await client.post("/internal/learning/share", headers=headers, json={
                "skill": "skills/private/x", "choice": "share", "decidedBy": "owner-1",
            })
            self.assertEqual(invalid.status, 400)
            self.assertFalse((self.profile / "skills" / "candidates" / "deadline-check").exists())

        self.run_client(scenario)


if __name__ == "__main__":
    unittest.main()
