import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from runtimes.hermes.learning import LearningRecordError
from scripts import evaluate_learning as evaluation
from scripts.collective_learning import verify_rejection_receipt
from tests import test_collective_review as review_tests


def scenario():
    return {
        "scenarioId": "clarify-deadline", "input": "When is the report due?",
        "setupAssumptions": ["No timezone was supplied."],
        "expectedObservableOutcomes": ["The response asks for the timezone."],
        "acceptanceCriteria": [{"observable": "response.text", "operator": "contains", "value": "timezone"}],
        "scope": "Response-only clarification.",
    }


class LearningEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.original_cwd = Path.cwd()
        self.directory = tempfile.TemporaryDirectory(prefix=".learning-evaluation-test-", dir=self.original_cwd)
        self.root = Path(self.directory.name)
        os.chdir(self.root)
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(os.chdir, self.original_cwd)
        self.baseline = self.root / "baseline"
        self.candidate = self.root / "candidate"
        for home, instruction in ((self.baseline, "Be concise."), (self.candidate, "Ask for timezone.")):
            skill = home / "skills" / "role" / "clarify-deadline" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(
                "---\nname: clarify-deadline\ndescription: Clarify deadlines.\n---\n" + instruction,
                encoding="utf-8",
            )
            (home / "config.yaml").write_text("model:\n  default: pinned-model\n  provider: custom\n", encoding="utf-8")
            (home / "SOUL.md").write_text("Common role.", encoding="utf-8")
            (home / "memories").mkdir()
            (home / "memories" / "MEMORY.md").write_text("Private context.", encoding="utf-8")
        self.suite = self.root / "independent.json"
        self.suite.write_text(json.dumps({
            "schemaVersion": "1.0", "suiteKind": "independent_holdout", "scenarios": [scenario()],
        }), encoding="utf-8")
        self.parameters = {
            "baseline_profile": self.baseline, "candidate_profile": self.candidate,
            "independent_suite": self.suite, "output": self.root / "report.json",
            "hermes_python": Path(sys.executable),
        }

    def files_and_config(self):
        baseline = evaluation.profile_files(self.baseline)
        candidate = evaluation.profile_files(self.candidate)
        config, model = evaluation.compare_profiles(baseline, candidate)
        return baseline, candidate, config, model

    def usage(self, **overrides):
        return {
            "completed": True, "failed": False, "api_calls": 1, "model": "pinned-model",
            "provider": "custom", "input_tokens": 20, "output_tokens": 10, **overrides,
        }

    def test_missing_or_agent_authored_independent_suite_fails_before_execution(self):
        self.suite.unlink()
        with patch.object(evaluation, "run_response") as run:
            with self.assertRaises(FileNotFoundError):
                evaluation.evaluate(**self.parameters)
            run.assert_not_called()
        self.suite.write_text(json.dumps({
            "schemaVersion": "1.0", "suiteKind": "agent_proposed", "scenarios": [scenario()],
        }), encoding="utf-8")
        with self.assertRaisesRegex(evaluation.EvaluationError, "mandatory"):
            evaluation.load_independent_suite(self.suite)

    def test_independent_suite_rejects_executable_tests(self):
        data = json.loads(self.suite.read_text(encoding="utf-8"))
        data["scenarios"][0]["command"] = "echo unsafe"
        self.suite.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(LearningRecordError):
            evaluation.load_independent_suite(self.suite)

    def test_profile_comparison_requires_same_private_state_and_single_skill_contract(self):
        baseline, candidate, _, _ = self.files_and_config()
        for changed in (
            {**candidate, "memories/MEMORY.md": b"Different private context"},
            {**candidate, "config.yaml": b"model: other"},
        ):
            with self.assertRaisesRegex(evaluation.EvaluationError, "identical"):
                evaluation.compare_profiles(baseline, changed)
        with self.assertRaises(LearningRecordError):
            evaluation.compare_profiles(
                baseline, {**candidate, "skills/role/clarify-deadline/extra.py": b"print('not allowed')"},
            )

    def test_isolation_disables_learning_and_does_not_mutate_profiles(self):
        baseline, _, config, _ = self.files_and_config()
        with patch.dict(os.environ, {
            "HERMES_KANBAN_TASK": "task", "MCP_SERVER": "unsafe", "PYTHONPATH": "unsafe",
            "AZURE_CONFIG_DIR": str(self.root / "shared-azure-login"),
        }):
            home, env = evaluation.isolated_profile(self.root / "run", baseline, config)
        self.assertEqual(home.parent.name, "profiles")
        self.assertEqual(env["HERMES_HOME"], str(home))
        self.assertNotIn("HERMES_KANBAN_TASK", env)
        self.assertNotIn("MCP_SERVER", env)
        self.assertNotIn("PYTHONPATH", env)
        self.assertEqual(env["AZURE_CONFIG_DIR"], str(self.root / "shared-azure-login"))
        self.assertEqual(config["skills"]["creation_nudge_interval"], 0)
        self.assertEqual(config["memory"]["nudge_interval"], 0)
        self.assertFalse(config["skills"]["inline_shell"])
        self.assertEqual(config["platform_toolsets"]["cli"], [])
        self.assertEqual(config["mcp_servers"], {})
        self.assertEqual((home / "memories" / "MEMORY.md").read_bytes(), baseline["memories/MEMORY.md"])
        (home / "SOUL.md").write_text("Changed only the isolated copy.", encoding="utf-8")
        self.assertEqual(evaluation.profile_files(self.baseline), baseline)

    def test_profile_env_overrides_are_not_silently_loaded(self):
        (self.baseline / ".env").write_text("HERMES_HOME=other", encoding="utf-8")
        with self.assertRaisesRegex(evaluation.EvaluationError, "shared authenticated"):
            evaluation.profile_files(self.baseline)

    def test_prompt_includes_skills_and_setup_but_not_expected_answers(self):
        baseline, _, _, _ = self.files_and_config()
        case = scenario()
        case["acceptanceCriteria"][0]["value"] = "assertion-secret"
        case["expectedObservableOutcomes"] = ["expected-secret"]
        prompt = evaluation.scenario_prompt(baseline, case)
        self.assertIn("Be concise.", prompt)
        self.assertIn(case["input"], prompt)
        self.assertNotIn("assertion-secret", prompt)
        self.assertNotIn("expected-secret", prompt)

    def test_installed_hermes_response_only_preflight_without_model_execution(self):
        runtime = Path(__file__).resolve().parents[1] / "runtimes" / "hermes" / ".venv"
        interpreter = runtime / "Scripts" / "python.exe" if os.name == "nt" else runtime / "bin" / "python"
        if not interpreter.is_file():
            self.skipTest("The separate pinned Hermes environment is not installed.")
        baseline, _, config, _ = self.files_and_config()
        home, env = evaluation.isolated_profile(self.root / "preflight", baseline, config)
        result = subprocess.run(
            [str(interpreter), "-I", "-c", evaluation.PREFLIGHT], cwd=home, env=env,
            capture_output=True, text=True, encoding="utf-8", timeout=120, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("HERMES_EVALUATION_PREFLIGHT_OK", result.stdout)

    def test_actual_subprocess_command_and_usage_receipt_contract(self):
        baseline, _, config, model = self.files_and_config()
        home, env = evaluation.isolated_profile(self.root / "run", baseline, config)

        def command(arguments, **kwargs):
            self.assertFalse(kwargs["shell"])
            self.assertIn("-I", arguments)
            self.assertEqual(kwargs["cwd"], home)
            if "-c" in arguments:
                return subprocess.CompletedProcess(arguments, 0, "HERMES_EVALUATION_PREFLIGHT_OK", "")
            self.assertIn("hermes_cli.main", arguments)
            self.assertIn("-z", arguments)
            self.assertNotIn("--ignore-rules", arguments)
            (home / "evaluation-usage.json").write_text(json.dumps(self.usage()), encoding="utf-8")
            return subprocess.CompletedProcess(arguments, 0, "Which timezone?\n", "")

        with patch.object(evaluation.subprocess, "run", side_effect=command):
            response, usage = evaluation.run_response(Path(sys.executable), home, env, model, "Prompt", 30)
        self.assertEqual(response, "Which timezone?")
        self.assertEqual(usage["api_calls"], 1)

    def test_missing_api_call_or_changed_model_is_not_a_quality_pass(self):
        baseline, _, config, model = self.files_and_config()
        home, env = evaluation.isolated_profile(self.root / "run", baseline, config)
        for invalid in ({"api_calls": 0}, {"completed": False}, {"model": "different"}, {"failed": True}):
            with self.subTest(invalid=invalid):
                (home / "evaluation-usage.json").write_text(json.dumps(self.usage(**invalid)), encoding="utf-8")
                with patch.object(evaluation.subprocess, "run", side_effect=[
                    subprocess.CompletedProcess([], 0, "HERMES_EVALUATION_PREFLIGHT_OK", ""),
                    subprocess.CompletedProcess([], 0, "timezone", ""),
                ]), self.assertRaisesRegex(evaluation.EvaluationError, "actual API call"):
                    evaluation.run_response(Path(sys.executable), home, env, model, "Prompt", 30)

    def test_unit_harness_separates_arms_scores_and_removes_private_copies(self):
        baseline, candidate, _, _ = self.files_and_config()
        homes = []

        def response(python, home, env, model, prompt, timeout):
            homes.append(home)
            answer = "Which timezone? Private answer." if "Ask for timezone." in prompt else "Soon. Private answer."
            return answer, self.usage()

        with patch.object(evaluation, "run_response", side_effect=response):
            report = evaluation.evaluate(**self.parameters)
        self.assertEqual(len(set(homes)), 2)
        self.assertTrue(all(not home.exists() for home in homes))
        self.assertEqual(report["status"], "evaluated")
        self.assertEqual(report["comparison"]["independentBaselinePassed"], 0)
        self.assertEqual(report["comparison"]["independentCandidatePassed"], 1)
        self.assertNotIn("Private answer", self.parameters["output"].read_text(encoding="utf-8"))
        self.assertEqual(evaluation.profile_files(self.baseline), baseline)
        self.assertEqual(evaluation.profile_files(self.candidate), candidate)

    def test_execution_failure_persists_explicit_error_not_pass(self):
        with patch.object(evaluation, "run_response", side_effect=evaluation.EvaluationError("Runtime unavailable.")):
            with self.assertRaises(evaluation.EvaluationError):
                evaluation.evaluate(**self.parameters)
        report = json.loads(self.parameters["output"].read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "error")
        self.assertNotIn("comparison", report)
        self.assertEqual(list((self.root / ".artifacts" / "learning-evaluation").iterdir()), [])

    def test_holdout_cannot_reuse_agent_proposed_input(self):
        with patch.object(evaluation, "signed_scenarios", return_value=(
            [{"scenario": scenario(), "source": "agent_proposed", "recordId": "record-1"}], "a" * 64,
        )), patch.object(evaluation, "run_response") as run:
            with self.assertRaisesRegex(evaluation.EvaluationError, "must not duplicate"):
                evaluation.evaluate(**self.parameters)
            run.assert_not_called()

    def test_signed_packet_scenarios_are_bound_to_exact_operator_profile_changes(self):
        fixture = review_tests.CollectiveReviewTests()
        envelope = fixture._envelope("worker-1")
        packet_path = self.root / "approved-packet.json"
        keys_path = self.root / "worker-public-keys.json"
        packet_path.write_text(json.dumps(envelope), encoding="utf-8")
        keys_path.write_text(json.dumps({"worker-1": fixture.worker_public_key}), encoding="utf-8")
        baseline = evaluation.profile_files(self.baseline)
        candidate = {
            **baseline,
            **{key: value.encode("utf-8") for key, value in envelope["packet"]["improvements"][0]["files"].items()},
        }
        cases, digest = evaluation.signed_scenarios(packet_path, keys_path, baseline, candidate)
        self.assertEqual(cases[0]["source"], "agent_proposed")
        self.assertEqual(cases[0]["recordId"], "lr-worker-1")
        self.assertEqual(digest, envelope["receipt"]["packetDigest"])
        candidate["skills/role/clarify-deadline/SKILL.md"] += b"\nUnrelated change."
        with self.assertRaisesRegex(evaluation.EvaluationError, "All compared skill changes"):
            evaluation.signed_scenarios(packet_path, keys_path, baseline, candidate)
        envelope["packet"]["improvements"][0]["provenance"][0]["agentProposedScenarios"][0]["input"] = "Tampered."
        packet_path.write_text(json.dumps(envelope), encoding="utf-8")
        with self.assertRaises(ValueError):
            evaluation.signed_scenarios(packet_path, keys_path, baseline, candidate)

    def test_signed_rejection_verifies_existing_public_key_and_requested_disposition(self):
        key = Ed25519PrivateKey.from_private_bytes(b"\x05" * 32)
        public_key = base64.b64encode(key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw,
        )).decode("ascii")
        receipt = {
            "disposition": "reject_and_refresh", "rejectedAt": "2026-09-05T12:00:00Z",
            "rejectedBy": "operator", "reason": "Not reusable.", "workerId": "worker-1",
            "roleReleaseCommit": "a" * 40, "governedStateHash": "b" * 64, "dispositionDigest": "c" * 64,
        }
        receipt["signature"] = base64.b64encode(key.sign(json.dumps(
            receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode("utf-8"))).decode("ascii")
        parameters = {"public_key": public_key, "digest": "c" * 64, "rejected_by": "operator", "reason": "Not reusable."}
        verify_rejection_receipt(receipt, **parameters)
        for changes in ({"approved": True}, {"reason": "Other reason."}, {"workerId": "other-worker"}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                verify_rejection_receipt({**receipt, **changes}, **parameters)


if __name__ == "__main__":
    unittest.main()
