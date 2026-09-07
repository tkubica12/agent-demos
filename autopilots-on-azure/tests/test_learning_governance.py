import base64
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests import test_hermes_runtime as runtime_tests
from tests import test_collective_review as review_tests

import blueprint
import collective_learning
import learning
import start_hermes
from bridge.runtime.hermes import HermesRuntimeAdapter
from scripts.collective_review import (
    CollectiveReviewError,
    build_review_manifest,
    create_role_release_pull_request,
    validate_decision,
)


class LearningGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.runtime = runtime_tests.HermesRuntimeTests()
        self.review = review_tests.CollectiveReviewTests()
        self.key = Ed25519PrivateKey.from_private_bytes(b"\x08" * 32)
        public_key = base64.b64encode(self.key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw,
        )).decode("ascii")
        self.environment = patch.dict(os.environ, {"COLLECTIVE_LEARNING_APPROVAL_PUBLIC_KEY": public_key})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def sign(self, receipt):
        receipt["signature"] = base64.b64encode(self.key.sign(
            json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        )).decode("ascii")
        return receipt

    def candidate(self, profile, *, namespace="candidates", name="deadline-verification", suffix=""):
        path = profile / "skills" / namespace / name / "SKILL.md"
        token = learning.begin_learning_turn(profile)["token"]
        existed = path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        if existed:
            path.write_text(path.read_text(encoding="utf-8") + suffix, encoding="utf-8")
        else:
            path.write_text(
                f"---\nname: {name}\ndescription: Clarify missing deadline context.\n---\n\nAsk for timezone.\n{suffix}",
                encoding="utf-8",
            )
        provenance = self.runtime._provenance(
            f"skills/{namespace}/{name}",
            classification="candidate_improvement" if namespace == "candidates" else "role_skill_improvement",
            action="patch" if existed else "create",
        )
        result = learning.reconcile_learning_turn(profile, token=token, provenance=[provenance])
        self.assertEqual(result["rejected"], [])
        return path, result["accepted"][0]

    def rejection(self, profile):
        prepared = collective_learning.prepare_refresh_rejection(profile)
        self.assertEqual(collective_learning.pending_refresh_rejection(profile), prepared)
        descriptor = prepared["disposition"]
        return self.sign({
            "disposition": "reject_and_refresh",
            "rejectedAt": "2026-09-05T12:00:00Z",
            "rejectedBy": "operator",
            "reason": "Local proposal is not reusable.",
            "workerId": descriptor["workerId"],
            "roleReleaseCommit": descriptor["roleReleaseCommit"],
            "governedStateHash": descriptor["governedStateHash"],
            "dispositionDigest": prepared["dispositionDigest"],
        })

    def test_scenarios_are_required_sanitized_declarative_and_scored_without_execution(self):
        scenario = self.runtime._provenance("skills/candidates/deadline-verification")["agentProposedScenarios"][0]
        with self.assertRaisesRegex(learning.LearningRecordError, "1-10"):
            learning.validate_agent_proposed_scenarios([])
        for change in (
            {"command": "echo bad"},
            {"input": "Send it to person@example.com"},
            {"acceptanceCriteria": [{"observable": "response.text", "operator": "shell", "value": "echo bad"}]},
        ):
            with self.subTest(change=change), self.assertRaises(learning.LearningRecordError):
                learning.validate_agent_proposed_scenarios([{**scenario, **change}])
        self.assertTrue(learning.evaluate_scenario_response(scenario, "Which timezone?")["passed"])
        result = learning.evaluate_scenario_response(scenario, "Confirmed.")
        self.assertFalse(result["passed"])
        self.assertFalse(result["independentHoldout"])

    def test_runtime_rejection_routes_require_authentication_and_signed_disposition(self):
        with tempfile.TemporaryDirectory(dir=runtime_tests.RUNTIME_DIR.parent.parent) as directory:
            _, profile, _ = self.runtime._installed_profile(Path(directory))
            self.candidate(profile)
            app = start_hermes.create_health_app(profile, profile, None, None)
            transport = httpx.ASGITransport(app=app)
            adapter = HermesRuntimeAdapter(
                credential_factory=lambda: object(),
                sandbox_config_factory=lambda: object(),
                ensure_sandbox=lambda *args, **kwargs: SimpleNamespace(
                    sandbox_id="worker", endpoint_url="http://runtime.test", reused_existing_sandbox=True,
                ),
                client_factory=lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs),
            )
            adapter._wait_for_health = AsyncMock()

            async def run():
                async with httpx.AsyncClient(transport=transport, base_url="http://runtime.test") as client:
                    response = await client.post("/internal/collective-learning/prepare-rejection")
                    self.assertEqual(response.status_code, 401)
                    response = await client.post(
                        "/internal/collective-learning/attest-rejection",
                        headers={"X-Autopilot-Key": "operator-key"},
                        json={"receipt": {"approved": True}},
                    )
                    self.assertEqual(response.status_code, 400)
                first = await adapter.prepare_refresh_rejection()
                second = await adapter.prepare_refresh_rejection()
                self.assertEqual(first["dispositionDigest"], second["dispositionDigest"])
                with self.assertRaisesRegex(ValueError, "digest"):
                    await adapter.reject_and_refresh(
                        disposition_digest="wrong", rejected_by="operator", reason="Not reusable.",
                    )
                result = await adapter.reject_and_refresh(
                    disposition_digest=first["dispositionDigest"], rejected_by="operator", reason="Not reusable.",
                )
                self.assertEqual(result["disposition"], "reject_and_refresh")
                self.assertNotIn("approved", result)
                receipt = {key: value for key, value in result.items()
                           if key not in {"sandboxId", "gatewayUrl", "reusedExistingSandbox"}}
                self.assertEqual(collective_learning.attest_refresh_rejection(profile, receipt=receipt), receipt)
                self.assertTrue(collective_learning.worker_refresh_readiness(profile)["ready"])
                with self.assertRaisesRegex(collective_learning.CollectiveLearningError, "No approved"):
                    collective_learning.approved_learning_packet(profile)

            with patch.dict(os.environ, {
                "API_SERVER_KEY": "operator-key",
                "COLLECTIVE_LEARNING_APPROVAL_PRIVATE_KEY": base64.b64encode(
                    self.key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                           serialization.NoEncryption())
                ).decode("ascii"),
            }):
                asyncio.run(run())

    def test_packet_retains_cumulative_records_and_scenarios_and_signs_them(self):
        with tempfile.TemporaryDirectory() as directory:
            _, profile, _ = self.runtime._installed_profile(Path(directory))
            path, first = self.candidate(profile)
            _, second = self.candidate(profile, suffix="\nAsk for the confirming source.\n")
            summary = collective_learning.prepare_learning_packet(profile)
            pending = collective_learning.pending_learning_packet(profile)
            packet = pending["packet"]
            records = packet["improvements"][0]["provenance"]
            self.assertEqual([record["recordId"] for record in records], [first["recordId"], second["recordId"]])
            self.assertEqual(summary["improvements"][0]["agentProposedScenarioCount"], 2)
            self.assertEqual(packet["evaluation"]["executionStatus"], "not_run")
            receipt = self.sign({
                "approved": True, "approvedAt": "2026-09-05T12:00:00Z", "approvedBy": "operator",
                "workerId": "worker-1", "roleReleaseCommit": packet["roleRelease"]["commit"],
                "governedStateHash": packet["governedStateHash"], "packetDigest": pending["packetDigest"],
            })
            records[0]["agentProposedScenarios"][0]["input"] = "Changed scenario input."
            pending_path = profile / "learning" / "exports" / f"{packet['roleRelease']['commit']}.pending.json"
            pending_path.write_text(json.dumps(packet), encoding="utf-8")
            with self.assertRaisesRegex(collective_learning.CollectiveLearningError, "digest"):
                collective_learning.attest_learning_packet(profile, receipt=receipt)
            self.assertTrue(path.exists())

    def test_reverting_to_earlier_skill_content_keeps_intermediate_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            _, profile, _ = self.runtime._installed_profile(Path(directory))
            path, first = self.candidate(profile)
            original = path.read_text(encoding="utf-8")
            _, second = self.candidate(profile, suffix="\nAsk for source.\n")
            token = learning.begin_learning_turn(profile)["token"]
            path.write_text(original, encoding="utf-8")
            result = learning.reconcile_learning_turn(
                profile, token=token,
                provenance=[self.runtime._provenance("skills/candidates/deadline-verification", action="patch")],
            )
            self.assertEqual(len(result["accepted"]), 1)
            collective_learning.prepare_learning_packet(profile)
            records = collective_learning.pending_learning_packet(profile)["packet"]["improvements"][0]["provenance"]
            self.assertEqual(len(records), 3)
            self.assertEqual(records[-1]["artifact"]["afterHash"], first["artifact"]["afterHash"])
            self.assertEqual(records[-1]["artifact"]["beforeHash"], second["artifact"]["afterHash"])

    def test_bundles_deletion_and_bad_frontmatter_are_rolled_back(self):
        with tempfile.TemporaryDirectory() as directory:
            _, profile, _ = self.runtime._installed_profile(Path(directory))
            path, _ = self.candidate(profile)
            original = path.read_bytes()
            for mode in ("bundle", "delete", "frontmatter", "orphan"):
                with self.subTest(mode=mode):
                    token = learning.begin_learning_turn(profile)["token"]
                    if mode == "bundle":
                        (path.parent / "script.py").write_text("print('unsupported')", encoding="utf-8")
                    elif mode == "delete":
                        path.unlink()
                    elif mode == "orphan":
                        (profile / "skills" / "candidates" / "orphan.md").write_text("orphan", encoding="utf-8")
                    else:
                        path.write_text("---\nname: wrong\ndescription: Wrong.\n---\n", encoding="utf-8")
                    result = learning.reconcile_learning_turn(profile, token=token, provenance=[
                        self.runtime._provenance(
                            "skills/candidates/deadline-verification", action="delete" if mode == "delete" else "patch",
                        )
                    ])
                    self.assertTrue(result["rolledBack"])
                    self.assertEqual(path.read_bytes(), original)
                    self.assertFalse((path.parent / "script.py").exists())
                    self.assertFalse((profile / "skills" / "candidates" / "orphan.md").exists())

    def test_rejection_is_local_audited_and_refresh_replaces_whole_role_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, profile, settings = self.runtime._installed_profile(root)
            added_role, _ = self.candidate(profile, namespace="role", name="locally-added")
            candidate, _ = self.candidate(profile)
            private = profile / "skills" / "private" / "my-private" / "SKILL.md"
            private.parent.mkdir(parents=True)
            private.write_text("Private details", encoding="utf-8")
            receipt = self.rejection(profile)
            collective_learning.attest_refresh_rejection(profile, receipt=receipt)
            self.assertTrue(collective_learning.worker_refresh_readiness(profile)["ready"])
            with self.assertRaisesRegex(collective_learning.CollectiveLearningError, "No approved"):
                collective_learning.approved_learning_packet(profile)
            audit = profile / "learning" / "dispositions" / f"{receipt['dispositionDigest']}.json"
            self.assertTrue(audit.exists())
            commit = self.runtime._commit_role_release(repo, "4.0.0", "next")
            blueprint.install_or_refresh_role_release(root / "hermes", self.runtime._settings(repo, "4.0.0", commit))
            self.assertFalse(added_role.exists())
            self.assertFalse(candidate.exists())
            self.assertEqual(private.read_text(encoding="utf-8"), "Private details")
            self.assertTrue(audit.exists())
            with self.assertRaisesRegex(ValueError, "Roll back behavior"):
                blueprint.install_or_refresh_role_release(root / "hermes", settings)

    def test_rejection_rejects_stale_digest_and_later_changes_invalidate_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            _, profile, _ = self.runtime._installed_profile(Path(directory))
            path, _ = self.candidate(profile)
            receipt = self.rejection(profile)
            wrong = {key: value for key, value in receipt.items() if key != "signature"}
            wrong["dispositionDigest"] = "wrong"
            with self.assertRaisesRegex(collective_learning.CollectiveLearningError, "digest"):
                collective_learning.attest_refresh_rejection(profile, receipt=self.sign(wrong))
            collective_learning.attest_refresh_rejection(profile, receipt=receipt)
            path.write_text(path.read_text(encoding="utf-8") + "\nChanged after rejection.\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "blocked"):
                collective_learning.worker_refresh_readiness(profile)

    def test_rejection_allows_unexportable_changes_without_approving_them(self):
        with tempfile.TemporaryDirectory() as directory:
            _, profile, _ = self.runtime._installed_profile(Path(directory))
            path = profile / "skills" / "role" / "junior-project-manager" / "SKILL.md"
            path.write_text(path.read_text(encoding="utf-8") + "\nContact person@example.com.\n", encoding="utf-8")
            with self.assertRaises(collective_learning.CollectiveLearningError):
                collective_learning.prepare_learning_packet(profile)
            collective_learning.attest_refresh_rejection(profile, receipt=self.rejection(profile))
            self.assertTrue(collective_learning.worker_refresh_readiness(profile)["ready"])

    def decision(self):
        return {
            "summary": "Clarify ambiguous deadlines.",
            "proposals": [{
                "action": "create_or_replace", "targetPath": "skills/role/deadline-verification/SKILL.md",
                "content": "---\nname: deadline-verification\ndescription: Clarify missing deadline context.\n---\nAsk for timezone.",
                "rationale": "Observed ambiguity supports narrow clarification.",
                "supportingRecordIds": ["lr-worker-1"], "supportingWorkers": ["worker-1"],
            }],
            "rejected": [], "conflicts": [],
        }

    def test_promotion_requires_complete_correct_record_worker_accounting(self):
        packets = [self.review._packet()]
        for label, change in (
            ("empty records", {"supportingRecordIds": []}),
            ("wrong worker", {"supportingWorkers": ["worker-2"]}),
            ("empty rationale", {"rationale": ""}),
            ("wrong skill name", {"content": "---\nname: wrong\ndescription: Wrong.\n---\n"}),
        ):
            with self.subTest(label=label), self.assertRaises(CollectiveReviewError):
                decision = self.decision()
                decision["proposals"][0].update(change)
                validate_decision(decision, packets=packets)
        decision = self.decision()
        decision["rejected"] = [{"recordId": "lr-worker-1", "reason": "Rejected."}]
        with self.assertRaisesRegex(CollectiveReviewError, "both accepted and rejected"):
            validate_decision(decision, packets=packets)
        decision["rejected"] = []
        decision["proposals"] = []
        with self.assertRaisesRegex(CollectiveReviewError, "accounted"):
            validate_decision(decision, packets=packets)
        decision["rejected"] = [{"recordId": "lr-worker-1", "reason": ""}]
        with self.assertRaisesRegex(CollectiveReviewError, "reason"):
            validate_decision(decision, packets=packets)
        duplicate_packet = self.review._packet("worker-2")
        duplicate_packet["improvements"][0]["provenance"][0]["recordId"] = "lr-worker-1"
        with self.assertRaisesRegex(CollectiveReviewError, "Duplicate provenance"):
            validate_decision(self.decision(), packets=[packets[0], duplicate_packet])

    def test_manifest_rejects_missing_source_approval_and_false_evaluation_claims(self):
        envelope = self.review._envelope("worker-1")
        packets = [envelope["packet"]]
        keys = {"worker-1": self.review.worker_public_key}
        with self.assertRaisesRegex(CollectiveReviewError, "exactly match"):
            build_review_manifest(packets, self.decision(), approved_envelopes=[], worker_public_keys=keys)
        envelope["packet"]["evaluation"]["executionStatus"] = "passed"
        with self.assertRaisesRegex(CollectiveReviewError, "attestation"):
            build_review_manifest(packets, self.decision(), approved_envelopes=[envelope], worker_public_keys=keys)

    def test_old_individual_role_ownership_is_explicitly_rejected(self):
        with self.assertRaisesRegex(ValueError, "whole skills/role"):
            blueprint._validate_owned_path("skills/role/deadline-verification")
        self.assertEqual(blueprint._validate_owned_path("skills/role"), "skills/role")
        with self.assertRaisesRegex(ValueError, "whole skills/role"):
            blueprint._validate_owned_path("skills/private")

    def test_manifest_retains_exact_signed_sources_and_no_pr_for_all_rejected(self):
        envelope = self.review._envelope("worker-1")
        packets = [envelope["packet"]]
        keys = {"worker-1": self.review.worker_public_key}
        manifest = build_review_manifest(
            packets, self.decision(), approved_envelopes=[envelope], worker_public_keys=keys,
        )
        self.assertEqual(manifest["approvedSources"], [envelope])
        self.assertEqual(manifest["recordWorkers"], {"lr-worker-1": "worker-1"})
        self.assertEqual(manifest["evaluation"]["independentHoldout"], "not_supplied")
        decision = {"summary": "Not enough evidence.", "proposals": [],
                    "rejected": [{"recordId": "lr-worker-1", "reason": "Too narrow."}], "conflicts": []}
        with patch("scripts.collective_review._run") as run:
            result = create_role_release_pull_request(
                packets, decision, next_role_release="3.1.0", draft=True,
                approved_envelopes=[envelope], worker_public_keys=keys,
            )
        run.assert_not_called()
        self.assertEqual(result["status"], "no_promotion")


if __name__ == "__main__":
    unittest.main()
