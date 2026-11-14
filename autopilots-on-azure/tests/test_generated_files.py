import asyncio
import base64
import hashlib
import io
import json
import os
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import zipfile

from bridge.generated_files import (
    GeneratedFileError, GeneratedFileStore, MAX_EVIDENCE_BYTES, MAX_FILE_BYTES,
    NativeSkillLoadEvidence, completed_run_evidence, conversation_file_scope,
    DOCX_CONTENT_TYPE, input_file_scope,
)


class GeneratedFileEvidenceTests(unittest.TestCase):
    def skill_evidence(self, events, snapshot):
        wire = b"".join(
            ("data: " + json.dumps({"run_id": "run-1", **event}) + "\n\n").encode()
            for event in [*events, {"event": "run.completed"}]
        )
        return completed_run_evidence("run-1", {"run_id": "run-1", "status": "completed"}, wire, snapshot)

    def test_actual_skill_content_is_joined_only_to_its_successful_native_tool_pair(self):
        collector = NativeSkillLoadEvidence()
        content = "---\nname: teams-html-widget\n---\n# Test skill\n"
        collector.record(
            run_id="run-1", tool_name="skill_view", args={"name": "teams-html-widget", "secret": "hidden"},
            result=json.dumps({"success": True, "name": "teams-html-widget", "content": content, "secret": "hidden"}),
            status="ok", tool_call_id="call-1", duration_ms=123,
        )
        snapshot = collector.snapshot("run-1")
        actual = self.skill_evidence([
            {"event": "tool.started", "tool": "skill_view", "tool_call_id": "call-1", "preview": content},
            {"event": "tool.completed", "tool": "skill_view", "tool_call_id": "call-1", "duration": 0.123, "error": False},
        ], snapshot)
        self.assertEqual(actual["tools"][-1], {
            "event": "tool.completed", "tool": "skill_view", "toolCallId": "call-1",
            "duration": 0.123, "error": False, "skillName": "teams-html-widget",
            "skillContentReturned": True, "skillContentSha256": hashlib.sha256(content.encode()).hexdigest(),
        })
        self.assertNotIn("hidden", json.dumps(snapshot))
        self.assertNotIn("Test skill", json.dumps(actual))
        self.assertEqual(collector.snapshot("other-run")["loads"], [])

    def test_failed_missing_wrong_deduplicated_and_linked_file_skill_loads_prove_nothing(self):
        success = {"success": True, "name": "teams-html-widget", "content": "Test skill"}
        for changes, arguments, outcome in (
            ({}, {"name": "teams-html-widget"}, "error"),
            ({"success": False}, {"name": "teams-html-widget"}, "ok"),
            ({"name": None}, {"name": "teams-html-widget"}, "ok"),
            ({"name": "another-skill"}, {"name": "teams-html-widget"}, "ok"),
            ({"content": None}, {"name": "teams-html-widget"}, "ok"),
            ({"content": ""}, {"name": "teams-html-widget"}, "ok"),
            ({"dedup": True}, {"name": "teams-html-widget"}, "ok"),
            ({"content_returned": False}, {"name": "teams-html-widget"}, "ok"),
            ({"status": "unchanged"}, {"name": "teams-html-widget"}, "ok"),
            ({}, {"name": "teams-html-widget", "file_path": "SKILL.md"}, "ok"),
            ({}, {}, "ok"),
        ):
            with self.subTest(changes=changes, arguments=arguments, outcome=outcome):
                collector = NativeSkillLoadEvidence()
                collector.record(
                    run_id="run-1", tool_name="skill_view", args=arguments,
                    result=json.dumps({**success, **changes}), status=outcome, tool_call_id="call-1",
                )
                self.assertEqual(collector.snapshot("run-1")["loads"], [])

    def test_skill_proof_requires_matching_started_completed_ids_and_successful_bounded_duration(self):
        snapshot = {"runId": "run-1", "loads": [{
            "toolCallId": "call-1", "skillName": "teams-html-widget", "skillContentReturned": True,
            "skillContentSha256": "a" * 64,
        }]}
        start = {"event": "tool.started", "tool": "skill_view", "tool_call_id": "call-1"}
        done = {"event": "tool.completed", "tool": "skill_view", "tool_call_id": "call-1", "duration": 0.1, "error": False}
        for events in (
            [start], [done], [done, start],
            [start, {**done, "tool_call_id": "call-2"}], [start, {**done, "error": True}],
            [start, {**done, "duration": 3601}], [start, {**done, "tool": "terminal"}],
        ):
            with self.subTest(events=events):
                actual = self.skill_evidence(events, snapshot)
                self.assertTrue(all("skillName" not in tool for tool in actual["tools"]))
        with self.assertRaises(GeneratedFileError):
            self.skill_evidence([start, done], {**snapshot, "runId": "other-run"})
        with self.assertRaises(GeneratedFileError):
            self.skill_evidence([start, done, start, done], snapshot)

    def test_native_observer_uses_run_context_not_task_session_or_api_request_identity(self):
        collector = NativeSkillLoadEvidence()
        scope = SimpleNamespace(get_current_session_key=lambda default: "run-1")
        with (
            patch.dict(os.environ, {"GENERATED_CHAT_FILES_ENABLED": "true"}),
            patch.dict(sys.modules, {"tools.approval_context": scope}),
        ):
            collector.observe(
                tool_name="skill_view", args={"name": "teams-html-widget"},
                result=json.dumps({"success": True, "name": "teams-html-widget", "content": "Test skill"}),
                status="ok", tool_call_id="call-1", duration_ms=123,
                task_id="wrong-task", session_id="wrong-session", api_request_id="wrong-request",
            )
        self.assertEqual(len(collector.snapshot("run-1")["loads"]), 1)
        self.assertEqual(collector.snapshot("wrong-task")["loads"], [])

    def test_only_native_safe_status_and_tool_fields_are_returned(self):
        status = {
            "run_id": "run-1", "status": "completed", "output": "secret",
            "generatedAttachments": {"claim": "secret"}, "instructions": "secret",
            "runtime": {"provider": "azure-foundry", "model": "gpt-5", "route_source": "global", "key": "secret"},
            "usage": {"input_tokens": 7, "output_tokens": 3, "key": "secret"},
        }
        events = [
            {"event": "tool.started", "tool": "terminal", "tool_call_id": "call-1", "preview": "secret"},
            {"event": "tool.completed", "tool": "terminal", "tool_call_id": "call-1", "duration": 1.25, "error": False,
             "preview": "secret"},
            {"event": "reasoning.available", "text": "secret"},
            {"event": "run.completed", "output": "secret"},
        ]
        wire = b"".join(
            ("data: " + json.dumps({"run_id": "run-1", **event}) + "\n\n").encode() for event in events
        )
        actual = completed_run_evidence("run-1", status, wire)
        self.assertEqual(actual["runtime"], {"provider": "azure-foundry", "model": "gpt-5", "route_source": "global"})
        self.assertEqual(actual["usage"], {"input_tokens": 7, "output_tokens": 3})
        self.assertEqual(actual["tools"], [
            {"event": "tool.started", "tool": "terminal", "toolCallId": "call-1"},
            {"event": "tool.completed", "tool": "terminal", "toolCallId": "call-1", "duration": 1.25, "error": False},
        ])
        self.assertNotIn("secret", json.dumps(actual))

    def test_mismatched_truncated_incomplete_invalid_and_oversized_evidence_is_refused(self):
        status = {"run_id": "run-1", "status": "completed"}
        for data in (
            b'data: {"run_id":"other","event":"run.completed"}\n',
            b'data: {"run_id":"run-1","event":"replay.truncated"}\n',
            b'data: {"run_id":"run-1","event":"tool.started","tool":"terminal"}\n',
            b"data: not-json\n", b"\xff", b"x" * (MAX_EVIDENCE_BYTES + 1),
        ):
            with self.subTest(data=data[:80]), self.assertRaises(GeneratedFileError):
                completed_run_evidence("run-1", status, data)
        for invalid in ({**status, "run_id": "other"}, {**status, "status": "running"}):
            with self.assertRaises(GeneratedFileError):
                completed_run_evidence("run-1", invalid, b"")

    def test_missing_served_model_is_explicitly_unavailable_not_inferred(self):
        actual = completed_run_evidence(
            "run-1", {"run_id": "run-1", "status": "completed", "runtime": {"provider": "", "model": ""}},
            b'data: {"run_id":"run-1","event":"run.completed"}\n',
        )
        self.assertFalse(actual["runtimeMetadataAvailable"])
        self.assertIsNone(actual["runtime"]["model"])


@unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "Native generated-file storage requires Linux")
class GeneratedFileStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = GeneratedFileStore(self.root / "exports", worker_id="worker-1")
        self.scope = conversation_file_scope("worker-1", "teams_personal", "conversation-1")
        self.turn = self.store.admit("admission-1", session_id="session-1", scope=self.scope)
        self.store.bind_run(self.turn["turnId"], "run-1")
        self.workspace = self.store._turn(self.turn["turnId"]) / "workspace"

    def original_docx(self):
        parts = {
            "[Content_Types].xml": (
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="png" ContentType="image/png"/>'
                '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
                '</Types>'
            ).encode(),
            "_rels/.rels": (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                '</Relationships>'
            ).encode(),
            "word/document.xml": (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body><w:p><w:r><w:t>Original document text</w:t></w:r></w:p></w:body></w:document>'
            ).encode(),
            "word/styles.xml": (
                '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:style w:type="paragraph" w:styleId="OriginalStyle"><w:name w:val="Original Style"/></w:style></w:styles>'
            ).encode(),
            "word/media/image.png": base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3ioAAAAASUVORK5CYII="
            ),
        }
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as package:
            for name, data in parts.items():
                package.writestr(name, data)
        return output.getvalue(), parts

    def stage_original(self, data, **overrides):
        async def chunks():
            yield data[:7]
            yield data[7:]

        settings = {
            "session_id": "private:session-1", "scope": self.scope,
            "input_scope": input_file_scope("worker-1", "teams_personal", "conversation-1", "user-1"),
            "index": 1, "name": "original.docx", "content_type": DOCX_CONTENT_TYPE,
            "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "chunks": chunks(),
        }
        settings.update(overrides)
        return asyncio.run(self.store.stage_input("input-admission", **settings))

    def test_original_docx_is_byte_exact_and_an_edited_copy_preserves_nontext_parts(self):
        original, parts = self.original_docx()
        receipt = self.stage_original(original)
        metadata = self.store.require_inputs(
            "input-admission", turn_id=receipt["turnId"], session_id="private:session-1",
            scope=self.scope,
            input_scope=input_file_scope("worker-1", "teams_personal", "conversation-1", "user-1"), count=1,
        )
        directory = self.store._turn(metadata["turnId"])
        source = directory / "inputs" / "01.docx"
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(source.stat().st_mode & 0o777, 0o444)
        instructions = self.store.instructions(metadata["turnId"])
        self.assertIn(str(source), instructions)
        self.assertNotIn("Original document text", instructions)
        self.store.bind_run(metadata["turnId"], "input-run")
        edited = directory / "workspace" / "edited.docx"
        with zipfile.ZipFile(source) as document, zipfile.ZipFile(edited, "w") as output:
            for name in document.namelist():
                data = document.read(name)
                if name == "word/document.xml":
                    data = data.replace(b"Original document text", b"Edited document text")
                output.writestr(name, data)
        sealed = self.store.seal("input-run", f'Edited.\nMEDIA:"{edited}"', scope=self.scope)
        entry = sealed["files"][0]
        _, delivered = self.store.download(
            entry["turnId"], entry["fileId"], scope=self.scope, run_id="input-run",
        )
        with zipfile.ZipFile(io.BytesIO(delivered)) as document:
            self.assertIn(b"Edited document text", document.read("word/document.xml"))
            for name, data in parts.items():
                if name != "word/document.xml":
                    self.assertEqual(document.read(name), data)
        self.assertEqual(source.read_bytes(), original)

    def test_original_inputs_deny_user_session_scope_admission_count_and_run_rebinding(self):
        original, _ = self.original_docx()
        receipt = self.stage_original(original)
        arguments = {
            "turn_id": receipt["turnId"], "session_id": "private:session-1", "scope": self.scope,
            "input_scope": input_file_scope("worker-1", "teams_personal", "conversation-1", "user-1"), "count": 1,
        }
        for changes in (
            {"input_scope": input_file_scope("worker-1", "teams_personal", "conversation-1", "other-user")},
            {"scope": input_file_scope("other-worker", "teams_personal", "conversation-1", "user-1")},
            {"session_id": "different-session"}, {"turn_id": "b" * 32}, {"count": 2},
        ):
            with self.subTest(changes=changes), self.assertRaises(GeneratedFileError):
                self.store.require_inputs("input-admission", **{**arguments, **changes})
        with self.assertRaises(GeneratedFileError):
            self.store.require_inputs("other-admission", **arguments)
        with self.assertRaises(GeneratedFileError):
            self.stage_original(original)
        self.store.bind_run(receipt["turnId"], "input-run")
        with self.assertRaises(GeneratedFileError):
            self.stage_original(original, index=2)

    def test_original_input_names_hash_lengths_types_and_limits_fail_without_committing_bytes(self):
        for changes in (
            {"name": "../outside.docx"}, {"name": "folder\\outside.docx"},
            {"name": ".hidden.docx"}, {"name": "line\nbreak.docx"},
            {"content_type": "application/x-executable"}, {"sha256": "b" * 64},
            {"size": 1}, {"size": 4}, {"index": 21},
        ):
            with self.subTest(changes=changes), self.assertRaises(GeneratedFileError):
                self.stage_original(b"abc", **changes)
        for limits in (
            {"ATTACHMENT_MAX_BYTES": "2"}, {"ATTACHMENT_MAX_TOTAL_BYTES": "2"},
            {"ATTACHMENT_MAX_COUNT": "0"},
        ):
            with self.subTest(limits=limits), patch.dict(os.environ, limits), self.assertRaises(GeneratedFileError):
                self.stage_original(b"abc")
        receipt = self.stage_original(b"abc", name="input.txt", content_type="text/plain")
        self.assertEqual(len(self.store._metadata(receipt["turnId"])["inputs"]), 1)
        with patch.dict(os.environ, {"ATTACHMENT_MAX_TOTAL_BYTES": "5"}), self.assertRaises(GeneratedFileError):
            self.stage_original(b"abc", index=2, name="second.txt", content_type="text/plain")

    def test_original_input_symlinks_tampering_and_expiry_block_use_and_export(self):
        receipt = self.stage_original(b"original", name="input.txt", content_type="text/plain")
        self.store.bind_run(receipt["turnId"], "input-run")
        directory = self.store._turn(receipt["turnId"])
        source = directory / "inputs" / "01.txt"
        outside = self.root / "outside.txt"
        outside.write_bytes(b"outside")
        source.unlink()
        source.symlink_to(outside)
        with self.assertRaises(GeneratedFileError):
            self.store.instructions(receipt["turnId"])
        with self.assertRaises(GeneratedFileError):
            self.store.seal("input-run", "No files", scope=self.scope)
        source.unlink()
        source.write_bytes(b"modified")
        with self.assertRaises(GeneratedFileError):
            self.store.instructions(receipt["turnId"])
        metadata = self.store._metadata(receipt["turnId"])
        with patch("bridge.generated_files.time.time", return_value=metadata["expiresAt"] + 1):
            with self.assertRaises(GeneratedFileError):
                self.store.require_inputs(
                    "input-admission", turn_id=receipt["turnId"], session_id="private:session-1",
                    scope=self.scope, input_scope=metadata["inputScope"], count=1,
                )

    def test_original_input_metadata_paths_are_validated_before_open(self):
        receipt = self.stage_original(b"original", name="input.txt", content_type="text/plain")
        metadata = self.store._metadata(receipt["turnId"])
        for name in ("../outside.txt", "02.txt", "01.docx"):
            metadata["inputs"][0]["storageName"] = name
            self.store._write_metadata(self.store._turn(receipt["turnId"]), metadata)
            with (
                patch.object(self.store, "_read_regular", wraps=self.store._read_regular) as reader,
                self.assertRaises(GeneratedFileError),
            ):
                self.store.instructions(receipt["turnId"])
            self.assertEqual([call.args[1] for call in reader.call_args_list], ["manifest.json"])

    def test_concurrent_original_uploads_reserve_the_total_limit_until_completion(self):
        async def exercise():
            entered = asyncio.Event()
            release = asyncio.Event()

            async def held():
                entered.set()
                await release.wait()
                yield b"abc"

            arguments = {
                "session_id": "private:session-1", "scope": self.scope, "input_scope": "a" * 64,
                "index": 1, "name": "input.txt", "content_type": "text/plain",
                "size": 3, "sha256": hashlib.sha256(b"abc").hexdigest(),
            }
            first = asyncio.create_task(self.store.stage_input("first-input", chunks=held(), **arguments))
            await entered.wait()
            try:
                with self.assertRaises(GeneratedFileError):
                    await self.store.stage_input("second-input", chunks=held(), **arguments)
            finally:
                release.set()
                await first
            self.assertEqual(self.store._input_bytes_reserved, 0)

        with patch.dict(os.environ, {"ATTACHMENT_MAX_TOTAL_BYTES": "5"}):
            asyncio.run(exercise())

    def test_txt_and_docx_are_sealed_as_actual_bytes_and_survive_source_change(self):
        package = io.BytesIO()
        with zipfile.ZipFile(package, "w") as document:
            document.writestr("[Content_Types].xml", "<Types/>")
            document.writestr("word/document.xml", "<document>Generated unit test</document>")
        sources = {"report.txt": b"Generated UTF-8 report\n", "report.docx": package.getvalue()}
        for name, data in sources.items():
            (self.workspace / name).write_bytes(data)
        output = "Generated reports.\n" + "\n".join(f'MEDIA:"{self.workspace / name}"' for name in sources)
        sealed = self.store.seal("run-1", output, scope=self.scope)
        self.assertEqual(sealed["output"], "Generated reports.")
        for entry in sealed["files"]:
            (self.workspace / entry["name"]).write_bytes(b"changed source")
            metadata, actual = self.store.download(
                entry["turnId"], entry["fileId"], scope=self.scope, run_id="run-1",
            )
            self.assertEqual(actual, sources[entry["name"]])
            self.assertEqual(metadata["sha256"], hashlib.sha256(actual).hexdigest())
        repeated = self.store.seal("run-1", output, scope=self.scope)
        self.assertEqual(repeated["files"], sealed["files"])

    def test_outside_traversal_and_other_turn_paths_are_refused(self):
        outside = self.root / "other.txt"
        outside.write_bytes(b"Not this conversation")
        other = self.store.admit("admission-2", session_id="session-2", scope="b" * 64)
        other_file = self.store._turn(other["turnId"]) / "workspace" / "other.txt"
        other_file.write_bytes(b"Different turn")
        for path in (outside, self.workspace / ".." / "other.txt", other_file):
            with self.subTest(path=path), self.assertRaisesRegex(GeneratedFileError, "outside"):
                self.store.seal("run-1", f'MEDIA:"{path}"', scope=self.scope)
        self.assertFalse(self.store._metadata(self.turn["turnId"])["sealed"])

    def test_symlinks_hardlinks_fifo_and_oversized_files_are_refused(self):
        outside = self.root / "outside.txt"
        outside.write_bytes(b"Do not export")
        link = self.workspace / "link.txt"
        link.symlink_to(outside)
        os.link(outside, self.workspace / "hardlink.txt")
        os.mkfifo(self.workspace / "fifo.txt")
        large = self.workspace / "large.txt"
        with large.open("wb") as stream:
            stream.truncate(MAX_FILE_BYTES + 1)
        for name in ("link.txt", "hardlink.txt", "fifo.txt", "large.txt"):
            with self.subTest(name=name), self.assertRaises(GeneratedFileError):
                self.store.seal("run-1", f'MEDIA:"{self.workspace / name}"', scope=self.scope)
        self.assertFalse(self.store._metadata(self.turn["turnId"])["sealed"])

    def test_workspace_symlink_is_refused(self):
        self.workspace.rmdir()
        self.workspace.symlink_to(self.root, target_is_directory=True)
        (self.root / "unsafe.txt").write_bytes(b"outside workspace")
        with self.assertRaisesRegex(GeneratedFileError, "directory is unsafe"):
            self.store.seal("run-1", f'MEDIA:"{self.workspace / "unsafe.txt"}"', scope=self.scope)

    def test_worker_conversation_run_expiry_and_byte_tampering_fail_closed(self):
        source = self.workspace / "report.txt"
        source.write_bytes(b"Generated")
        sealed = self.store.seal("run-1", f'MEDIA:"{source}"', scope=self.scope)
        entry = sealed["files"][0]
        for scope, run_id in (("b" * 64, "run-1"), (self.scope, "another-run")):
            with self.subTest(scope=scope, run=run_id), self.assertRaises(GeneratedFileError):
                self.store.download(entry["turnId"], entry["fileId"], scope=scope, run_id=run_id)
        other_worker = GeneratedFileStore(self.store.root, worker_id="worker-2")
        with self.assertRaisesRegex(GeneratedFileError, "another Worker"):
            other_worker.download(entry["turnId"], entry["fileId"], scope=self.scope, run_id="run-1")
        with patch("bridge.generated_files.time.time", return_value=sealed["expiresAt"] + 1):
            with self.assertRaisesRegex(GeneratedFileError, "expired"):
                self.store.download(entry["turnId"], entry["fileId"], scope=self.scope, run_id="run-1")
        (self.store._turn(entry["turnId"]) / entry["fileId"]).write_bytes(b"tampered")
        with self.assertRaisesRegex(GeneratedFileError, "integrity"):
            self.store.download(entry["turnId"], entry["fileId"], scope=self.scope, run_id="run-1")

    def test_invalid_format_empty_content_and_excess_file_count_are_refused(self):
        for name, data in (("empty.txt", b""), ("bad.txt", b"\xff"), ("bad.docx", b"not Word"), (".secret.txt", b"secret")):
            (self.workspace / name).write_bytes(data)
            with self.subTest(name=name), self.assertRaises(GeneratedFileError):
                self.store.seal("run-1", f'MEDIA:"{self.workspace / name}"', scope=self.scope)
        lines = []
        for index in range(4):
            path = self.workspace / f"{index}.txt"
            path.write_bytes(b"report")
            lines.append(f'MEDIA:"{path}"')
        with self.assertRaisesRegex(GeneratedFileError, "at most 3"):
            self.store.seal("run-1", "\n".join(lines), scope=self.scope)

    def test_batch_is_validated_before_any_snapshot_is_written_and_unknown_ids_create_nothing(self):
        good = self.workspace / "good.txt"
        good.write_bytes(b"safe")
        with self.assertRaises(GeneratedFileError):
            self.store.seal("run-1", f'MEDIA:"{good}"\nMEDIA:"{self.root / "outside.txt"}"', scope=self.scope)
        directory = self.store._turn(self.turn["turnId"])
        self.assertEqual({item.name for item in directory.iterdir()}, {"workspace", "manifest.json"})
        before = set(self.store.root.iterdir())
        with self.assertRaises(GeneratedFileError):
            self.store.download("a" * 32, "b" * 32, scope=self.scope, run_id="run-1")
        self.assertEqual(set(self.store.root.iterdir()), before)


if __name__ == "__main__":
    unittest.main()
