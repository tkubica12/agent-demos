import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


RUNTIME_DIR = Path(__file__).resolve().parents[1] / "runtimes" / "hermes"
sys.path.insert(0, str(RUNTIME_DIR))

import start_hermes  # noqa: E402
from blueprint import (  # noqa: E402
    RoleReleaseSettings,
    install_or_refresh_role_release,
    role_release_settings_from_environment,
)
from collective_learning import (  # noqa: E402
    attest_learning_packet,
    pending_learning_packet,
    prepare_learning_packet,
)
from learning import (  # noqa: E402
    LearningRecordError,
    abort_learning_turn,
    assert_legacy_state_migrated,
    begin_learning_turn,
    build_learning_status,
    initialize_governed_state,
    reconcile_learning_turn,
    stored_learning_records,
    validate_skill_namespaces,
)


class HermesRuntimeTests(unittest.TestCase):
    def _git(self, repo: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def _commit_role_release(
        self,
        repo: Path,
        role_release: str,
        marker: str,
    ) -> str:
        distribution = repo / "blueprints" / "junior-project-manager"
        role_skill = distribution / "skills" / "role" / "junior-project-manager"
        dream_skill = distribution / "skills" / "role" / "dream-reflection"
        if distribution.exists():
            shutil.rmtree(distribution)
        role_skill.mkdir(parents=True)
        dream_skill.mkdir(parents=True)
        (distribution / "distribution.yaml").write_text(
            "\n".join(
                [
                    "role_blueprint: junior-project-manager",
                    f"role_release: {role_release}",
                    "distribution_owned:",
                    "  - SOUL.md",
                    "  - config.yaml",
                    "  - skills/role/junior-project-manager",
                    "  - skills/role/dream-reflection",
                    "  - distribution.yaml",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (distribution / "SOUL.md").write_text(f"# {marker}\n", encoding="utf-8")
        (distribution / "config.yaml").write_text("custom:\n  roleDefault: true\n", encoding="utf-8")
        (role_skill / "SKILL.md").write_text(
            f"---\nname: junior-project-manager\ndescription: {marker} role behavior.\n---\n\n# {marker}\n",
            encoding="utf-8",
        )
        (dream_skill / "SKILL.md").write_text(
            "---\nname: dream-reflection\ndescription: Reflect over Work History.\n---\n",
            encoding="utf-8",
        )
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-m", f"Role Release {role_release}")
        return self._git(repo, "rev-parse", "HEAD")

    @staticmethod
    def _settings(repo: Path, role_release: str, commit: str) -> RoleReleaseSettings:
        return RoleReleaseSettings(
            role_blueprint="junior-project-manager",
            source=str(repo),
            repository_path="blueprints/junior-project-manager",
            role_release=role_release,
            commit=commit,
            worker_id="worker-1",
            assignment_scope="team-alpha",
        )

    @staticmethod
    def _provenance(
        artifact_path: str,
        *,
        classification: str = "candidate_improvement",
        action: str = "create",
        source_stage: str = "foreground",
    ) -> dict:
        return {
            "classification": classification,
            "artifactPath": artifact_path,
            "action": action,
            "title": "Verify deadline evidence",
            "generalizedLearning": "Record the source, timezone, and confirmer before accepting a deadline.",
            "rationale": "Traceable deadlines prevent ambiguous commitments.",
            "evidence": [
                {
                    "sourceType": "private_session",
                    "summary": "Several generalized handoffs lacked traceable deadline evidence.",
                }
            ],
            "confidence": 0.94,
            "sourceStage": source_stage,
        }

    def _installed_profile(self, root: Path) -> tuple[Path, Path, RoleReleaseSettings]:
        repo = root / "source"
        home = root / "hermes"
        repo.mkdir()
        self._git(repo, "init")
        self._git(repo, "config", "user.email", "test@example.com")
        self._git(repo, "config", "user.name", "Hermes Test")
        commit = self._commit_role_release(repo, "3.0.0", "release-3")
        settings = self._settings(repo, "3.0.0", commit)
        installed = install_or_refresh_role_release(home, settings)
        return repo, installed.profile_home, settings

    def test_role_release_install_writes_worker_manifest_and_role_baseline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, profile, settings = self._installed_profile(Path(temp_dir))
            manifest = json.loads((profile / "local" / "worker.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["roleBlueprint"], "junior-project-manager")
        self.assertEqual(manifest["roleRelease"], "3.0.0")
        self.assertEqual(manifest["roleReleaseCommit"], settings.commit)
        self.assertEqual(manifest["workerId"], "worker-1")
        self.assertIn(
            "skills/role/junior-project-manager/SKILL.md",
            manifest["roleSkillBaseline"],
        )
        self.assertNotIn("skills/private", manifest["distributionOwned"])

    def test_same_role_release_is_reused(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo, profile, settings = self._installed_profile(root)
            (profile / "memories" / "MEMORY.md").write_text("private memory\n", encoding="utf-8")
            reused = install_or_refresh_role_release(root / "hermes", settings)
            memory_after = (profile / "memories" / "MEMORY.md").read_text(encoding="utf-8")

        self.assertFalse(reused.changed)
        self.assertEqual(memory_after, "private memory\n")

    def test_private_playbook_and_work_history_survive_worker_refresh(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo, profile, settings = self._installed_profile(root)
            private_skill = profile / "skills" / "private" / "cedar-delivery" / "SKILL.md"
            private_skill.parent.mkdir(parents=True)
            private_skill.write_text(
                "---\nname: cedar-delivery\ndescription: Private Cedar delivery playbook.\n---\n",
                encoding="utf-8",
            )
            (profile / "memories" / "USER.md").write_text("prefers bullets\n", encoding="utf-8")
            (profile / "state.db").write_bytes(b"private work history")
            commit_v4 = self._commit_role_release(repo, "4.0.0", "release-4")

            refreshed = install_or_refresh_role_release(
                root / "hermes",
                self._settings(repo, "4.0.0", commit_v4),
            )
            private_exists = private_skill.exists()
            user_after = (profile / "memories" / "USER.md").read_text(encoding="utf-8")
            history_after = (profile / "state.db").read_bytes()
            soul_after = (profile / "SOUL.md").read_text(encoding="utf-8")

        self.assertTrue(refreshed.changed)
        self.assertTrue(private_exists)
        self.assertEqual(user_after, "prefers bullets\n")
        self.assertEqual(history_after, b"private work history")
        self.assertIn("# release-4", soul_after)
        self.assertNotEqual(settings.commit, commit_v4)

    def test_worker_refresh_requires_approved_export_for_governed_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo, profile, settings = self._installed_profile(root)
            token = begin_learning_turn(profile)["token"]
            candidate = profile / "skills" / "candidates" / "deadline-verification" / "SKILL.md"
            candidate.parent.mkdir(parents=True)
            candidate.write_text(
                "---\nname: deadline-verification\ndescription: Verify deadline evidence.\n---\n",
                encoding="utf-8",
            )
            reconcile_learning_turn(
                profile,
                token=token,
                provenance=[self._provenance("skills/candidates/deadline-verification")],
            )
            commit_v4 = self._commit_role_release(repo, "4.0.0", "release-4")

            with self.assertRaisesRegex(RuntimeError, "approved export receipt"):
                install_or_refresh_role_release(
                    root / "hermes",
                    self._settings(repo, "4.0.0", commit_v4),
                )

            private_key = Ed25519PrivateKey.from_private_bytes(b"\x04" * 32)
            previous_key = os.environ.get("COLLECTIVE_LEARNING_APPROVAL_PUBLIC_KEY")
            os.environ["COLLECTIVE_LEARNING_APPROVAL_PUBLIC_KEY"] = base64.b64encode(
                private_key.public_key().public_bytes(
                    encoding=serialization.Encoding.Raw,
                    format=serialization.PublicFormat.Raw,
                )
            ).decode("ascii")
            try:
                prepare_learning_packet(profile)
                pending = pending_learning_packet(profile)
                packet = pending["packet"]
                receipt = {
                    "approved": True,
                    "approvedAt": "2026-07-16T08:01:00Z",
                    "approvedBy": "operator",
                    "workerId": packet["worker"]["workerId"],
                    "roleReleaseCommit": packet["roleRelease"]["commit"],
                    "governedStateHash": packet["governedStateHash"],
                    "packetDigest": pending["packetDigest"],
                }
                receipt["signature"] = base64.b64encode(
                    private_key.sign(
                        json.dumps(
                            receipt,
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=True,
                        ).encode("utf-8")
                    )
                ).decode("ascii")
                attest_learning_packet(profile, receipt=receipt)
                install_or_refresh_role_release(
                    root / "hermes",
                    self._settings(repo, "4.0.0", commit_v4),
                )
            finally:
                if previous_key is None:
                    os.environ.pop("COLLECTIVE_LEARNING_APPROVAL_PUBLIC_KEY", None)
                else:
                    os.environ["COLLECTIVE_LEARNING_APPROVAL_PUBLIC_KEY"] = previous_key
            candidate_exists = candidate.exists()
            archive_exists = (
                profile / "learning" / "archive" / "role-release-3.0.0" / "candidate-improvements"
            ).exists()

        self.assertFalse(candidate_exists)
        self.assertTrue(archive_exists)

    def test_worker_refresh_rejects_role_release_rollback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo, profile, _ = self._installed_profile(root)
            older_commit = self._commit_role_release(repo, "2.9.0", "older")

            with self.assertRaisesRegex(ValueError, "newer Role Release"):
                install_or_refresh_role_release(
                    root / "hermes",
                    self._settings(repo, "2.9.0", older_commit),
                )
            soul_after = (profile / "SOUL.md").read_text(encoding="utf-8")

        self.assertIn("# release-3", soul_after)

    def test_failed_worker_refresh_restores_previous_role_release(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo, profile, settings = self._installed_profile(root)
            original_soul = (profile / "SOUL.md").read_text(encoding="utf-8")
            original_manifest = (profile / "local" / "worker.json").read_text(encoding="utf-8")
            commit_v4 = self._commit_role_release(repo, "4.0.0", "release-4")

            def fail_after_partial_copy(distribution_root, profile_home, owned_paths):
                (profile_home / "SOUL.md").write_text("# partial release\n", encoding="utf-8")
                raise OSError("simulated copy failure")

            with patch("blueprint._copy_owned_paths", side_effect=fail_after_partial_copy):
                with self.assertRaisesRegex(OSError, "simulated copy failure"):
                    install_or_refresh_role_release(
                        root / "hermes",
                        self._settings(repo, "4.0.0", commit_v4),
                    )

            restored_soul = (profile / "SOUL.md").read_text(encoding="utf-8")
            restored_manifest = (profile / "local" / "worker.json").read_text(encoding="utf-8")
            transaction_exists = (profile / "learning" / "refresh-transaction").exists()

        self.assertEqual(restored_soul, original_soul)
        self.assertEqual(restored_manifest, original_manifest)
        self.assertFalse(transaction_exists)
        self.assertNotEqual(settings.commit, commit_v4)

    def test_candidate_improvement_change_accepts_linked_provenance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, profile, _ = self._installed_profile(root)
            token = begin_learning_turn(profile)["token"]
            skill = profile / "skills" / "candidates" / "deadline-verification" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(
                "---\nname: deadline-verification\ndescription: Verify external deadline evidence.\n---\n"
                "\n# Deadline verification\n\nRecord source, timezone, and confirmer.\n",
                encoding="utf-8",
            )

            result = reconcile_learning_turn(
                profile,
                token=token,
                provenance=[self._provenance("skills/candidates/deadline-verification")],
            )
            records, rejected = stored_learning_records(profile)
            skill_exists = skill.exists()

        self.assertFalse(result["rolledBack"])
        self.assertEqual(len(result["accepted"]), 1)
        self.assertEqual(result["accepted"][0]["artifact"]["path"], "skills/candidates/deadline-verification")
        self.assertIsNone(result["accepted"][0]["artifact"]["beforeHash"])
        self.assertEqual(len(records), 1)
        self.assertEqual(rejected, [])
        self.assertTrue(skill_exists)

    def test_role_skill_patch_accepts_linked_provenance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, profile, _ = self._installed_profile(root)
            token = begin_learning_turn(profile)["token"]
            skill = profile / "skills" / "role" / "junior-project-manager" / "SKILL.md"
            skill.write_text(skill.read_text(encoding="utf-8") + "\nRequire rollback evidence.\n", encoding="utf-8")

            result = reconcile_learning_turn(
                profile,
                token=token,
                provenance=[
                    self._provenance(
                        "skills/role/junior-project-manager",
                        classification="role_skill_improvement",
                        action="patch",
                    )
                ],
            )
            skill_after = skill.read_text(encoding="utf-8")

        self.assertEqual(len(result["accepted"]), 1)
        self.assertIsNotNone(result["accepted"][0]["artifact"]["beforeHash"])
        self.assertIsNotNone(result["accepted"][0]["artifact"]["afterHash"])
        self.assertIn("Require rollback evidence", skill_after)

    def test_unprovenanced_governed_change_is_rolled_back(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, profile, _ = self._installed_profile(root)
            token = begin_learning_turn(profile)["token"]
            role_skill = profile / "skills" / "role" / "junior-project-manager" / "SKILL.md"
            before = role_skill.read_text(encoding="utf-8")
            role_skill.write_text(before + "\nUnsafe unprovenanced patch.\n", encoding="utf-8")

            result = reconcile_learning_turn(profile, token=token, provenance=[])
            role_after = role_skill.read_text(encoding="utf-8")

        self.assertTrue(result["rolledBack"])
        self.assertIn("no matching provenance", result["rejected"][0]["reason"])
        self.assertEqual(role_after, before)

    def test_private_playbook_change_never_requires_provenance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, profile, _ = self._installed_profile(root)
            token = begin_learning_turn(profile)["token"]
            playbook = profile / "skills" / "private" / "cedar-delivery" / "SKILL.md"
            playbook.parent.mkdir(parents=True)
            playbook.write_text(
                "---\nname: cedar-delivery\ndescription: Private Cedar workflow.\n---\n",
                encoding="utf-8",
            )

            result = reconcile_learning_turn(profile, token=token, provenance=[])
            records, _ = stored_learning_records(profile)
            playbook_exists = playbook.exists()

        self.assertFalse(result["rolledBack"])
        self.assertEqual(result["privatePlaybooksChanged"], ["skills/private/cedar-delivery"])
        self.assertEqual(records, [])
        self.assertTrue(playbook_exists)

    def test_private_content_in_candidate_improvement_is_rejected_and_rolled_back(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, profile, _ = self._installed_profile(root)
            token = begin_learning_turn(profile)["token"]
            skill = profile / "skills" / "candidates" / "unsafe-learning" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(
                "---\nname: unsafe-learning\ndescription: Contact owner@example.com.\n---\n",
                encoding="utf-8",
            )

            result = reconcile_learning_turn(
                profile,
                token=token,
                provenance=[self._provenance("skills/candidates/unsafe-learning")],
            )

        self.assertTrue(result["rolledBack"])
        self.assertIn("email address", result["rejected"][0]["reason"])
        self.assertFalse(skill.exists())

    def test_next_turn_recovers_unprovenanced_background_skill_drift(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, profile, _ = self._installed_profile(root)
            first_token = begin_learning_turn(profile)["token"]
            skill = profile / "skills" / "candidates" / "deadline-verification" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            original = (
                "---\nname: deadline-verification\ndescription: Verify deadline evidence.\n---\n"
            )
            skill.write_text(original, encoding="utf-8")
            reconcile_learning_turn(
                profile,
                token=first_token,
                provenance=[self._provenance("skills/candidates/deadline-verification")],
            )
            skill.write_text(original + "\nUnprovenanced background patch.\n", encoding="utf-8")

            next_turn = begin_learning_turn(profile)
            recovered_content = skill.read_text(encoding="utf-8")

        self.assertEqual(
            next_turn["recoveredUnprovenancedFiles"],
            ["skills/candidates/deadline-verification/SKILL.md"],
        )
        self.assertEqual(recovered_content, original)

    def test_quarantined_cli_candidate_can_be_reconciled_with_provenance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, profile, _ = self._installed_profile(root)
            initialize_governed_state(profile)
            candidate = profile / "skills" / "candidates" / "meeting-decision-record" / "SKILL.md"
            candidate.parent.mkdir(parents=True)
            content = (
                "---\nname: meeting-decision-record\n"
                "description: Record complete meeting decisions.\n---\n"
            )
            candidate.write_text(content, encoding="utf-8")

            turn = begin_learning_turn(profile)
            self.assertFalse(candidate.exists())
            candidate.parent.mkdir(parents=True)
            candidate.write_text(content, encoding="utf-8")
            result = reconcile_learning_turn(
                profile,
                token=turn["token"],
                provenance=[
                    self._provenance(
                        "skills/candidates/meeting-decision-record"
                    )
                ],
            )
            quarantine_path = next(
                (profile / "learning" / "quarantine").glob("unprovenanced-*.json")
            )
            quarantine = json.loads(quarantine_path.read_text(encoding="utf-8"))

        self.assertEqual(
            turn["recoveredUnprovenancedFiles"],
            ["skills/candidates/meeting-decision-record/SKILL.md"],
        )
        self.assertEqual(len(result["accepted"]), 1)
        self.assertEqual(quarantine["status"], "reconciled")

    def test_duplicate_provenance_without_content_change_is_skipped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, profile, _ = self._installed_profile(root)
            first_token = begin_learning_turn(profile)["token"]
            skill = profile / "skills" / "candidates" / "deadline-verification" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(
                "---\nname: deadline-verification\ndescription: Verify deadline evidence.\n---\n",
                encoding="utf-8",
            )
            first = reconcile_learning_turn(
                profile,
                token=first_token,
                provenance=[self._provenance("skills/candidates/deadline-verification")],
            )
            duplicate_token = begin_learning_turn(profile)["token"]
            duplicate = reconcile_learning_turn(
                profile,
                token=duplicate_token,
                provenance=[self._provenance("skills/candidates/deadline-verification")],
            )

        self.assertEqual(duplicate["accepted"], [])
        self.assertEqual(duplicate["rejected"], [])
        self.assertEqual(
            duplicate["skippedDuplicates"],
            [
                {
                    "artifactPath": "skills/candidates/deadline-verification",
                    "recordId": first["accepted"][0]["recordId"],
                }
            ],
        )

    def test_profile_learning_lease_rejects_concurrent_turns(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, profile, _ = self._installed_profile(root)
            first = begin_learning_turn(profile)
            with self.assertRaisesRegex(LearningRecordError, "transaction is active"):
                begin_learning_turn(profile)
            reconcile_learning_turn(profile, token=first["token"], provenance=[])
            second = begin_learning_turn(profile)
            reconcile_learning_turn(profile, token=second["token"], provenance=[])

        self.assertNotEqual(first["token"], second["token"])

    def test_abort_learning_turn_rolls_back_and_releases_lease(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, profile, _ = self._installed_profile(root)
            role = profile / "skills" / "role" / "junior-project-manager" / "SKILL.md"
            before = role.read_text(encoding="utf-8")
            turn = begin_learning_turn(profile)
            role.write_text(before + "\nPartial failed change.\n", encoding="utf-8")

            result = abort_learning_turn(profile, token=turn["token"])
            after = role.read_text(encoding="utf-8")
            retry = begin_learning_turn(profile)
            reconcile_learning_turn(profile, token=retry["token"], provenance=[])

        self.assertTrue(result["aborted"])
        self.assertEqual(after, before)

    def test_learning_status_contains_provenance_and_excludes_private_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, profile, _ = self._installed_profile(root)
            token = begin_learning_turn(profile)["token"]
            skill = profile / "skills" / "candidates" / "deadline-verification" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(
                "---\nname: deadline-verification\ndescription: Verify deadline evidence.\n---\n",
                encoding="utf-8",
            )
            reconcile_learning_turn(
                profile,
                token=token,
                provenance=[self._provenance("skills/candidates/deadline-verification")],
            )
            packet = build_learning_status(profile)

        self.assertEqual(packet["statusVersion"], "2.0")
        self.assertEqual(packet["worker"]["workerId"], "worker-1")
        self.assertEqual(packet["roleRelease"]["release"], "3.0.0")
        self.assertEqual(len(packet["records"]), 1)
        self.assertIn("skills/private/", packet["privatePathsExcluded"])

    def test_skill_basename_collisions_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, profile, _ = self._installed_profile(root)
            private = profile / "skills" / "private" / "junior-project-manager" / "SKILL.md"
            private.parent.mkdir(parents=True)
            private.write_text("---\nname: junior-project-manager\ndescription: Collision.\n---\n", encoding="utf-8")

            with self.assertRaisesRegex(LearningRecordError, "collides across namespaces"):
                validate_skill_namespaces(profile)

    def test_legacy_private_cache_and_hot_learning_must_be_migrated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = Path(temp_dir)
            cache = profile / "local" / "private-cache.md"
            cache.parent.mkdir(parents=True)
            cache.write_text("Customer-specific fact.\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "Private Playbook"):
                assert_legacy_state_migrated(profile)

            cache.unlink()
            hot = profile / "skills" / "hot-learning" / "SKILL.md"
            hot.parent.mkdir(parents=True)
            hot.write_text("# Legacy\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "Candidate Improvements"):
                assert_legacy_state_migrated(profile)

    def test_wrapper_routes_gateway_to_separate_internal_port(self):
        previous = {
            key: os.environ.get(key)
            for key in ["HERMES_HEALTH_WRAPPER", "API_SERVER_PORT", "HERMES_GATEWAY_PORT"]
        }
        os.environ["HERMES_HEALTH_WRAPPER"] = "true"
        os.environ["API_SERVER_PORT"] = "8642"
        os.environ["HERMES_GATEWAY_PORT"] = "9119"
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                config = start_hermes.hermes_config(Path(temp_dir))
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertEqual(config["gateway"]["platforms"]["api_server"]["port"], 9119)

    def test_role_release_settings_require_new_environment_names(self):
        names = [
            "HERMES_ROLE_BLUEPRINT",
            "HERMES_ROLE_BLUEPRINT_SOURCE",
            "HERMES_ROLE_BLUEPRINT_PATH",
            "HERMES_ROLE_RELEASE",
            "HERMES_ROLE_RELEASE_COMMIT",
            "WORKER_ID",
            "WORKER_ASSIGNMENT_SCOPE",
        ]
        previous = {name: os.environ.get(name) for name in names}
        values = {
            "HERMES_ROLE_BLUEPRINT": "junior-project-manager",
            "HERMES_ROLE_BLUEPRINT_SOURCE": "https://example.com/roles.git",
            "HERMES_ROLE_BLUEPRINT_PATH": "roles/junior-project-manager",
            "HERMES_ROLE_RELEASE": "3.0.0",
            "HERMES_ROLE_RELEASE_COMMIT": "a" * 40,
            "WORKER_ID": "hermes1",
            "WORKER_ASSIGNMENT_SCOPE": "team-alpha",
        }
        os.environ.update(values)
        try:
            settings = role_release_settings_from_environment()
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        self.assertIsNotNone(settings)
        self.assertEqual(settings.role_release, "3.0.0")
        self.assertEqual(settings.worker_id, "hermes1")

    def test_a9_environment_is_rejected_explicitly(self):
        previous = {
            name: os.environ.get(name)
            for name in (
                "HERMES_BLUEPRINT_SOURCE",
                "HERMES_ROLE_BLUEPRINT_SOURCE",
            )
        }
        os.environ["HERMES_BLUEPRINT_SOURCE"] = "https://example.com/legacy.git"
        os.environ.pop("HERMES_ROLE_BLUEPRINT_SOURCE", None)
        try:
            with self.assertRaisesRegex(ValueError, "A9 Hermes blueprint environment"):
                role_release_settings_from_environment()
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_a9_worker_manifest_blocks_role_release_install_before_mutation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "source"
            home = root / "hermes"
            profile = home / "profiles" / "junior-project-manager"
            legacy_manifest = profile / "local" / "autopilots-instance.json"
            legacy_manifest.parent.mkdir(parents=True)
            legacy_manifest.write_text('{"blueprintVersion":"2.3.0"}', encoding="utf-8")
            legacy_skill = profile / "skills" / "junior-project-manager" / "SKILL.md"
            legacy_skill.parent.mkdir(parents=True)
            legacy_skill.write_text("# Legacy role skill\n", encoding="utf-8")
            repo.mkdir()
            self._git(repo, "init")
            self._git(repo, "config", "user.email", "test@example.com")
            self._git(repo, "config", "user.name", "Hermes Test")
            commit = self._commit_role_release(repo, "3.0.0", "release-3")

            with self.assertRaisesRegex(RuntimeError, "A9 Worker profile migration"):
                install_or_refresh_role_release(
                    home,
                    self._settings(repo, "3.0.0", commit),
                )
            legacy_content = legacy_skill.read_text(encoding="utf-8")

        self.assertEqual(legacy_content, "# Legacy role skill\n")


if __name__ == "__main__":
    unittest.main()
