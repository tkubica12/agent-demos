"""Turn-scoped, bounded generated-file protocol shared by the native runtime and gateway."""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import secrets
import shutil
import stat
import threading
import time
import zipfile
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


MAX_FILES = 3
MAX_FILE_BYTES = 10 * 1024 * 1024
FILE_TTL_SECONDS = 3600
MAX_RETAINED_TURNS = 256
MAX_RETAINED_BYTES = 100 * 1024 * 1024
DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
CONTENT_TYPES = {".docx": DOCX_CONTENT_TYPE, ".txt": "text/plain"}
SCOPE_HEADER = "X-Autopilot-File-Scope"
RUN_HEADER = "X-Autopilot-File-Run"
ID_PATTERN = re.compile(r"[0-9a-f]{32}")
RUN_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,160}")
MEDIA_LINE = re.compile(r"^[ \t]*MEDIA:[ \t]*(.+?)[ \t]*$", re.MULTILINE)
MAX_EVIDENCE_BYTES = 1024 * 1024
SKILL_IDENTITIES = frozenset({"teams-html-widget"})
MAX_SKILL_LOAD_SECONDS = 3600


class GeneratedFileError(RuntimeError):
    pass


@dataclass(frozen=True)
class GeneratedAttachment:
    name: str
    content_type: str
    data: bytes = field(repr=False)


