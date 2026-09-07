import hashlib
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.collective_review import (
    CollectiveReviewError,
    judge_prompt,
    load_packets,
    remove_embedded_role_release,
    role_blueprint_context_from_root,
    update_distribution_owned,
    validate_decision,
    validate_next_role_release,
    build_review_manifest,
    create_role_release_pull_request,
)
from scripts.collective_learning import attested_envelope
from runtimes.hermes.learning import _artifact_hash


class CollectiveReviewTests(unittest.TestCase):
    worker_private_key = Ed25519PrivateKey.from_private_bytes(b"\x02" * 32)
    worker_public_key = base64.b64encode(
        worker_private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")

    def test_operator_export_removes_bridge_transport_metadata(self):
        packet = {"packetVersion": "2.0"}
        receipt = {"approved": True}

        envelope = attested_envelope(
            {
                "packet": packet,
                "receipt": receipt,
                "sandboxId": "sandbox-1",
                "gatewayUrl": "https://sandbox.example",
                "reusedExistingSandbox": True,
            }
        )

        self.assertEqual(envelope, {"packet": packet, "receipt": receipt})

    def _packet(self, worker_id: str = "worker-1") -> dict:
        record_id = f"lr-{worker_id}"
        packet = {
            "packetVersion": "2.0",
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
                    "baselineFileHashes": {},
                    "provenance": [{
                        "schemaVersion": "3.0",
                        "recordId": record_id,
                        "createdAt": "2026-07-16T08:00:00Z",
                        "classification": "candidate_improvement",
                        "artifactPath": "skills/candidates/deadline-verification",
                        "action": "create",
                        "title": "Verify deadline evidence",
                        "generalizedLearning": "Request a timezone before confirming a deadline.",
                        "rationale": "Ambiguous dates need clarification.",
                        "evidence": [{"sourceType": "private_session", "summary": "Several deadlines lacked timezone context."}],
                        "confidence": 0.8,
                        "sourceStage": "foreground",
                        "agentProposedScenarios": [{
                            "scenarioId": "missing-timezone",
                            "input": "Confirm Friday as the deadline.",
                            "setupAssumptions": ["The timezone is unknown."],
                            "expectedObservableOutcomes": ["Ask for timezone confirmation."],
                            "acceptanceCriteria": [{"observable": "response.text", "operator": "contains", "value": "timezone"}],
                            "scope": "Deadline clarification.",
                        }],
                        "privacy": {"redactionStatus": "passed", "warnings": []},
                    }],
                }
            ],
            "privacy": {
                "status": "ready_for_human_approval",
                "excludedPaths": ["memories/", "skills/private/", "state.db"],
            },
            "evaluation": {
                "agentProposedScenarios": "included_in_provenance",
                "executionStatus": "not_run",
                "independentHoldout": "not_supplied",
            },
        }
        improvement = packet["improvements"][0]
        record = improvement["provenance"][0]
        record["artifact"] = {
            "path": improvement["artifactPath"], "beforeHash": None,
            "afterHash": _artifact_hash({key: value.encode("utf-8") for key, value in improvement["files"].items()}),
            "changedFiles": list(improvement["files"]),
        }
        record["roleRelease"] = {key: packet["roleRelease"][key] for key in ("roleBlueprint", "release", "commit")}
        record["worker"] = dict(packet["worker"])
        return packet

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
            improvement = envelope["packet"]["improvements"][0]
            improvement["provenance"][-1]["artifact"]["afterHash"] = _artifact_hash(
                {key: value.encode("utf-8") for key, value in improvement["files"].items()}
            )
            envelope["receipt"]["packetDigest"] = hashlib.sha256(
                json.dumps(envelope["packet"], sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            ).hexdigest()
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

    def test_merger_prompt_includes_existing_role_blueprint_and_quality_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            role_root = Path(temp_dir)
            (role_root / "skills" / "role" / "existing").mkdir(parents=True)
            (role_root / "SOUL.md").write_text(
                "# Role\n\nKeep commitments traceable.\n",
                encoding="utf-8",
            )
            (role_root / "distribution.yaml").write_text(
                "role_blueprint: junior-project-manager\n"
                "role_release: 3.0.0\n"
                "distribution_owned:\n"
                "- SOUL.md\n"
                "- skills/role/existing\n",
                encoding="utf-8",
            )
            (role_root / "skills" / "role" / "existing" / "SKILL.md").write_text(
                "---\nname: existing\ndescription: Track commitments.\n---\n",
                encoding="utf-8",
            )

            context = role_blueprint_context_from_root(role_root)
            prompt = judge_prompt([self._packet()], "3.1.0", context)

        self.assertIn("Keep commitments traceable.", prompt)
        self.assertIn("skills/role/existing/SKILL.md", prompt)
        self.assertIn("concrete, non-overlapping progressive-disclosure trigger", prompt)
        self.assertIn("self-contained and executable when loaded alone", prompt)

    def test_promotion_keeps_whole_role_tree_ownership(self):
        distribution = {
            "distribution_owned": [
                "SOUL.md",
                "skills/role",
                "schemas",
                "distribution.yaml",
            ]
        }
        proposals = [
            {
                "targetPath": "skills/role/delivery-commitment-control/SKILL.md",
            },
            {
                "targetPath": "skills/role/junior-project-manager/SKILL.md",
            },
        ]

        update_distribution_owned(distribution, proposals)

        self.assertEqual(
            distribution["distribution_owned"],
            [
                "SOUL.md",
                "skills/role",
                "schemas",
                "distribution.yaml",
            ],
        )

    def test_promotion_removes_stale_role_release_from_soul(self):
        soul = "# Junior Project Manager\n\nRole Release: 3.0.1.\n\nKeep plans concrete.\n"

        self.assertEqual(
            remove_embedded_role_release(soul),
            "# Junior Project Manager\n\nKeep plans concrete.\n",
        )

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
        decision["rejected"] = [{"recordId": "lr-worker-1", "reason": "Unsupported."}]
        decision["summary"] = "Send the review to owner@example.com."
        with self.assertRaisesRegex(CollectiveReviewError, "decision privacy scan"):
            validate_decision(decision, packets=packets)


if __name__ == "__main__":
    unittest.main()
