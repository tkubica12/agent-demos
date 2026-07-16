import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

RUNTIME_DIR = Path(__file__).resolve().parents[1] / "runtimes" / "hermes"
sys.path.insert(0, str(RUNTIME_DIR))

from blueprint import role_skill_hashes  # noqa: E402
from collective_learning import (  # noqa: E402
    CollectiveLearningError,
    approved_learning_packet,
    attest_learning_packet,
    pending_learning_packet,
    prepare_learning_packet,
)
from learning import begin_learning_turn, reconcile_learning_turn  # noqa: E402


class CollectiveLearningTests(unittest.TestCase):
    def setUp(self):
        self.private_key = Ed25519PrivateKey.from_private_bytes(b"\x03" * 32)
        self.previous_public_key = os.environ.get("COLLECTIVE_LEARNING_APPROVAL_PUBLIC_KEY")
        os.environ["COLLECTIVE_LEARNING_APPROVAL_PUBLIC_KEY"] = base64.b64encode(
            self.private_key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        ).decode("ascii")

    def tearDown(self):
        if self.previous_public_key is None:
            os.environ.pop("COLLECTIVE_LEARNING_APPROVAL_PUBLIC_KEY", None)
        else:
            os.environ["COLLECTIVE_LEARNING_APPROVAL_PUBLIC_KEY"] = self.previous_public_key

    def _approval_receipt(self, profile: Path, approved_by: str = "operator") -> dict:
        pending = pending_learning_packet(profile)
        packet = pending["packet"]
        receipt = {
            "approved": True,
            "approvedAt": "2026-07-16T08:01:00Z",
            "approvedBy": approved_by,
            "workerId": packet["worker"]["workerId"],
            "roleReleaseCommit": packet["roleRelease"]["commit"],
            "governedStateHash": packet["governedStateHash"],
            "packetDigest": pending["packetDigest"],
        }
        return self._sign_receipt(receipt)

    def _sign_receipt(self, receipt: dict) -> dict:
        unsigned = {key: value for key, value in receipt.items() if key != "signature"}
        receipt["signature"] = base64.b64encode(
            self.private_key.sign(
                json.dumps(
                    unsigned,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode("utf-8")
            )
        ).decode("ascii")
        return receipt

    def _profile(self, root: Path) -> Path:
        profile = root / "profile"
        role = profile / "skills" / "role" / "junior-project-manager" / "SKILL.md"
        role.parent.mkdir(parents=True)
        role.write_text(
            "---\nname: junior-project-manager\ndescription: Track delivery commitments.\n---\n"
            "\n# Delivery\n\nRequire owner and due date.\n",
            encoding="utf-8",
        )
        manifest = {
            "roleBlueprint": "junior-project-manager",
            "roleBlueprintSource": "https://example.com/roles.git",
            "roleBlueprintPath": "roles/junior-project-manager",
            "roleRelease": "3.0.0",
            "roleReleaseCommit": "a" * 40,
            "workerId": "worker-1",
            "assignmentScope": "team-alpha",
            "profileName": "junior-project-manager",
            "distributionOwned": ["skills/role/junior-project-manager"],
            "roleSkillBaseline": role_skill_hashes(profile),
            "refreshedAt": "2026-07-16T08:00:00Z",
        }
        worker = profile / "local" / "worker.json"
        worker.parent.mkdir(parents=True)
        worker.write_text(json.dumps(manifest), encoding="utf-8")
        return profile

    @staticmethod
    def _provenance(artifact_path: str, classification: str, action: str) -> dict:
        return {
            "classification": classification,
            "artifactPath": artifact_path,
            "action": action,
            "title": "Improve deadline verification",
            "generalizedLearning": "Record source, timezone, and confirmer for deadlines.",
            "rationale": "Traceability reduces schedule ambiguity.",
            "evidence": [
                {
                    "sourceType": "private_session",
                    "summary": "Generalized handoffs repeatedly lacked deadline evidence.",
                }
            ],
            "confidence": 0.93,
            "sourceStage": "dream",
        }

    def _create_candidate_with_provenance(self, profile: Path) -> None:
        token = begin_learning_turn(profile)["token"]
        skill = profile / "skills" / "candidates" / "deadline-verification" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            "---\nname: deadline-verification\ndescription: Verify deadline evidence.\n---\n"
            "\n# Deadline verification\n\nRecord source, timezone, and confirmer.\n",
            encoding="utf-8",
        )
        result = reconcile_learning_turn(
            profile,
            token=token,
            provenance=[
                self._provenance(
                    "skills/candidates/deadline-verification",
                    "candidate_improvement",
                    "create",
                )
            ],
        )
        self.assertEqual(len(result["accepted"]), 1)

    def test_prepare_packet_exports_candidate_and_excludes_private_playbook(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = self._profile(Path(temp_dir))
            private = profile / "skills" / "private" / "cedar-delivery" / "SKILL.md"
            private.parent.mkdir(parents=True)
            private.write_text(
                "---\nname: cedar-delivery\ndescription: Private Cedar procedure.\n---\n",
                encoding="utf-8",
            )
            self._create_candidate_with_provenance(profile)

            summary = prepare_learning_packet(profile)
            pending = json.loads(
                (profile / "learning" / "exports" / f"{'a' * 40}.pending.json").read_text(encoding="utf-8")
            )

        self.assertTrue(summary["approvalRequired"])
        self.assertEqual(
            summary["improvements"][0]["artifactPath"],
            "skills/candidates/deadline-verification",
        )
        serialized = json.dumps(pending)
        self.assertNotIn("cedar-delivery", serialized)
        self.assertNotIn("Private Cedar", serialized)
        self.assertEqual(pending["privacy"]["status"], "ready_for_human_approval")

    def test_approval_requires_exact_digest_and_produces_refresh_receipt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = self._profile(Path(temp_dir))
            self._create_candidate_with_provenance(profile)
            prepare_learning_packet(profile)
            wrong_receipt = self._approval_receipt(profile, "operator")
            wrong_receipt["packetDigest"] = "wrong"
            self._sign_receipt(wrong_receipt)

            with self.assertRaisesRegex(CollectiveLearningError, "digest"):
                attest_learning_packet(
                    profile,
                    receipt=wrong_receipt,
                )

            receipt = attest_learning_packet(
                profile,
                receipt=self._approval_receipt(profile, "operator"),
            )
            exported = approved_learning_packet(profile)
            packet = exported["packet"]
            attestation = exported["receipt"]

        self.assertTrue(receipt["approved"])
        self.assertEqual(receipt["roleReleaseCommit"], "a" * 40)
        self.assertEqual(packet["privacy"]["status"], "ready_for_human_approval")
        self.assertEqual(attestation["approvedBy"], "operator")
        self.assertEqual(len(attestation["signature"]), 88)

    def test_approved_packet_is_invalidated_by_later_skill_change(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = self._profile(Path(temp_dir))
            self._create_candidate_with_provenance(profile)
            prepare_learning_packet(profile)
            attest_learning_packet(
                profile,
                receipt=self._approval_receipt(profile, "operator"),
            )
            skill = profile / "skills" / "candidates" / "deadline-verification" / "SKILL.md"
            skill.write_text(skill.read_text(encoding="utf-8") + "\nChanged later.\n", encoding="utf-8")

            with self.assertRaisesRegex(CollectiveLearningError, "changed after"):
                approved_learning_packet(profile)

    def test_prepare_fails_when_current_artifact_has_no_matching_provenance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = self._profile(Path(temp_dir))
            skill = profile / "skills" / "candidates" / "missing-provenance" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(
                "---\nname: missing-provenance\ndescription: Missing provenance.\n---\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(CollectiveLearningError, "no provenance"):
                prepare_learning_packet(profile)


if __name__ == "__main__":
    unittest.main()
