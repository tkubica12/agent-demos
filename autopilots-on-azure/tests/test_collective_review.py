import hashlib
import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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
import scripts.collective_learning as operator_cli
from collective_learning import exported_artifact_hash, file_hashes


class CollectiveReviewTests(unittest.TestCase):
    worker_private_key = Ed25519PrivateKey.from_private_bytes(b"\x02" * 32)
    worker_public_key = base64.b64encode(
        worker_private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")

    def test_operator_export_removes_bridge_transport_metadata(self):
        packet = {"packetVersion": "3.0"}
        receipt = {"approved": True}

        envelope = attested_envelope(
            {
                "packet": packet,
                "receipt": receipt,
            }
        )

        self.assertEqual(envelope, {"packet": packet, "receipt": receipt})

    def test_operator_cli_sends_scoped_entra_token_not_runtime_key_or_caller_identity(self):
        config = {
            "agent365_tenant_id": "operator-tenant",
            "collective_learning_operator_client_id": "operator-client",
            "api_server_key": "runtime-key-must-not-be-sent",
        }
        credential = MagicMock()
        credential.__enter__.return_value = credential
        credential.get_token.return_value.token = "owner-access-token"
        response = io.BytesIO(b'{"approved": true}')
        with (
            patch.object(operator_cli, "_load_json", side_effect=[{"bridge_url": "https://bridge.example"}, config]),
            patch.object(operator_cli, "AzureCliCredential", return_value=credential) as login,
            patch.object(operator_cli.urllib.request, "urlopen", return_value=response) as send,
        ):
            result = operator_cli._operator_request("/internal/collective-learning/approve", method="POST",
                                                    body={"packetDigest": "a" * 64})
        self.assertTrue(result["approved"])
        login.assert_called_once_with(tenant_id="operator-tenant")
        credential.get_token.assert_called_once_with("api://operator-client/.default")
        request = send.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer owner-access-token")
        self.assertIsNone(request.get_header("X-autopilot-key"))
        self.assertEqual(json.loads(request.data), {"packetDigest": "a" * 64})

    @staticmethod
    def improvement_id(worker_id: str) -> str:
        return "li-" + hashlib.sha256(worker_id.encode("utf-8")).hexdigest()[:24]

    def _packet(self, worker_id: str = "worker-1") -> dict:
        path = "skills/candidates/deadline-verification"
        files = {
            f"{path}/SKILL.md": "---\nname: deadline-verification\ndescription: Verify deadline evidence.\n---\n",
        }
        created = {f"{path}/SKILL.md": {"before": None, "after": "0" * 64}}
        patched = {f"{path}/SKILL.md": {"before": "0" * 64, "after": file_hashes(files)[f"{path}/SKILL.md"]}}
        return {
            "packetVersion": "3.0",
            "createdAt": "2026-10-31T08:00:00Z",
            "roleRelease": {
                "roleBlueprint": "junior-project-manager",
                "source": "https://github.com/example/roles.git",
                "path": "roles/junior-project-manager",
                "release": "3.0.0",
                "commit": "a" * 40,
            },
            "worker": {"workerId": worker_id, "assignmentScope": "team-alpha"},
            "governedStateHash": "b" * 64,
            "improvements": [{
                "improvementId": self.improvement_id(worker_id),
                "classification": "candidate_improvement",
                "artifactPath": path,
                "files": files,
                "baselineFileHashes": {},
                "artifactHash": exported_artifact_hash(files),
                "ledger": [
                    {"id": "e1", "ts": "2026-10-30T10:00:00+00:00", "actor": "curator", "action": "create",
                     "files": created},
                    {"id": "e2", "ts": "2026-10-30T11:00:00+00:00", "actor": "agent", "action": "patch",
                     "files": patched},
                ],
                "shareDecision": {"receiptId": "share-candidates-deadline-verification", "decidedBy": "owner-1",
                                  "decidedAt": "2026-10-31T07:00:00Z"},
                "approvals": [],
            }],
            "privacy": {
                "status": "ready_for_human_approval",
                "excludedPaths": ["memories/", "skills/private/", "state.db"],
            },
            "evaluation": {
                "agentProposedScenarios": "not_generated",
                "executionStatus": "not_run",
                "independentHoldout": "not_supplied",
            },
        }

    def _signed(self, packet: dict) -> dict:
        receipt = {
            "approved": True,
            "approvedAt": "2026-10-31T08:01:00Z",
            "approvedBy": "operator",
            "workerId": packet["worker"]["workerId"],
            "roleReleaseCommit": "a" * 40,
            "governedStateHash": "b" * 64,
            "packetDigest": hashlib.sha256(
                json.dumps(packet, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            ).hexdigest(),
        }
        receipt["signature"] = base64.b64encode(self.worker_private_key.sign(
            json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        )).decode("ascii")
        return {"packet": packet, "receipt": receipt}

    def _envelope(self, worker_id: str) -> dict:
        return self._signed(self._packet(worker_id))

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

    def test_load_packets_rejects_unapproved_untraced_or_private_content(self):
        keys = {"worker-1": self.worker_public_key}
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "packet.json"
            envelope = self._envelope("worker-1")
            envelope["receipt"]["approved"] = False
            unsigned = {key: value for key, value in envelope["receipt"].items() if key != "signature"}
            envelope["receipt"]["signature"] = base64.b64encode(self.worker_private_key.sign(
                json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            )).decode("ascii")
            path.write_text(json.dumps(envelope), encoding="utf-8")
            with self.assertRaisesRegex(CollectiveReviewError, "does not match"):
                load_packets([path], worker_public_keys=keys)

            envelope = self._envelope("worker-1")
            envelope["packet"]["improvements"][0]["files"]["skills/candidates/deadline-verification/SKILL.md"] += "x"
            path.write_text(json.dumps(envelope), encoding="utf-8")
            with self.assertRaisesRegex(CollectiveReviewError, "attestation"):
                load_packets([path], worker_public_keys=keys)

            packet = self._packet("worker-1")
            packet["improvements"][0]["ledger"].pop(0)
            path.write_text(json.dumps(self._signed(packet)), encoding="utf-8")
            with self.assertRaisesRegex(CollectiveReviewError, "continuous change chain"):
                load_packets([path], worker_public_keys=keys)

            packet = self._packet("worker-1")
            packet["improvements"][0]["shareDecision"] = {}
            path.write_text(json.dumps(self._signed(packet)), encoding="utf-8")
            with self.assertRaisesRegex(CollectiveReviewError, "Share decision"):
                load_packets([path], worker_public_keys=keys)

            packet = self._packet("worker-1")
            improvement = packet["improvements"][0]
            improvement["files"]["skills/candidates/deadline-verification/SKILL.md"] += "Contact owner@example.com.\n"
            improvement["artifactHash"] = exported_artifact_hash(improvement["files"])
            improvement["ledger"][-1]["files"]["skills/candidates/deadline-verification/SKILL.md"]["after"] = (
                file_hashes(improvement["files"])["skills/candidates/deadline-verification/SKILL.md"]
            )
            path.write_text(json.dumps(self._signed(packet)), encoding="utf-8")
            with self.assertRaisesRegex(CollectiveReviewError, "privacy scan"):
                load_packets([path], worker_public_keys=keys)

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
                    "supportingImprovementIds": [self.improvement_id("worker-1")],
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
                    "supportingImprovementIds": [self.improvement_id("worker-1")],
                    "supportingWorkers": ["worker-1"],
                }
            ],
            "rejected": [],
            "conflicts": [],
        }

        with self.assertRaisesRegex(CollectiveReviewError, "Unsafe proposal target"):
            validate_decision(decision, packets=packets)

        decision["proposals"] = []
        decision["rejected"] = [{"improvementId": self.improvement_id("worker-1"), "reason": "Unsupported."}]
        decision["summary"] = "Send the review to owner@example.com."
        with self.assertRaisesRegex(CollectiveReviewError, "decision privacy scan"):
            validate_decision(decision, packets=packets)


    def decision(self):
        return {
            "summary": "Clarify ambiguous deadlines.",
            "proposals": [{
                "action": "create_or_replace", "targetPath": "skills/role/deadline-verification/SKILL.md",
                "content": "---\nname: deadline-verification\ndescription: Clarify missing deadline context.\n---\nAsk for timezone.",
                "rationale": "Observed ambiguity supports narrow clarification.",
                "supportingImprovementIds": [self.improvement_id("worker-1")], "supportingWorkers": ["worker-1"],
            }],
            "rejected": [], "conflicts": [],
        }

    def test_promotion_requires_complete_correct_improvement_worker_accounting(self):
        packets = [self._packet()]
        for label, change in (
            ("empty improvements", {"supportingImprovementIds": []}),
            ("wrong worker", {"supportingWorkers": ["worker-2"]}),
            ("empty rationale", {"rationale": ""}),
            ("wrong skill name", {"content": "---\nname: wrong\ndescription: Wrong.\n---\n"}),
        ):
            with self.subTest(label=label), self.assertRaises(CollectiveReviewError):
                decision = self.decision()
                decision["proposals"][0].update(change)
                validate_decision(decision, packets=packets)
        decision = self.decision()
        decision["rejected"] = [{"improvementId": self.improvement_id("worker-1"), "reason": "Rejected."}]
        with self.assertRaisesRegex(CollectiveReviewError, "both accepted and rejected"):
            validate_decision(decision, packets=packets)
        decision["rejected"] = []
        decision["proposals"] = []
        with self.assertRaisesRegex(CollectiveReviewError, "accounted"):
            validate_decision(decision, packets=packets)
        duplicate = self._packet("worker-2")
        duplicate["improvements"][0]["improvementId"] = self.improvement_id("worker-1")
        with self.assertRaisesRegex(CollectiveReviewError, "Duplicate improvement"):
            validate_decision(self.decision(), packets=[packets[0], duplicate])

    def test_manifest_retains_exact_signed_sources_and_no_pr_for_all_rejected(self):
        envelope = self._envelope("worker-1")
        packets = [envelope["packet"]]
        keys = {"worker-1": self.worker_public_key}
        with self.assertRaisesRegex(CollectiveReviewError, "exactly match"):
            build_review_manifest(packets, self.decision(), approved_envelopes=[], worker_public_keys=keys)
        manifest = build_review_manifest(
            packets, self.decision(), approved_envelopes=[envelope], worker_public_keys=keys,
        )
        self.assertEqual(manifest["approvedSources"], [envelope])
        self.assertEqual(manifest["improvementWorkers"], {self.improvement_id("worker-1"): "worker-1"})
        self.assertEqual(manifest["evaluation"]["independentHoldout"], "not_supplied")
        decision = {"summary": "Not enough evidence.", "proposals": [],
                    "rejected": [{"improvementId": self.improvement_id("worker-1"), "reason": "Too narrow."}],
                    "conflicts": []}
        with patch("scripts.collective_review._run") as run:
            result = create_role_release_pull_request(
                packets, decision, next_role_release="3.1.0", draft=True,
                approved_envelopes=[envelope], worker_public_keys=keys,
            )
        run.assert_not_called()
        self.assertEqual(result["status"], "no_promotion")
        envelope["packet"]["evaluation"]["executionStatus"] = "passed"
        with self.assertRaisesRegex(CollectiveReviewError, "attestation"):
            build_review_manifest(packets, self.decision(), approved_envelopes=[envelope], worker_public_keys=keys)


if __name__ == "__main__":
    unittest.main()
