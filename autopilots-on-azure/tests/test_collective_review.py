import hashlib
import base64
import json
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.collective_review import (
    CollectiveReviewError,
    load_packets,
    validate_decision,
    validate_next_role_release,
)


class CollectiveReviewTests(unittest.TestCase):
    worker_private_key = Ed25519PrivateKey.from_private_bytes(b"\x02" * 32)
    worker_public_key = base64.b64encode(
        worker_private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")

    def _packet(self, worker_id: str = "worker-1") -> dict:
        record_id = f"lr-{worker_id}"
        return {
            "packetVersion": "1.0",
            "createdAt": "2026-07-16T08:00:00Z",
            "roleRelease": {
                "roleBlueprint": "junior-project-manager",
                "source": "https://github.com/example/roles.git",
                "path": "roles/junior-project-manager",
                "release": "3.0.0",
                "commit": "a" * 40,
            },
            "worker": {
                "workerId": worker_id,
                "assignmentScope": "team-alpha",
            },
            "governedStateHash": "b" * 64,
            "improvements": [
                {
                    "classification": "candidate_improvement",
                    "artifactPath": "skills/candidates/deadline-verification",
                    "files": {
                        "skills/candidates/deadline-verification/SKILL.md": (
                            "---\nname: deadline-verification\n"
                            "description: Verify deadline evidence.\n---\n"
                        )
                    },
                    "provenance": {
                        "recordId": record_id,
                        "action": "create",
                        "title": "Verify deadline evidence",
                    },
                }
            ],
            "privacy": {
                "status": "ready_for_human_approval",
                "excludedPaths": ["memories/", "skills/private/", "state.db"],
                "approvedBy": "operator",
                "approvedAt": "2026-07-16T08:01:00Z",
            },
        }

    def _envelope(self, worker_id: str) -> dict:
        packet = self._packet(worker_id)
        packet_digest = hashlib.sha256(
            json.dumps(
                packet,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        receipt = {
            "approved": True,
            "approvedAt": "2026-07-16T08:01:00Z",
            "approvedBy": "operator",
            "workerId": worker_id,
            "roleReleaseCommit": "a" * 40,
            "governedStateHash": "b" * 64,
            "packetDigest": packet_digest,
        }
        receipt["signature"] = base64.b64encode(
            self.worker_private_key.sign(
                json.dumps(
                    receipt,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode("utf-8")
            )
        ).decode("ascii")
        return {"packet": packet, "receipt": receipt}

    def test_load_packets_requires_approved_same_release_unique_workers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "first.json"
            second = root / "second.json"
            first.write_text(json.dumps(self._envelope("worker-1")), encoding="utf-8")
            second.write_text(json.dumps(self._envelope("worker-2")), encoding="utf-8")

            packets = load_packets(
                [first, second],
                worker_public_keys={
                    "worker-1": self.worker_public_key,
                    "worker-2": self.worker_public_key,
                },
            )

        self.assertEqual([packet["worker"]["workerId"] for packet in packets], ["worker-1", "worker-2"])

    def test_load_packets_rejects_unapproved_or_private_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "packet.json"
            envelope = self._envelope("worker-1")
            envelope["receipt"]["approved"] = False
            unsigned = {
                key: value
                for key, value in envelope["receipt"].items()
                if key != "signature"
            }
            envelope["receipt"]["signature"] = base64.b64encode(
                self.worker_private_key.sign(
                    json.dumps(
                        unsigned,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ).encode("utf-8")
                )
            ).decode("ascii")
            path.write_text(json.dumps(envelope), encoding="utf-8")
            with self.assertRaisesRegex(CollectiveReviewError, "does not match"):
                load_packets([path], worker_public_keys={"worker-1": self.worker_public_key})

            envelope = self._envelope("worker-1")
            envelope["packet"]["improvements"][0]["files"]["skills/candidates/deadline-verification/SKILL.md"] += (
                "Contact owner@example.com."
            )
            path.write_text(json.dumps(envelope), encoding="utf-8")
            with self.assertRaisesRegex(CollectiveReviewError, "attestation"):
                load_packets([path], worker_public_keys={"worker-1": self.worker_public_key})

            packet_digest = hashlib.sha256(
                json.dumps(
                    envelope["packet"],
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode("utf-8")
            ).hexdigest()
            envelope["receipt"]["packetDigest"] = packet_digest
            unsigned = {
                key: value
                for key, value in envelope["receipt"].items()
                if key != "signature"
            }
            envelope["receipt"]["signature"] = base64.b64encode(
                self.worker_private_key.sign(
                    json.dumps(
                        unsigned,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ).encode("utf-8")
                )
            ).decode("ascii")
            path.write_text(json.dumps(envelope), encoding="utf-8")
            with self.assertRaisesRegex(CollectiveReviewError, "privacy scan"):
                load_packets([path], worker_public_keys={"worker-1": self.worker_public_key})

    def test_validate_decision_accepts_safe_role_skill_proposal(self):
        packets = [self._packet()]
        decision = {
            "summary": "Promote the deadline-verification procedure.",
            "proposals": [
                {
                    "action": "create_or_replace",
                    "targetPath": "skills/role/deadline-verification/SKILL.md",
                    "content": (
                        "---\nname: deadline-verification\n"
                        "description: Verify deadline evidence before accepting commitments.\n---\n"
                        "\n# Deadline verification\n\nRecord source, timezone, and confirmer.\n"
                    ),
                    "rationale": "The procedure is reusable across assignments.",
                    "supportingRecordIds": ["lr-worker-1"],
                    "supportingWorkers": ["worker-1"],
                }
            ],
            "rejected": [],
            "conflicts": [],
        }

        self.assertEqual(validate_decision(decision, packets=packets), decision)

    def test_next_role_release_must_be_strictly_newer_semver(self):
        packets = [self._packet()]
        validate_next_role_release(packets, "3.1.0")
        with self.assertRaisesRegex(CollectiveReviewError, "newer than 3.0.0"):
            validate_next_role_release(packets, "3.0.0")
        with self.assertRaisesRegex(CollectiveReviewError, "semantic version"):
            validate_next_role_release(packets, "next")

    def test_validate_decision_rejects_private_or_unsafe_target(self):
        packets = [self._packet()]
        decision = {
            "summary": "Unsafe proposal.",
            "proposals": [
                {
                    "action": "create_or_replace",
                    "targetPath": "skills/private/cedar/SKILL.md",
                    "content": "# Private",
                    "rationale": "Unsafe.",
                    "supportingRecordIds": ["lr-worker-1"],
                    "supportingWorkers": ["worker-1"],
                }
            ],
            "rejected": [],
            "conflicts": [],
        }

        with self.assertRaisesRegex(CollectiveReviewError, "Unsafe proposal target"):
            validate_decision(decision, packets=packets)

        decision["proposals"] = []
        decision["summary"] = "Send the review to owner@example.com."
        with self.assertRaisesRegex(CollectiveReviewError, "decision privacy scan"):
            validate_decision(decision, packets=packets)


if __name__ == "__main__":
    unittest.main()