def generated_files_enabled() -> bool:
    return os.getenv("GENERATED_CHAT_FILES_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def conversation_file_scope(worker_id: str, source: str, conversation_id: str) -> str:
    if not worker_id or not conversation_id:
        raise GeneratedFileError("Generated files require WORKER_ID and a conversation identity.")
    return hashlib.sha256(json.dumps(
        [worker_id, source, conversation_id], separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def file_content_type(name: str) -> str:
    if (
        not name or name.startswith(".") or len(name) > 160
        or any(character in name for character in '/\\:"\'')
        or any(ord(character) < 32 for character in name)
        or Path(name).name != name
    ):
        raise GeneratedFileError("Generated files require a safe, non-hidden filename.")
    content_type = CONTENT_TYPES.get(Path(name).suffix.lower())
    if content_type is None:
        raise GeneratedFileError("Generated chat files support DOCX and UTF-8 TXT only.")
    return content_type


def validate_generated_bytes(name: str, data: bytes) -> str:
    content_type = file_content_type(name)
    if not data or len(data) > MAX_FILE_BYTES:
        raise GeneratedFileError("Generated file is empty or exceeds the 10 MiB limit.")
    if content_type == "text/plain":
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GeneratedFileError("Generated TXT must be valid UTF-8.") from exc
    else:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as package:
                if not {"[Content_Types].xml", "word/document.xml"}.issubset(package.namelist()):
                    raise GeneratedFileError("Generated DOCX is missing its required Word package parts.")
                if len(package.infolist()) > 1000 or sum(item.file_size for item in package.infolist()) > 50 * 1024 * 1024:
                    raise GeneratedFileError("Generated DOCX exceeds the package limits.")
        except zipfile.BadZipFile as exc:
            raise GeneratedFileError("Generated DOCX is not a valid Word package.") from exc
    return content_type


class NativeSkillLoadEvidence:
    """Retain only successful allowlisted main-skill loads, never their arguments or content."""

    def __init__(self):
        self._lock = threading.Lock()
        self._loads: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()

    def observe(self, *, tool_name: str, args: Any, result: Any, status: str,
                tool_call_id: str = "", duration_ms: int = 0, **_: Any) -> None:
        if not generated_files_enabled() or tool_name != "skill_view":
            return
        from tools.approval_context import get_current_session_key

        run_id = get_current_session_key(default="")
        if not isinstance(run_id, str) or not RUN_PATTERN.fullmatch(run_id):
            return
        self.record(
            run_id=run_id, tool_name=tool_name, args=args, result=result, status=status,
            tool_call_id=tool_call_id, duration_ms=duration_ms,
        )

    def record(self, *, run_id: str, tool_name: str, args: Any, result: Any,
               status: str, tool_call_id: str, duration_ms: int = 0) -> None:
        if (
            tool_name != "skill_view" or status != "ok" or not isinstance(args, dict)
            or not isinstance(args.get("name"), str) or args["name"] not in SKILL_IDENTITIES
            or args.get("file_path") not in (None, "")
        ):
            return
        if (
            not isinstance(run_id, str) or not RUN_PATTERN.fullmatch(run_id)
            or not isinstance(tool_call_id, str) or not RUN_PATTERN.fullmatch(tool_call_id)
            or type(duration_ms) is not int or not 0 <= duration_ms <= MAX_SKILL_LOAD_SECONDS * 1000
        ):
            raise GeneratedFileError("Native skill evidence has invalid execution identity or duration.")
        if not isinstance(result, str) or len(result) > MAX_EVIDENCE_BYTES:
            raise GeneratedFileError("Native skill evidence requires a bounded structured skill result.")
        try:
            payload = json.loads(result)
        except json.JSONDecodeError as exc:
            raise GeneratedFileError("Native skill evidence has an invalid structured result.") from exc
        if (
            not isinstance(payload, dict) or payload.get("success") is not True or payload.get("error")
            or payload.get("name") != args["name"] or payload.get("dedup") is True
            or ("content_returned" in payload and payload["content_returned"] is not True)
            or payload.get("status") == "unchanged"
            or not isinstance(payload.get("content"), str) or not payload["content"]
        ):
            return
        try:
            content = payload["content"].encode("utf-8")
        except UnicodeEncodeError as exc:
            raise GeneratedFileError("Native skill evidence has invalid UTF-8 content.") from exc
        if len(content) > MAX_EVIDENCE_BYTES:
            raise GeneratedFileError("Native skill evidence exceeds the bounded content limit.")
        content_hash = hashlib.sha256(content).hexdigest()
        evidence = {
            "toolCallId": tool_call_id, "skillName": payload["name"],
            "skillContentReturned": True, "skillContentSha256": content_hash,
        }
        with self._lock:
            key = (run_id, tool_call_id)
            previous = self._loads.get(key)
            if previous is not None and previous != evidence:
                del self._loads[key]
                raise GeneratedFileError("Native skill evidence has conflicting tool-call content.")
            self._loads[key] = evidence
            while len(self._loads) > 256:
                self._loads.popitem(last=False)

    def snapshot(self, run_id: str) -> dict[str, Any]:
        if not RUN_PATTERN.fullmatch(run_id):
            raise GeneratedFileError("Invalid native skill evidence run ID.")
        with self._lock:
            return {
                "runId": run_id,
                "loads": [dict(value) for (recorded_run, _), value in self._loads.items() if recorded_run == run_id],
            }


native_skill_load_evidence = NativeSkillLoadEvidence()


def completed_run_evidence(run_id: str, status: Any, events: bytes, skill_loads: Any = None) -> dict[str, Any]:
    """Project completed native status/SSE into metadata without prompts, previews, or file claims."""
    if not isinstance(status, dict) or status.get("run_id") != run_id or status.get("status") != "completed":
        raise GeneratedFileError("Native evidence requires the exact completed run.")
    if len(events) > MAX_EVIDENCE_BYTES:
        raise GeneratedFileError("Native evidence exceeds the 1 MiB limit.")
    tools = []
    completed = False
    skill_records: dict[str, dict[str, Any]] = {}
    if skill_loads is not None:
        if (
            not isinstance(skill_loads, dict) or skill_loads.get("runId") != run_id
            or not isinstance(skill_loads.get("loads"), list) or len(skill_loads["loads"]) > 256
        ):
            raise GeneratedFileError("Native skill evidence does not match the completed run.")
        for record in skill_loads["loads"]:
            if (
                not isinstance(record, dict) or not isinstance(record.get("skillName"), str)
                or record["skillName"] not in SKILL_IDENTITIES
                or record.get("skillContentReturned") is not True
                or not isinstance(record.get("skillContentSha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", record["skillContentSha256"])
                or not isinstance(record.get("toolCallId"), str)
                or not RUN_PATTERN.fullmatch(record["toolCallId"])
                or record["toolCallId"] in skill_records
            ):
                raise GeneratedFileError("Native skill evidence contains an invalid load identity.")
            skill_records[record["toolCallId"]] = {
                key: record[key] for key in ("skillName", "skillContentReturned", "skillContentSha256")
            }
    skill_starts: set[str] = set()
    skill_calls: set[str] = set()
    try:
        for line in events.decode("utf-8").splitlines():
            if not line.startswith("data:"):
                continue
            event = json.loads(line[5:].strip())
            if not isinstance(event, dict) or event.get("run_id") != run_id:
                raise GeneratedFileError("Native evidence contains a mismatched run.")
            kind = event.get("event")
            if kind == "replay.truncated":
                raise GeneratedFileError("Native evidence replay was truncated.")
            if kind == "run.completed":
                completed = True
            if kind not in {"tool.started", "tool.completed"}:
                continue
            name = event.get("tool")
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,160}", name):
                raise GeneratedFileError("Native evidence contains an invalid tool identity.")
            tool = {"event": kind, "tool": name}
            call_id = event.get("tool_call_id")
            if call_id is not None:
                if not isinstance(call_id, str) or not RUN_PATTERN.fullmatch(call_id):
                    raise GeneratedFileError("Native evidence contains an invalid tool-call identity.")
                tool["toolCallId"] = call_id
            if kind == "tool.completed":
                duration = event.get("duration")
                if type(event.get("error")) is not bool:
                    raise GeneratedFileError("Native evidence lacks the tool outcome.")
                if type(duration) not in {int, float} or not math.isfinite(duration) or duration < 0:
                    raise GeneratedFileError("Native evidence contains an invalid tool duration.")
                tool.update(error=event["error"], duration=duration)
                if (
                    name == "skill_view" and call_id in skill_starts and event["error"] is False
                    and duration <= MAX_SKILL_LOAD_SECONDS and call_id in skill_records
                ):
                    tool.update(skill_records[call_id])
                if name == "skill_view":
                    skill_starts.discard(call_id)
            elif name == "skill_view" and call_id is not None:
                if call_id in skill_calls:
                    raise GeneratedFileError("Native skill evidence has ambiguous tool-call starts.")
                skill_starts.add(call_id)
                skill_calls.add(call_id)
            tools.append(tool)
            if len(tools) > 256:
                raise GeneratedFileError("Native evidence exceeds the 256 tool-event limit.")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GeneratedFileError("Native evidence is not a valid UTF-8 event stream.") from exc
    if not completed:
        raise GeneratedFileError("Native evidence lacks the terminal completion event.")
    runtime = status.get("runtime")
    runtime = runtime if isinstance(runtime, dict) else {}
    runtime_ids = {}
    for key in ("provider", "model", "route_source"):
        value = runtime.get(key)
        if value == "":
            value = None
        if value is not None and (
            not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,160}", value)
        ):
            raise GeneratedFileError("Native evidence contains an invalid runtime identity.")
        runtime_ids[key] = value
    usage = status.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    counts = {
        key: value for key in (
            "input_tokens", "output_tokens", "total_tokens", "cache_read_tokens", "cache_write_tokens",
            "prompt_tokens", "completion_tokens",
        ) if type(value := usage.get(key)) is int and value >= 0
    }
    return {
        "runId": run_id, "status": "completed", "runtime": runtime_ids,
        "runtimeMetadataAvailable": bool(runtime_ids["provider"] and runtime_ids["model"]),
        "usage": counts, "tools": tools,
    }


class GeneratedFileStore:
    def __init__(self, root: Path, *, worker_id: str, workspace_owner: tuple[int, int] | None = None):
        if not worker_id:
            raise GeneratedFileError("GENERATED_CHAT_FILES_ENABLED requires WORKER_ID.")
        if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
            raise GeneratedFileError("Secure generated-file retrieval requires the native Linux runtime.")
        self.root = root
        self.worker_id = worker_id
        self.workspace_owner = workspace_owner
        self._control_directory(root.parent)
        self._control_directory(root)

    @staticmethod
    def _control_directory(path: Path) -> None:
        try:
            path.mkdir(mode=0o711)
        except FileExistsError:
            pass
        GeneratedFileStore._validate_control_directory(path)

    @staticmethod
    def _validate_control_directory(path: Path) -> None:
        info = path.lstat()
        if (
            not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise GeneratedFileError("Generated-file storage must be a trusted, non-symlink directory.")

    def _turn(self, turn_id: str) -> Path:
        if not ID_PATTERN.fullmatch(turn_id):
            raise GeneratedFileError("Invalid generated-file turn ID.")
        directory = self.root / turn_id
        self._validate_control_directory(self.root)
        try:
            self._validate_control_directory(directory)
        except OSError as exc:
            raise GeneratedFileError("Generated-file turn is unavailable.") from exc
        return directory

    @staticmethod
    def _write_metadata(directory: Path, metadata: dict[str, Any]) -> None:
        serialized = json.dumps(metadata, separators=(",", ":"))
        if len(serialized.encode("utf-8")) > 64 * 1024:
            raise GeneratedFileError("Generated-file manifest exceeds its size limit.")
        temporary = directory / f".manifest-{secrets.token_hex(8)}"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(serialized)
        os.replace(temporary, directory / "manifest.json")

    @staticmethod
    def _read_regular(directory: Path, name: str, *, limit: int) -> bytes:
        try:
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as exc:
            raise GeneratedFileError("Generated-file directory is unsafe or unavailable.") from exc
        try:
            file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
            with os.fdopen(file_fd, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
                    raise GeneratedFileError("Generated file must be a bounded, non-linked regular file.")
                data = stream.read(limit + 1)
                after = os.fstat(stream.fileno())
                if (
                    len(data) > limit or len(data) != before.st_size or after.st_nlink != 1
                    or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                ):
                    raise GeneratedFileError("Generated file changed while being sealed.")
                return data
        except OSError as exc:
            raise GeneratedFileError("Generated file is missing, unsafe, or unreadable.") from exc
        finally:
            os.close(directory_fd)

    def _metadata(self, turn_id: str) -> dict[str, Any]:
        metadata = json.loads(self._read_regular(self._turn(turn_id), "manifest.json", limit=64 * 1024))
        if metadata.get("workerId") != self.worker_id:
            raise GeneratedFileError("Generated files belong to another Worker.")
        return metadata

    def _cleanup(self) -> tuple[int, int]:
        retained = 0
        retained_bytes = 0
        for directory in self.root.iterdir():
            if not ID_PATTERN.fullmatch(directory.name):
                raise GeneratedFileError("Unexpected entry in generated-file storage.")
            metadata = self._metadata(directory.name)
            if metadata["expiresAt"] <= time.time():
                shutil.rmtree(directory)
            else:
                retained += 1
                retained_bytes += sum(item["size"] for item in metadata["files"])
        return retained, retained_bytes

    def admit(self, admission_id: str, *, session_id: str, scope: str) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f]{64}", scope):
            raise GeneratedFileError("A trusted generated-file conversation scope is required.")
        turn_id = hashlib.sha256(f"{self.worker_id}\n{admission_id}".encode("utf-8")).hexdigest()[:32]
        directory = self.root / turn_id
        if directory.exists():
            metadata = self._metadata(turn_id)
            if metadata["scope"] != scope or metadata["sessionId"] != session_id:
                raise GeneratedFileError("Generated-file admission belongs to another conversation or turn.")
            if metadata["expiresAt"] <= time.time():
                raise GeneratedFileError("Generated-file admission expired; automatic regeneration is forbidden.")
            return metadata
        retained, _ = self._cleanup()
        if retained >= MAX_RETAINED_TURNS:
            raise GeneratedFileError("Generated-file retention is full; expired turns must be cleaned before admission.")
        self._control_directory(directory)
        directory = self._turn(turn_id)
        workspace = directory / "workspace"
        workspace.mkdir(mode=0o700)
        if self.workspace_owner is not None:
            os.chown(workspace, *self.workspace_owner)
        metadata = {
            "turnId": turn_id, "workerId": self.worker_id, "sessionId": session_id,
            "scope": scope, "expiresAt": time.time() + FILE_TTL_SECONDS,
            "runId": "", "sealed": False, "files": [],
        }
        self._write_metadata(directory, metadata)
        return metadata

    def instructions(self, turn_id: str) -> str:
        workspace = self._turn(turn_id) / "workspace"
        return (
            "\nGenerated chat documents: use the native terminal/file tools to create final DOCX or UTF-8 TXT "
            f"files ONLY in this turn's output directory: {workspace}. "
            "This is an export directory, not a place for secrets, temporary files, or symlinks. "
            "Use at most 3 final files, each nonempty and at most 10 MiB. "
            "For standalone generated documents do not use Microsoft 365 sharing or m365-collaboration tools. "
            "The installed /app/.venv/bin/python provides python-docx for creating real Word documents. "
            "Return each finished file with the native MEDIA convention on its own separate line: "
            f'\nMEDIA:"{workspace}/filename.docx"\n'
            "Do not return sandbox links, host paths elsewhere, or claim delivery without a MEDIA line. "
            "The bridge validates and uploads the actual bytes as downloadable chat attachments."
        )

    def bind_run(self, turn_id: str, run_id: str) -> None:
        if not RUN_PATTERN.fullmatch(run_id):
            raise GeneratedFileError("Invalid native generated-file run ID.")
        metadata = self._metadata(turn_id)
        if metadata["runId"] and metadata["runId"] != run_id:
            raise GeneratedFileError("Generated-file turn already belongs to another native run.")
        metadata["runId"] = run_id
        self._write_metadata(self._turn(turn_id), metadata)

    def find_run(self, run_id: str) -> dict[str, Any] | None:
        for directory in self.root.iterdir():
            if ID_PATTERN.fullmatch(directory.name):
                metadata = self._metadata(directory.name)
                if metadata["runId"] == run_id:
                    return metadata
        return None

    def seal(self, run_id: str, output: str, *, scope: str) -> dict[str, Any] | None:
        metadata = self.find_run(run_id)
        if metadata is None:
            return None
        self._authorize(metadata, scope=scope, run_id=run_id)
        if metadata["sealed"]:
            return metadata
        matches = list(MEDIA_LINE.finditer(output))
        visible = MEDIA_LINE.sub("", output).strip()
        if "MEDIA:" in visible.upper() or len(matches) > MAX_FILES:
            raise GeneratedFileError("Generated files require at most 3 separate MEDIA lines.")
        directory = self._turn(metadata["turnId"])
        workspace = directory / "workspace"
        prepared = []
        seen = set()
        for match in matches:
            raw_path = match.group(1).strip()
            if raw_path[:1] in {'"', "'", "`"} and raw_path[-1:] == raw_path[:1]:
                raw_path = raw_path[1:-1]
            path = Path(raw_path)
            if not path.is_absolute() or path.parent != workspace or path.name in seen:
                raise GeneratedFileError("Generated file is outside this turn's output directory or duplicated.")
            seen.add(path.name)
            content_type = file_content_type(path.name)
            data = self._read_regular(workspace, path.name, limit=MAX_FILE_BYTES)
            validate_generated_bytes(path.name, data)
            prepared.append((path.name, content_type, data))
        files = []
        _, retained_bytes = self._cleanup()
        if retained_bytes + sum(len(data) for _, _, data in prepared) > MAX_RETAINED_BYTES:
            raise GeneratedFileError("Generated-file retention exceeds 100 MiB; files are retained for at most one hour.")
        for name, content_type, data in prepared:
            digest = hashlib.sha256(data).hexdigest()
            file_id = hashlib.sha256(f"{metadata['turnId']}\n{name}\n{digest}".encode("utf-8")).hexdigest()[:32]
            snapshot = directory / file_id
            if snapshot.exists():
                if self._read_regular(directory, file_id, limit=MAX_FILE_BYTES) != data:
                    raise GeneratedFileError("Generated-file snapshot conflicts with its identity.")
            else:
                descriptor = os.open(snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
            files.append({
                "turnId": metadata["turnId"], "fileId": file_id, "name": name,
                "contentType": content_type, "size": len(data), "sha256": digest,
            })
        metadata.update(sealed=True, files=files, output=visible)
        self._write_metadata(directory, metadata)
        return metadata

    @staticmethod
    def _authorize(metadata: dict[str, Any], *, scope: str, run_id: str) -> None:
        if not secrets.compare_digest(metadata["scope"], scope) or metadata["runId"] != run_id:
            raise GeneratedFileError("Generated files do not belong to this conversation and native run.")
        if metadata["expiresAt"] <= time.time():
            raise GeneratedFileError("Generated files expired; automatic regeneration is forbidden.")

    def download(self, turn_id: str, file_id: str, *, scope: str, run_id: str) -> tuple[dict[str, Any], bytes]:
        if not ID_PATTERN.fullmatch(file_id):
            raise GeneratedFileError("Invalid generated-file ID.")
        metadata = self._metadata(turn_id)
        self._authorize(metadata, scope=scope, run_id=run_id)
        entry = next((item for item in metadata["files"] if item["fileId"] == file_id), None)
        if not metadata["sealed"] or entry is None:
            raise GeneratedFileError("Generated file is unavailable for this completed turn.")
        data = self._read_regular(self._turn(turn_id), file_id, limit=MAX_FILE_BYTES)
        if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise GeneratedFileError("Sealed generated-file bytes failed integrity verification.")
        return entry, data
