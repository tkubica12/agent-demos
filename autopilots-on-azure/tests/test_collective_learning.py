import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

RUNTIME_DIR = Path(__file__).resolve().parents[1] / "runtimes" / "hermes"
sys.path.insert(0, str(RUNTIME_DIR))

import blueprint  # noqa: E402
from collective_learning import (  # noqa: E402
    CollectiveLearningError,
    approved_learning_packet,
    attest_learning_packet,
    attest_refresh_rejection,
    exported_artifact_hash,
    pending_learning_packet,
    prepare_learning_packet,
    prepare_refresh_rejection,
    worker_refresh_readiness,
)
from native_learning import decide_pending, record_share_choice  # noqa: E402
from tests.native_hermes_fakes import (  # noqa: E402
    ApprovalKey,
    FakeSkillLedger,
    FakeSkillManager,
    FakeWriteApproval,
    skill_text,
    write_profile,
)


class CollectiveLearningTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.key = ApprovalKey()
        environment = patch.dict(os.environ, {"COLLECTIVE_LEARNING_APPROVAL_PUBLIC_KEY": self.key.public_key})
        environment.start()
        self.addCleanup(environment.stop)

    def native(self, profile):
        ledger = FakeSkillLedger(profile)
        return ledger, FakeSkillManager(profile, ledger)

    def create_skill(self, ledger, profile, namespace, name, body):
        root = profile / "skills" / namespace / name
        ledger.record(
            "create", name, root,
            lambda: (root.mkdir(parents=True), (root / "SKILL.md").write_text(skill_text(name, body), encoding="utf-8")),
            actor="curator",
        )

    def patch_skill(self, ledger, profile, namespace, name, body):
        path = profile / "skills" / namespace / name / "SKILL.md"
        ledger.record("patch", name, path.parent, lambda: path.write_text(skill_text(name, body), encoding="utf-8"))

    def share(self, profile, ledger, skill):
        return record_share_choice(profile, skill=skill, choice="share", decided_by="owner-1", ledger=ledger)

    def approval(self, profile):
        pending = pending_learning_packet(profile)
        packet = pending["packet"]
        return self.key.sign({
            "approved": True, "approvedAt": "2026-10-31T08:00:00Z", "approvedBy": "operator",
            "workerId": packet["worker"]["workerId"], "roleReleaseCommit": packet["roleRelease"]["commit"],
            "governedStateHash": packet["governedStateHash"], "packetDigest": pending["packetDigest"],
        })

    def test_packet_contains_only_owner_shared_changes_with_native_trace_and_receipts(self):
        profile = self.root / "profile"
        write_profile(profile, {"status-report": "Report status weekly."})
        ledger, manager = self.native(profile)
        approvals = FakeWriteApproval()
        self.create_skill(ledger, profile, "candidates", "deadline-check", "Confirm the timezone of every deadline.")
        pending_id = approvals.stage(
            {"action": "patch", "name": "deadline-check", "old_string": "Confirm the timezone of every deadline.",
             "new_string": "Confirm the timezone and source of every deadline."},
            summary="patch 'deadline-check'",
        )
        decide_pending(profile, pending_id=pending_id, approve=True, decided_by="owner-1",
                       approvals=approvals, skill_manager=manager, ledger=ledger)
        self.patch_skill(ledger, profile, "role", "status-report", "Report status weekly with blockers.")
        self.create_skill(ledger, profile, "private", "contoso-steering", "Brief the Contoso steering group.")
        self.share(profile, ledger, "skills/candidates/deadline-check")

        summary = prepare_learning_packet(profile, ledger=ledger)
        packet = pending_learning_packet(profile)["packet"]

        self.assertEqual([item["artifactPath"] for item in summary["improvements"]], ["skills/candidates/deadline-check"])
        self.assertEqual(summary["excluded"], [{
            "artifactPath": "skills/role/status-report", "classification": "role_skill_improvement",
            "reason": "The Worker owner has not chosen Share for this change.",
        }])
        improvement = packet["improvements"][0]
        self.assertEqual(packet["packetVersion"], "3.0")
        self.assertEqual([record["action"] for record in improvement["ledger"]], ["create", "patch"])
        self.assertEqual(improvement["approvals"][0]["pendingId"], pending_id)
        self.assertEqual(improvement["shareDecision"]["decidedBy"], "owner-1")
        self.assertEqual(improvement["artifactHash"], exported_artifact_hash(improvement["files"]))
        self.assertTrue(all(path.startswith("skills/candidates/") for record in improvement["ledger"]
                            for path in record["files"]))
        serialized = json.dumps(packet)
        for private in ("contoso", "Contoso", "status-report", "native-session", str(profile)):
            self.assertNotIn(private, serialized)
        self.assertEqual(prepare_learning_packet(profile, ledger=ledger)["packetDigest"], summary["packetDigest"])

    def test_shared_changes_fail_closed_with_reasons(self):
        profile = self.root / "profile"
        write_profile(profile, {"status-report": "Report status weekly."})
        ledger, manager = self.native(profile)
        self.create_skill(ledger, profile, "candidates", "lost-trace", "Ask for owners.")
        self.create_skill(ledger, profile, "candidates", "private-detail", "Email pm@contoso.com for approvals.")
        self.create_skill(ledger, profile, "candidates", "bundle", "Use the helper script.")
        manager.apply_skill_pending({"action": "write_file", "name": "bundle", "file_path": "scripts/run.py",
                                     "file_content": "print('local only')\n"})
        self.create_skill(ledger, profile, "candidates", "changed-later", "Track risks.")
        for name in ("lost-trace", "private-detail", "bundle", "changed-later"):
            self.share(profile, ledger, f"skills/candidates/{name}")
        ledger.entries = [entry for entry in ledger.entries if entry["skill"] != "lost-trace"]
        later = profile / "skills" / "candidates" / "changed-later" / "SKILL.md"
        later.write_text(later.read_text(encoding="utf-8") + "\nEdited.\n", encoding="utf-8")

        summary = prepare_learning_packet(profile, ledger=ledger)

        self.assertEqual(summary["improvements"], [])
        reasons = {item["artifactPath"].rsplit("/", 1)[-1]: item["reason"] for item in summary["excluded"]}
        self.assertIn("native ledger does not trace", reasons["lost-trace"])
        self.assertIn("email address", reasons["private-detail"])
        self.assertIn("exactly one SKILL.md", reasons["bundle"])
        self.assertIn("changed after the owner chose Share", reasons["changed-later"])

    def test_attestation_requires_signed_exact_digest_and_later_changes_invalidate_export(self):
        profile = self.root / "profile"
        write_profile(profile, {"status-report": "Report status weekly."})
        ledger, _ = self.native(profile)
        self.patch_skill(ledger, profile, "role", "status-report", "Report status weekly with blockers.")
        self.share(profile, ledger, "skills/role/status-report")
        prepare_learning_packet(profile, ledger=ledger)
        wrong = self.approval(profile)
        wrong["packetDigest"] = "0" * 64
        with self.assertRaisesRegex(CollectiveLearningError, "digest"):
            attest_learning_packet(profile, receipt=self.key.sign(wrong))
        forged = {**self.approval(profile), "signature": ApprovalKey(b"\x09" * 32).sign({"x": 1})["signature"]}
        with self.assertRaisesRegex(CollectiveLearningError, "signature"):
            attest_learning_packet(profile, receipt=forged)

        receipt = attest_learning_packet(profile, receipt=self.approval(profile))
        self.assertEqual(attest_learning_packet(profile, receipt=receipt), receipt)
        exported = approved_learning_packet(profile)
        self.assertEqual(exported["receipt"], receipt)
        self.assertEqual(set(exported["packet"]["improvements"][0]["baselineFileHashes"]),
                         {"skills/role/status-report/SKILL.md"})
        self.assertTrue(worker_refresh_readiness(profile)["exportRequired"])

        self.patch_skill(ledger, profile, "role", "status-report", "Changed after approval.")
        with self.assertRaisesRegex(CollectiveLearningError, "changed after"):
            approved_learning_packet(profile)
        with self.assertRaisesRegex(RuntimeError, "Worker Refresh is blocked"):
            worker_refresh_readiness(profile)


class WorkerRefreshGateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.key = ApprovalKey()
        environment = patch.dict(os.environ, {"COLLECTIVE_LEARNING_APPROVAL_PUBLIC_KEY": self.key.public_key})
        environment.start()
        self.addCleanup(environment.stop)
        self.repo = self.root / "roles"
        self.repo.mkdir()
        self.git("init")
        self.git("config", "user.email", "roles@example.com")
        self.git("config", "user.name", "Role Release Test")
        self.first = self.release("3.0.0", "Report status weekly.")
        installed = blueprint.install_or_refresh_role_release(self.root / "hermes", self.settings("3.0.0", self.first))
        self.profile = installed.profile_home
        self.ledger = FakeSkillLedger(self.profile)
        self.second = self.release("3.1.0", "Report status weekly with risks.")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout.strip()

    def release(self, version, body):
        root = self.repo / "roles" / "junior-project-manager"
        skill = root / "skills" / "role" / "status-report" / "SKILL.md"
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text(skill_text("status-report", body), encoding="utf-8")
        (root / "SOUL.md").write_text("# Junior Project Manager\n", encoding="utf-8")
        (root / "distribution.yaml").write_text(
            f"role_blueprint: junior-project-manager\nrole_release: {version}\n"
            "distribution_owned:\n  - SOUL.md\n  - skills/role\n  - distribution.yaml\n",
            encoding="utf-8",
        )
        self.git("add", ".")
        self.git("commit", "-m", f"Role Release {version}")
        return self.git("rev-parse", "HEAD")

    def settings(self, version, commit):
        return blueprint.RoleReleaseSettings(
            role_blueprint="junior-project-manager", source=str(self.repo),
            repository_path="roles/junior-project-manager", role_release=version, commit=commit,
            worker_id="worker-1", assignment_scope="team-alpha",
        )

    def refresh(self):
        return blueprint.install_or_refresh_role_release(self.root / "hermes", self.settings("3.1.0", self.second))

    def change_role_skill(self):
        path = self.profile / "skills" / "role" / "status-report" / "SKILL.md"
        self.ledger.record(
            "patch", "status-report", path.parent,
            lambda: path.write_text(skill_text("status-report", "Report status weekly with blockers."), encoding="utf-8"),
        )

    def test_refresh_is_refused_until_shared_governed_change_has_an_attested_export(self):
        self.change_role_skill()
        with self.assertRaisesRegex(RuntimeError, "Worker Refresh is blocked"):
            self.refresh()
        record_share_choice(self.profile, skill="skills/role/status-report", choice="share",
                            decided_by="owner-1", ledger=self.ledger)
        prepare_learning_packet(self.profile, ledger=self.ledger)
        with self.assertRaisesRegex(RuntimeError, "Worker Refresh is blocked"):
            self.refresh()
        pending = pending_learning_packet(self.profile)
        packet = pending["packet"]
        attest_learning_packet(self.profile, receipt=self.key.sign({
            "approved": True, "approvedAt": "2026-10-31T08:00:00Z", "approvedBy": "operator",
            "workerId": "worker-1", "roleReleaseCommit": self.first,
            "governedStateHash": packet["governedStateHash"], "packetDigest": pending["packetDigest"],
        }))

        refreshed = self.refresh()

        self.assertEqual(refreshed.manifest["roleRelease"], "3.1.0")
        role_skill = self.profile / "skills" / "role" / "status-report" / "SKILL.md"
        self.assertIn("with risks", role_skill.read_text(encoding="utf-8"))

    def test_signed_rejection_allows_refresh_without_export_and_kept_playbooks_survive(self):
        candidate = self.profile / "skills" / "candidates" / "deadline-check"
        self.ledger.record(
            "create", "deadline-check", candidate,
            lambda: (candidate.mkdir(parents=True),
                     (candidate / "SKILL.md").write_text(skill_text("deadline-check", "Confirm timezones."), encoding="utf-8")),
        )
        self.change_role_skill()
        record_share_choice(self.profile, skill="skills/role/status-report", choice="keep", decided_by="owner-1",
                            ledger=self.ledger, skill_manager=FakeSkillManager(self.profile, self.ledger))
        with self.assertRaisesRegex(RuntimeError, "Worker Refresh is blocked"):
            self.refresh()
        prepared = prepare_refresh_rejection(self.profile)
        descriptor = prepared["disposition"]
        attest_refresh_rejection(self.profile, receipt=self.key.sign({
            "disposition": "reject_and_refresh", "rejectedAt": "2026-10-31T08:00:00Z", "rejectedBy": "operator",
            "reason": "Not reusable across the role.", "workerId": "worker-1", "roleReleaseCommit": self.first,
            "governedStateHash": descriptor["governedStateHash"], "dispositionDigest": prepared["dispositionDigest"],
        }))
        with self.assertRaisesRegex(CollectiveLearningError, "No approved"):
            approved_learning_packet(self.profile)

        self.refresh()

        self.assertFalse(candidate.exists())
        playbook = self.profile / "skills" / "private" / "status-report-playbook" / "SKILL.md"
        self.assertIn("with blockers", playbook.read_text(encoding="utf-8"))


class RoleReleaseOwnershipTests(unittest.TestCase):
    def test_role_release_owns_the_whole_role_tree_and_never_private_namespaces(self):
        with self.assertRaisesRegex(ValueError, "whole skills/role"):
            blueprint._validate_owned_path("skills/role/deadline-verification")
        self.assertEqual(blueprint._validate_owned_path("skills/role"), "skills/role")
        with self.assertRaisesRegex(ValueError, "whole skills/role"):
            blueprint._validate_owned_path("skills/private")


if __name__ == "__main__":
    unittest.main()
