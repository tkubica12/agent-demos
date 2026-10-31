import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

RUNTIME_DIR = Path(__file__).resolve().parents[1] / "runtimes" / "hermes"
sys.path.insert(0, str(RUNTIME_DIR))

import native_learning  # noqa: E402
from blueprint import role_skill_hashes  # noqa: E402
from native_learning import (  # noqa: E402
    PRIVATE_SESSION_PREFIX,
    LearningConflictError,
    LearningDecisionError,
    decide_pending,
    learning_digest,
    private_turn_directive,
    record_share_choice,
    register_attachment_privacy,
    undo_entry,
)
from tests.native_hermes_fakes import (  # noqa: E402
    FakeSkillLedger,
    FakeSkillManager,
    FakeWriteApproval,
    skill_text,
    write_profile,
)


class NativeLearningTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.profile = Path(directory.name) / "profile"
        self.manifest = write_profile(self.profile, {"status-report": "Report status weekly."})
        self.ledger = FakeSkillLedger(self.profile)
        self.approvals = FakeWriteApproval(strict=False)
        self.manager = FakeSkillManager(self.profile, self.ledger)
        self.role_skill = self.profile / "skills" / "role" / "status-report" / "SKILL.md"

    def digest(self, **kwargs):
        return learning_digest(self.profile, ledger=self.ledger, approvals=self.approvals, **kwargs)

    def patch_role(self, text="Report status weekly with blockers first.", *, actor="agent"):
        return self.ledger.record(
            "patch", "status-report", self.role_skill.parent,
            lambda: self.role_skill.write_text(skill_text("status-report", text), encoding="utf-8"),
            actor=actor,
        )

    def create_skill(self, namespace, name, body, *, actor="curator"):
        root = self.profile / "skills" / namespace / name
        return self.ledger.record(
            "create", name, root,
            lambda: (root.mkdir(parents=True), (root / "SKILL.md").write_text(skill_text(name, body), encoding="utf-8")),
            actor=actor,
        )

    def test_digest_shows_native_changes_with_origin_namespace_diff_and_undo(self):
        self.create_skill("private", "acme-escalation", "Call the Acme duty manager.")
        self.patch_role()
        digest = self.digest()

        latest, earlier = digest["changes"]
        self.assertEqual(latest["skill"], "status-report")
        self.assertEqual((latest["namespace"], latest["origin"], latest["action"]), ("role", "foreground", "patch"))
        self.assertIn("+Report status weekly with blockers first.", latest["diff"])
        self.assertIn("-Report status weekly.", latest["diff"])
        self.assertTrue(latest["undoable"])
        self.assertEqual((earlier["namespace"], earlier["origin"]), ("private", "background"))
        self.assertEqual(digest["governed"][0]["artifactPath"], "skills/role/status-report")
        self.assertIsNone(digest["governed"][0]["choice"])
        self.assertTrue(digest["governed"][0]["traceComplete"])
        self.assertEqual(digest["newCount"], 3)
        self.assertFalse(digest["strictMode"])

        self.digest(mark_presented=True)
        self.assertEqual(self.digest()["newCount"], 0)
        self.create_skill("candidates", "deadline-check", "Confirm the timezone of every deadline.")
        fresh = self.digest()
        self.assertEqual(fresh["newCount"], 2)
        self.assertEqual([item["new"] for item in fresh["changes"]], [True, False, False])

    def test_undo_uses_native_rollback_writes_receipt_and_is_idempotent(self):
        entry_id = self.patch_role()
        receipt = undo_entry(self.profile, entry_id=entry_id, decided_by="owner-1", ledger=self.ledger)

        self.assertFalse(receipt["duplicate"])
        self.assertEqual(role_skill_hashes(self.profile), self.manifest["roleSkillBaseline"])
        self.assertEqual(
            [entry["action"] for entry in self.ledger.list_entries()], ["rollback", "pre-rollback", "patch"],
        )
        self.assertEqual(self.ledger.list_entries()[0]["actor"], "user")
        stored = json.loads((self.profile / "learning" / "receipts" / f"undo-{entry_id}.json").read_text())
        self.assertEqual((stored["kind"], stored["decidedBy"]), ("undo", "owner-1"))

        again = undo_entry(self.profile, entry_id=entry_id, decided_by="owner-1", ledger=self.ledger)
        self.assertTrue(again["duplicate"])
        self.assertEqual(len(self.ledger.entries), 3)
        undone = next(item for item in self.digest()["changes"] if item["entryId"] == entry_id)
        self.assertFalse(undone["undoable"])
        self.assertEqual(undone["undoReceipt"]["receiptId"], f"undo-{entry_id}")

    def test_undo_fails_closed_when_a_newer_change_or_unledgered_edit_exists(self):
        first = self.patch_role("First change.")
        self.patch_role("Second change.")
        with self.assertRaisesRegex(LearningConflictError, "changed again"):
            undo_entry(self.profile, entry_id=first, decided_by="owner-1", ledger=self.ledger)
        latest = self.ledger.list_entries()[0]["id"]
        self.role_skill.write_text(skill_text("status-report", "Edited without the ledger."), encoding="utf-8")
        with self.assertRaisesRegex(LearningConflictError, "changed again"):
            undo_entry(self.profile, entry_id=latest, decided_by="owner-1", ledger=self.ledger)
        self.assertIn("Edited without the ledger.", self.role_skill.read_text(encoding="utf-8"))
        with self.assertRaises(LearningDecisionError):
            undo_entry(self.profile, entry_id="not-an-id", decided_by="owner-1", ledger=self.ledger)

    def test_strict_mode_pending_writes_are_approved_or_rejected_natively(self):
        self.approvals.strict = True
        approve_id = self.approvals.stage(
            {"action": "patch", "name": "status-report", "old_string": "Report status weekly.",
             "new_string": "Report status weekly with risks."},
            summary="patch 'status-report' SKILL.md (+1/-1 lines)",
        )
        reject_id = self.approvals.stage(
            {"action": "create", "name": "deadline-check", "category": "candidates",
             "content": skill_text("deadline-check", "Confirm timezones.")},
            summary="create 'deadline-check'", origin="foreground",
        )
        digest = self.digest()
        self.assertTrue(digest["strictMode"])
        by_id = {item["pendingId"]: item for item in digest["pending"]}
        self.assertEqual(by_id[approve_id]["namespace"], "role")
        self.assertEqual(by_id[reject_id]["artifactPath"], "skills/candidates/deadline-check")
        self.assertIn("+Report status weekly with risks.", by_id[approve_id]["diff"])

        approved = decide_pending(
            self.profile, pending_id=approve_id, approve=True, decided_by="owner-1",
            approvals=self.approvals, skill_manager=self.manager, ledger=self.ledger,
        )
        rejected = decide_pending(
            self.profile, pending_id=reject_id, approve=False, decided_by="owner-1",
            approvals=self.approvals, skill_manager=self.manager, ledger=self.ledger,
        )
        self.assertIn("with risks", self.role_skill.read_text(encoding="utf-8"))
        self.assertEqual(approved["ledgerEntryIds"], [self.ledger.list_entries()[0]["id"]])
        self.assertEqual(self.ledger.list_entries()[0]["actor"], "user")
        self.assertFalse(rejected["approved"])
        self.assertFalse((self.profile / "skills" / "candidates" / "deadline-check").exists())
        self.assertEqual(self.approvals.records, {})
        self.assertTrue(decide_pending(
            self.profile, pending_id=approve_id, approve=True, decided_by="owner-1",
            approvals=self.approvals, skill_manager=self.manager, ledger=self.ledger,
        )["duplicate"])

    def test_failed_native_apply_keeps_the_pending_write(self):
        pending_id = self.approvals.stage(
            {"action": "patch", "name": "status-report", "old_string": "missing", "new_string": "x"},
            summary="patch",
        )
        with self.assertRaisesRegex(LearningConflictError, "Could not find a match"):
            decide_pending(
                self.profile, pending_id=pending_id, approve=True, decided_by="owner-1",
                approvals=self.approvals, skill_manager=self.manager, ledger=self.ledger,
            )
        self.assertIn(pending_id, self.approvals.records)
        self.assertFalse((self.profile / "learning" / "receipts" / f"pending-{pending_id}.json").exists())

    def test_share_marks_current_content_and_later_change_requires_a_new_choice(self):
        self.patch_role()
        governed = self.digest()["governed"][0]
        receipt = record_share_choice(
            self.profile, skill="skills/role/status-report", choice="share", decided_by="owner-1",
            artifact_hash=governed["artifactHash"], ledger=self.ledger,
        )
        self.assertEqual(receipt["ledgerEntryIds"], [self.ledger.list_entries()[0]["id"]])
        self.assertEqual(self.digest()["governed"][0]["choice"]["current"], True)
        self.assertTrue(record_share_choice(
            self.profile, skill="skills/role/status-report", choice="share", decided_by="owner-1",
            artifact_hash=governed["artifactHash"], ledger=self.ledger,
        )["duplicate"])
        self.patch_role("Changed after sharing.")
        self.assertEqual(self.digest()["governed"][0]["choice"]["current"], False)
        with self.assertRaisesRegex(LearningConflictError, "changed after the digest"):
            record_share_choice(
                self.profile, skill="skills/role/status-report", choice="discard", decided_by="owner-1",
                artifact_hash=governed["artifactHash"], ledger=self.ledger,
            )

    def test_keep_converts_role_change_to_private_playbook_and_restores_baseline(self):
        self.patch_role("Report status weekly for the Contoso steering group.")
        receipt = record_share_choice(
            self.profile, skill="skills/role/status-report", choice="keep", decided_by="owner-1",
            ledger=self.ledger, skill_manager=self.manager,
        )
        playbook = self.profile / "skills" / "private" / "status-report-playbook" / "SKILL.md"
        self.assertEqual(receipt["privatePlaybook"], "skills/private/status-report-playbook")
        self.assertIn("name: status-report-playbook", playbook.read_text(encoding="utf-8"))
        self.assertIn("Contoso steering group", playbook.read_text(encoding="utf-8"))
        self.assertEqual(role_skill_hashes(self.profile), self.manifest["roleSkillBaseline"])
        self.assertEqual(self.digest()["governed"], [])
        self.assertEqual(self.manager.applied[0]["category"], "private")

    def test_keep_and_discard_remove_candidates_through_native_rollback(self):
        self.create_skill("candidates", "deadline-check", "Confirm timezones.")
        self.create_skill("candidates", "risk-log", "Log risks.")
        kept = record_share_choice(
            self.profile, skill="skills/candidates/deadline-check", choice="keep", decided_by="owner-1",
            ledger=self.ledger, skill_manager=self.manager,
        )
        discarded = record_share_choice(
            self.profile, skill="skills/candidates/risk-log", choice="discard", decided_by="owner-1",
            ledger=self.ledger,
        )
        self.assertFalse((self.profile / "skills" / "candidates" / "deadline-check").exists())
        self.assertFalse((self.profile / "skills" / "candidates" / "risk-log").exists())
        self.assertTrue((self.profile / "skills" / "private" / "deadline-check-playbook" / "SKILL.md").is_file())
        self.assertEqual(len(kept["rolledBackEntryIds"]), 1)
        self.assertEqual(len(discarded["rolledBackEntryIds"]), 1)
        self.assertTrue(record_share_choice(
            self.profile, skill="skills/candidates/risk-log", choice="discard", decided_by="owner-1",
            artifact_hash=discarded["artifactHash"], ledger=self.ledger,
        )["duplicate"])

    def test_discard_fails_closed_without_a_complete_native_trace(self):
        self.role_skill.write_text(skill_text("status-report", "Edited outside Hermes tools."), encoding="utf-8")
        governed = self.digest()["governed"][0]
        self.assertFalse(governed["traceComplete"])
        self.assertIn("native ledger does not trace", governed["traceReason"])
        for choice in ("share", "discard"):
            with self.subTest(choice=choice), self.assertRaisesRegex(LearningConflictError, "native ledger"):
                record_share_choice(
                    self.profile, skill="skills/role/status-report", choice=choice, decided_by="owner-1",
                    ledger=self.ledger,
                )
        self.assertIn("Edited outside Hermes tools.", self.role_skill.read_text(encoding="utf-8"))

    def test_share_rejects_private_namespace_and_unknown_choices(self):
        for skill, choice in (("skills/private/acme", "share"), ("skills/role/status-report", "publish")):
            with self.subTest(skill=skill), self.assertRaises(LearningDecisionError):
                record_share_choice(self.profile, skill=skill, choice=choice, decided_by="owner-1", ledger=self.ledger)

    def test_private_sessions_veto_durable_writes_including_background_review(self):
        home = self.profile
        private = f"{PRIVATE_SESSION_PREFIX}teams_personal:abc:20261031T08"
        for tool, args in (
            ("memory", {"action": "add", "content": "Attachment fact"}),
            ("skill_manage", {"action": "create", "name": "doc-facts"}),
            ("write_file", {"path": str(home / "skills" / "private" / "x" / "SKILL.md"), "content": "x"}),
            ("patch", {"mode": "patch", "patch": f"*** Begin Patch\n*** Update File: {home / 'memories' / 'MEMORY.md'}\n"}),
        ):
            with self.subTest(tool=tool):
                directive = private_turn_directive(tool_name=tool, args=args, session_id=private, hermes_home=home)
                self.assertEqual(directive["action"], "block")
                self.assertIsNone(private_turn_directive(
                    tool_name=tool, args=args, session_id="teams_personal:abc:20261031T08", hermes_home=home,
                ))
        self.assertIsNone(private_turn_directive(
            tool_name="write_file", args={"path": str(home / "workspace" / "summary.md")},
            session_id=private, hermes_home=home,
        ))
        self.assertIsNone(private_turn_directive(tool_name="read_file", args={}, session_id=private, hermes_home=home))

        registered = []
        register_attachment_privacy(SimpleNamespace(register_hook=lambda name, callback: registered.append((name, callback))))
        self.assertEqual(registered, [("pre_tool_call", native_learning.private_turn_directive)])

    def test_bridge_and_runtime_agree_on_the_private_session_marker(self):
        from bridge.runtime import hermes as bridge_hermes

        self.assertEqual(bridge_hermes.PRIVATE_SESSION_PREFIX, PRIVATE_SESSION_PREFIX)
        self.assertTrue(native_learning.private_session(bridge_hermes.private_session_id("x" * 300)))
        self.assertEqual(len(bridge_hermes.private_session_id("x" * 300)), 256)

    def test_claim_blocks_concurrent_decisions_until_stale(self):
        entry_id = self.patch_role()
        claim = self.profile / "learning" / "receipts" / f"undo-{entry_id}.claim"
        claim.parent.mkdir(parents=True)
        claim.touch()
        with self.assertRaisesRegex(LearningConflictError, "in progress"):
            undo_entry(self.profile, entry_id=entry_id, decided_by="owner-1", ledger=self.ledger)
        old = claim.stat().st_mtime - native_learning.CLAIM_STALE_SECONDS - 1
        os.utime(claim, (old, old))
        self.assertFalse(undo_entry(self.profile, entry_id=entry_id, decided_by="owner-1", ledger=self.ledger)["duplicate"])
        self.assertFalse(claim.exists())


if __name__ == "__main__":
    unittest.main()
