import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from bridge.generated_files import (
    GeneratedFileError, GeneratedFileStore, MAX_FILE_BYTES, conversation_file_scope,
)


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
