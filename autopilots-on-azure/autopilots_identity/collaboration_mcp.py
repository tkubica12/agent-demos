from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import time
from collections import Counter
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
from mcp.server.fastmcp import FastMCP

from .tokens import AgentIdentityTokenProvider


GRAPH_SCOPE = "https://graph.microsoft.com/.default"
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
MAX_OFFICE_FILE_BYTES = 50 * 1024 * 1024
PENDING_PUBLISH_TTL_SECONDS = 60 * 60
PENDING_PUBLISH_JANITOR_SECONDS = 60
PENDING_PUBLISH_MANIFEST = "pending-publish.json"
PENDING_PUBLISH_ID_PATTERN = re.compile(r"^[0-9a-f]{24}$")
AGENT_RESULTS_FOLDER = "Hermes Results"
BASELINE_OPENXML_FILE = ".baseline-openxml.json"
MICROSOFT_FILE_HOST_SUFFIXES = (
    ".sharepoint.com",
    ".sharepoint-df.com",
    ".sharepointonline.com",
    ".onedrive.com",
    ".onedrive.live.com",
)
MICROSOFT_FILE_HOSTS = {"1drv.ms", "onedrive.live.com"}
logger = logging.getLogger(__name__)


@cache
def token_provider() -> AgentIdentityTokenProvider:
    return AgentIdentityTokenProvider.from_environment()


async def graph_token() -> str:
    return await token_provider().get_agent_user_token(
        os.getenv("M365_GRAPH_SCOPE", GRAPH_SCOPE)
    )


def collaboration_workspace() -> Path:
    return Path(
        os.getenv(
            "M365_COLLABORATION_WORKSPACE",
            "/data/hermes/workspace/m365-files",
        )
    ).resolve()


def validate_sharing_url(sharing_url: str) -> str:
    parsed = urlparse(sharing_url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        hostname in MICROSOFT_FILE_HOSTS
        or any(
            hostname.endswith(suffix)
            for suffix in MICROSOFT_FILE_HOST_SUFFIXES
        )
    ):
        raise ValueError(
            "A Microsoft 365 OneDrive or SharePoint sharing URL is required."
        )
    return sharing_url


def share_id(sharing_url: str) -> str:
    encoded = base64.urlsafe_b64encode(
        validate_sharing_url(sharing_url).encode("utf-8")
    ).decode("ascii")
    return "u!" + encoded.rstrip("=")


def safe_local_path(value: str) -> Path:
    workspace = collaboration_workspace()
    path = Path(value).resolve()
    try:
        path.relative_to(workspace)
    except ValueError as exc:
        raise ValueError(
            "Local Office files must remain in the private collaboration workspace."
        ) from exc
    if path.is_symlink():
        raise ValueError("Local Office files cannot be symbolic links.")
    return path


def run_local_tool(
    command: list[str],
    *,
    timeout: int = 180,
) -> dict:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    output = (result.stdout or "").strip()
    error = (result.stderr or "").strip()
    if result.returncode != 0:
        raise RuntimeError(
            error
            or output
            or f"{command[0]} exited with {result.returncode}."
        )
    if not output:
        return {"success": True}
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return {"output": output}
    return payload if isinstance(payload, dict) else {"result": payload}


def read_local_markdown(path: Path) -> str:
    result = run_local_tool(
        ["markitdown", str(path)],
        timeout=300,
    )
    return str(result.get("output") or "")[:1_000_000]


def normalize_markdown_text(value: str) -> str:
    return re.sub(
        r"\\([\\`*_{}\[\]()#+.!-])",
        r"\1",
        value,
    )


def utc_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat()


def operation_scope_hash(operation_scope: str) -> str:
    normalized = operation_scope.strip()
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def pending_publish_ttl_seconds() -> int:
    configured = os.getenv("M365_PENDING_PUBLISH_TTL_SECONDS", "")
    if configured.isdigit():
        return max(300, min(int(configured), 24 * 60 * 60))
    return PENDING_PUBLISH_TTL_SECONDS


def remove_private_workdir(path: Path) -> None:
    workspace = collaboration_workspace()
    resolved = path.resolve()
    if resolved.parent != workspace or not PENDING_PUBLISH_ID_PATTERN.fullmatch(
        resolved.name
    ):
        raise ValueError("Invalid private collaboration work directory.")
    if resolved.exists():
        shutil.rmtree(resolved)


def pending_publish_directory(operation_id: str) -> Path:
    if not PENDING_PUBLISH_ID_PATTERN.fullmatch(operation_id):
        raise ValueError("Invalid pending publish operation ID.")
    return collaboration_workspace() / operation_id


def write_pending_publish_manifest(directory: Path, manifest: dict) -> None:
    manifest_path = directory / PENDING_PUBLISH_MANIFEST
    temporary_path = directory / f".{PENDING_PUBLISH_MANIFEST}.tmp"
    temporary_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(manifest_path)


def baseline_openxml_path(path: Path) -> Path:
    return path.parent / BASELINE_OPENXML_FILE


def write_openxml_baseline(path: Path) -> dict:
    if path.suffix.lower() not in {".docx", ".xlsx", ".pptx"}:
        return {"Valid": True, "ErrorCount": 0, "Errors": []}
    result = run_local_tool(
        ["office-collaboration", "validate", str(path)]
    )
    baseline_path = baseline_openxml_path(path)
    temporary_path = baseline_path.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(baseline_path)
    return result


def read_openxml_baseline(path: Path) -> dict | None:
    baseline_path = baseline_openxml_path(path)
    if not baseline_path.is_file():
        return None
    try:
        result = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            "The Office validation baseline is invalid."
        ) from exc
    if not isinstance(result, dict):
        raise ValueError("The Office validation baseline is invalid.")
    return result


def openxml_error_signature(error: object) -> str:
    if not isinstance(error, dict):
        return json.dumps(error, sort_keys=True, ensure_ascii=False)
    return json.dumps(
        {
            "Id": error.get("Id"),
            "Description": error.get("Description"),
            "Path": error.get("Path"),
            "Part": error.get("Part"),
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def compare_openxml_validation(
    current: dict,
    baseline: dict | None,
) -> dict:
    current_errors = (
        current.get("Errors")
        if isinstance(current.get("Errors"), list)
        else []
    )
    baseline_errors = (
        baseline.get("Errors")
        if isinstance(baseline, dict)
        and isinstance(baseline.get("Errors"), list)
        else []
    )
    remaining = Counter(
        openxml_error_signature(error)
        for error in baseline_errors
    )
    new_errors = []
    for error in current_errors:
        signature = openxml_error_signature(error)
        if remaining[signature] > 0:
            remaining[signature] -= 1
        else:
            new_errors.append(error)
    return {
        "isValidChange": not new_errors,
        "preExistingErrorCount": len(baseline_errors),
        "currentErrorCount": len(current_errors),
        "newErrorCount": len(new_errors),
        "newErrors": new_errors,
        "validation": current,
    }


def stage_pending_publish(
    *,
    target: Path,
    item: dict,
    sharing_url: str,
    publish_kind: str,
    edits: list[dict[str, object]] | None = None,
    operation_scope: str = "",
) -> dict:
    operation_id = target.parent.name
    if (
        target.parent.parent != collaboration_workspace()
        or not PENDING_PUBLISH_ID_PATTERN.fullmatch(operation_id)
    ):
        raise ValueError(
            "Pending Office publishes must use a private operation directory."
        )
    created_at = time.time()
    expires_at = created_at + pending_publish_ttl_seconds()
    manifest = {
        "version": 1,
        "operationId": operation_id,
        "createdAt": utc_timestamp(created_at),
        "expiresAt": utc_timestamp(expires_at),
        "expiresAtUnix": expires_at,
        "sharingUrl": validate_sharing_url(sharing_url),
        "expectedETag": str(item.get("eTag") or ""),
        "fileName": target.name,
        "contentType": str(
            (item.get("file") or {}).get("mimeType")
            or "application/octet-stream"
        ),
        "publishKind": publish_kind,
    }
    scope_hash = operation_scope_hash(operation_scope)
    if scope_hash:
        manifest["operationScopeHash"] = scope_hash
    if edits is not None:
        manifest["edits"] = edits
    write_pending_publish_manifest(target.parent, manifest)
    return manifest


def prepare_pending_publish_target(path: Path) -> Path:
    workspace = collaboration_workspace()
    if (
        path.parent.parent == workspace
        and PENDING_PUBLISH_ID_PATTERN.fullmatch(path.parent.name)
    ):
        return path
    operation_directory = workspace / secrets.token_hex(12)
    operation_directory.mkdir(parents=True, exist_ok=False)
    staged_path = operation_directory / path.name
    previous_parent = path.parent
    previous_baseline = baseline_openxml_path(path)
    path.replace(staged_path)
    if previous_baseline.is_file():
        previous_baseline.replace(
            baseline_openxml_path(staged_path)
        )
    if previous_parent != workspace:
        try:
            previous_parent.rmdir()
        except OSError:
            pass
    return staged_path


def _load_pending_publish(
    operation_id: str,
    operation_scope: str | None,
) -> tuple[dict, Path]:
    directory = pending_publish_directory(operation_id)
    manifest_path = directory / PENDING_PUBLISH_MANIFEST
    if not manifest_path.is_file():
        raise ValueError("The pending Office publish does not exist or expired.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        remove_private_workdir(directory)
        raise ValueError(
            "The pending Office publish manifest is invalid."
        ) from exc
    if not isinstance(manifest, dict) or manifest.get("operationId") != operation_id:
        raise ValueError("The pending Office publish manifest is invalid.")
    stored_scope_hash = str(manifest.get("operationScopeHash") or "")
    if (
        operation_scope is not None
        and stored_scope_hash
        and not secrets.compare_digest(
            stored_scope_hash,
            operation_scope_hash(operation_scope),
        )
    ):
        raise ValueError(
            "The pending Office publish belongs to another conversation."
        )
    if float(manifest.get("expiresAtUnix") or 0) <= time.time():
        remove_private_workdir(directory)
        raise ValueError(
            "The pending Office publish expired; repeat the document edit."
        )
    target = directory / Path(str(manifest.get("fileName") or "")).name
    if not target.is_file():
        remove_private_workdir(directory)
        raise ValueError("The pending Office publish file is missing.")
    return manifest, target


def load_pending_publish(
    operation_id: str,
    operation_scope: str = "",
) -> tuple[dict, Path]:
    return _load_pending_publish(operation_id, operation_scope)


def load_pending_publish_internal(
    operation_id: str,
) -> tuple[dict, Path]:
    return _load_pending_publish(operation_id, None)


def cleanup_expired_pending_publishes() -> int:
    workspace = collaboration_workspace()
    if not workspace.is_dir():
        return 0
    removed = 0
    now = time.time()
    for manifest_path in workspace.glob(
        f"*/{PENDING_PUBLISH_MANIFEST}"
    ):
        directory = manifest_path.parent
        if not PENDING_PUBLISH_ID_PATTERN.fullmatch(directory.name):
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            expires_at = float(manifest.get("expiresAtUnix") or 0)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            expires_at = 0
        if expires_at <= now:
            remove_private_workdir(directory)
            removed += 1
    return removed


async def pending_publish_janitor() -> None:
    while True:
        await asyncio.sleep(PENDING_PUBLISH_JANITOR_SECONDS)
        try:
            cleanup_expired_pending_publishes()
        except OSError:
            logger.exception("Pending Office publish cleanup failed.")


@asynccontextmanager
async def collaboration_lifespan(_: FastMCP):
    cleanup_expired_pending_publishes()
    janitor = asyncio.create_task(pending_publish_janitor())
    try:
        yield {}
    finally:
        janitor.cancel()
        with suppress(asyncio.CancelledError):
            await janitor


async def graph_request(
    method: str,
    path: str,
    *,
    json_body: object | None = None,
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
    follow_redirects: bool = False,
) -> httpx.Response:
    token = await graph_token()
    async with httpx.AsyncClient(
        timeout=120,
        follow_redirects=follow_redirects,
    ) as client:
        response = await client.request(
            method,
            f"{GRAPH_BASE_URL}{path}",
            headers={
                "Authorization": f"Bearer {token}",
                **(headers or {}),
            },
            json=json_body,
            content=content,
        )
    response.raise_for_status()
    return response


async def drive_item(sharing_url: str) -> dict:
    response = await graph_request(
        "GET",
        (
            f"/shares/{share_id(sharing_url)}/driveItem"
            "?$select=id,name,eTag,webUrl,size,parentReference,file,"
            "createdBy,lastModifiedBy,shared,publication,pendingOperations"
        ),
    )
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Microsoft Graph returned a non-object drive item.")
    return payload


def drive_coordinates(item: dict) -> tuple[str, str]:
    drive_id = str(
        (item.get("parentReference") or {}).get("driveId") or ""
    )
    item_id = str(item.get("id") or "")
    if not drive_id or not item_id:
        raise RuntimeError(
            "Microsoft Graph drive item did not include drive and item IDs."
        )
    return drive_id, item_id


async def download_drive_item(item: dict) -> bytes:
    size = int(item.get("size") or 0)
    if size > MAX_OFFICE_FILE_BYTES:
        raise ValueError(
            f"Office collaboration files are limited to {MAX_OFFICE_FILE_BYTES} bytes."
        )
    drive_id, item_id = drive_coordinates(item)
    response = await graph_request(
        "GET",
        f"/drives/{quote(drive_id, safe='')}/items/{quote(item_id, safe='')}/content",
        follow_redirects=True,
    )
    data = response.content
    if len(data) > MAX_OFFICE_FILE_BYTES:
        raise ValueError(
            f"Office collaboration files are limited to {MAX_OFFICE_FILE_BYTES} bytes."
        )
    return data


async def upload_drive_item(
    item: dict,
    data: bytes,
    expected_etag: str,
) -> dict:
    if len(data) > MAX_OFFICE_FILE_BYTES:
        raise ValueError(
            f"Office collaboration files are limited to {MAX_OFFICE_FILE_BYTES} bytes."
        )
    current_etag = str(item.get("eTag") or "")
    if not expected_etag or current_etag != expected_etag:
        raise ValueError(
            "The Microsoft 365 file changed after download; refresh before uploading."
        )
    drive_id, item_id = drive_coordinates(item)
    content_type = str(
        (item.get("file") or {}).get("mimeType")
        or "application/octet-stream"
    )
    response = None
    for attempt in range(4):
        try:
            response = await graph_request(
                "PUT",
                (
                    f"/drives/{quote(drive_id, safe='')}/items/"
                    f"{quote(item_id, safe='')}/content"
                ),
                content=data,
                headers={
                    "Content-Type": content_type,
                    "If-Match": expected_etag,
                },
            )
            break
        except httpx.HTTPStatusError as exc:
            if (
                exc.response.status_code not in {423, 429, 503}
                or attempt == 3
            ):
                raise
            retry_after = exc.response.headers.get("Retry-After", "")
            delay = (
                min(int(retry_after), 15)
                if retry_after.isdigit()
                else min(5 * (attempt + 1), 15)
            )
            await asyncio.sleep(delay)
    if response is None:
        raise RuntimeError("Microsoft Graph upload returned no response.")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Microsoft Graph returned a non-object upload result.")
    return payload


def graph_error_summary(response: httpx.Response) -> dict:
    error: dict = {}
    try:
        payload = response.json()
        if isinstance(payload, dict) and isinstance(
            payload.get("error"),
            dict,
        ):
            error = payload["error"]
    except ValueError:
        pass
    inner = (
        error.get("innerError")
        if isinstance(error.get("innerError"), dict)
        else {}
    )
    return {
        "statusCode": response.status_code,
        "code": str(error.get("code") or ""),
        "message": str(error.get("message") or ""),
        "retryAfter": response.headers.get("Retry-After", ""),
        "requestId": str(
            inner.get("request-id")
            or response.headers.get("request-id")
            or response.headers.get("SPRequestGuid")
            or ""
        ),
    }


async def apply_word_text_node_edits(
    target: Path,
    edits: list[dict[str, object]],
) -> tuple[dict, dict]:
    patch_result = run_local_tool(
        [
            "office-collaboration",
            "word-patch-text-nodes",
            str(target),
            json.dumps(edits, ensure_ascii=False),
        ]
    )
    extracted = normalize_markdown_text(read_local_markdown(target))
    missing = [
        str(edit["replacementText"])
        for edit in edits
        if str(edit["replacementText"])
        and normalize_markdown_text(
            str(edit["replacementText"])
        ) not in extracted
    ]
    if missing:
        raise RuntimeError(
            "Word patch semantic verification omitted replacements."
        )
    validation = await minimax_docx_validate(str(target))
    return patch_result, validation


def locked_publish_result(
    manifest: dict,
    *,
    applied_locally: object | None = None,
    validation: dict | None = None,
    graph_error: dict | None = None,
) -> dict:
    result = {
        "status": "locked",
        "retryable": True,
        "retryAfterSeconds": 30,
        "operationId": manifest["operationId"],
        "expiresAt": manifest["expiresAt"],
        "choices": ["retry_original", "send_copy", "cancel"],
        "message": (
            "The validated edit is retained privately. Report the lock, retry "
            "the original with retry_pending_office_publish, and offer an "
            "edited copy or cancellation only if the lock persists."
        ),
    }
    if applied_locally is not None:
        result["appliedLocally"] = applied_locally
    if validation is not None:
        result["validation"] = validation
    if graph_error is not None:
        result["graphError"] = graph_error
    return result


async def rebase_pending_word_patch(
    manifest: dict,
    target: Path,
    item: dict,
) -> tuple[dict, dict]:
    edits = manifest.get("edits")
    if not isinstance(edits, list) or not edits:
        raise ValueError("The pending Word patch has no reusable edit intent.")
    fresh_path = target.with_name(f".fresh-{target.name}")
    fresh_path.write_bytes(await download_drive_item(item))
    try:
        write_openxml_baseline(fresh_path)
        patch_result, validation = await apply_word_text_node_edits(
            fresh_path,
            edits,
        )
        fresh_path.replace(target)
    finally:
        fresh_path.unlink(missing_ok=True)
    manifest["expectedETag"] = str(item.get("eTag") or "")
    manifest["rebasedAt"] = utc_timestamp(time.time())
    write_pending_publish_manifest(target.parent, manifest)
    return patch_result, validation


async def ensure_agent_results_folder() -> dict:
    path = f"/me/drive/root:/{quote(AGENT_RESULTS_FOLDER, safe='')}"
    try:
        response = await graph_request("GET", f"{path}?$select=id,name")
        payload = response.json()
        if isinstance(payload, dict) and payload.get("id"):
            return payload
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise
    try:
        response = await graph_request(
            "POST",
            "/me/drive/root/children",
            json_body={
                "name": AGENT_RESULTS_FOLDER,
                "folder": {},
                "@microsoft.graph.conflictBehavior": "fail",
            },
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 409:
            raise
        response = await graph_request("GET", f"{path}?$select=id,name")
    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("id"):
        raise RuntimeError(
            "Microsoft Graph did not return the Agent User results folder."
        )
    return payload


async def upload_agent_results_copy(
    target: Path,
    content_type: str,
) -> dict:
    folder = await ensure_agent_results_folder()
    copy_name = f"{target.stem} - Hermes edit{target.suffix}"
    response = await graph_request(
        "PUT",
        (
            f"/me/drive/items/{quote(str(folder['id']), safe='')}:"
            f"/{quote(copy_name, safe='')}:/content"
            "?@microsoft.graph.conflictBehavior=rename"
        ),
        content=target.read_bytes(),
        headers={"Content-Type": content_type},
    )
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Microsoft Graph returned a non-object copy result.")
    return payload


async def invite_drive_item_user(
    item: dict,
    recipient_identifier: str,
    role: str,
) -> dict:
    if role not in {"read", "write"}:
        raise ValueError("Office sharing role must be read or write.")
    recipient = recipient_identifier.strip()
    if not recipient:
        raise ValueError("A Microsoft 365 recipient identifier is required.")
    drive_id, item_id = drive_coordinates(item)
    response = await graph_request(
        "POST",
        (
            f"/drives/{quote(drive_id, safe='')}/items/"
            f"{quote(item_id, safe='')}/invite"
        ),
        json_body={
            "recipients": [{"objectId": recipient}],
            "requireSignIn": True,
            "sendInvitation": False,
            "roles": [role],
        },
    )
    payload = response.json()
    permissions = (
        payload.get("value")
        if isinstance(payload, dict)
        else None
    )
    if not isinstance(permissions, list) or not permissions:
        raise RuntimeError(
            "Microsoft Graph returned no permission after sharing the file."
        )
    failures = [
        permission.get("error")
        for permission in permissions
        if isinstance(permission, dict) and permission.get("error")
    ]
    if failures:
        raise RuntimeError(
            "Microsoft Graph partially failed to share the file: "
            + json.dumps(failures, ensure_ascii=False)
        )
    return {
        "recipient": recipient,
        "role": role,
        "permissions": permissions,
    }


async def delete_drive_item(item: dict) -> None:
    drive_id, item_id = drive_coordinates(item)
    await graph_request(
        "DELETE",
        (
            f"/drives/{quote(drive_id, safe='')}/items/"
            f"{quote(item_id, safe='')}"
        ),
    )


mcp = FastMCP(
    "Agent User Microsoft 365 file bridge",
    host="127.0.0.1",
    port=int(os.getenv("M365_COLLABORATION_MCP_PORT", "18082")),
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
    lifespan=collaboration_lifespan,
)


@mcp.tool()
async def get_user_profile(user_identifier: str) -> dict:
    """Resolve an Entra object ID or UPN to a basic Microsoft 365 profile."""
    response = await graph_request(
        "GET",
        (
            f"/users/{quote(user_identifier, safe='')}"
            "?$select=id,displayName,mail,userPrincipalName,jobTitle"
        ),
    )
    return response.json()


@mcp.tool()
async def get_office_file_metadata(sharing_url: str) -> dict:
    """Return identity, version, size, and attribution metadata for a shared Office file."""
    return await drive_item(sharing_url)


@mcp.tool()
async def download_office_file(sharing_url: str) -> dict:
    """Download a shared Office file into the private local collaboration workspace."""
    item = await drive_item(sharing_url)
    data = await download_drive_item(item)
    name = Path(str(item.get("name") or "office-file")).name
    target = collaboration_workspace() / secrets.token_hex(12) / name
    target.parent.mkdir(parents=True, exist_ok=False)
    target.write_bytes(data)
    try:
        baseline = write_openxml_baseline(target)
    except Exception:
        remove_private_workdir(target.parent)
        raise
    return {
        "localPath": str(target),
        "name": name,
        "size": len(data),
        "expectedETag": str(item.get("eTag") or ""),
        "webUrl": str(item.get("webUrl") or sharing_url),
        "lastModifiedBy": item.get("lastModifiedBy"),
        "baselineOpenXmlErrors": int(
            baseline.get("ErrorCount") or 0
        ),
    }


@mcp.tool()
async def upload_office_file(
    sharing_url: str,
    local_path: str,
    expected_etag: str,
    operation_scope: str,
) -> dict:
    """Upload an edited local Office file to the same drive item as a new version."""
    path = safe_local_path(local_path)
    if not path.is_file():
        raise ValueError("The local Office file does not exist.")
    data = path.read_bytes()
    item = await drive_item(sharing_url)
    try:
        return await upload_drive_item(item, data, expected_etag)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 423:
            raise
        cleanup_expired_pending_publishes()
        path = prepare_pending_publish_target(path)
        manifest = stage_pending_publish(
            target=path,
            item=item,
            sharing_url=sharing_url,
            publish_kind="generic-local-edit",
            operation_scope=operation_scope,
        )
        return locked_publish_result(
            manifest,
            graph_error=graph_error_summary(exc.response),
        )


@mcp.tool()
async def inspect_word_structure(sharing_url: str) -> dict:
    """Inspect Word paragraphs and text nodes for stable edit selectors."""
    item = await drive_item(sharing_url)
    if not str(item.get("name") or "").lower().endswith(".docx"):
        raise ValueError("inspect_word_structure requires a DOCX file.")
    data = await download_drive_item(item)
    name = Path(str(item.get("name") or "form.docx")).name
    target = collaboration_workspace() / secrets.token_hex(12) / name
    target.parent.mkdir(parents=True, exist_ok=False)
    target.write_bytes(data)
    try:
        return run_local_tool(
            [
                "office-collaboration",
                "word-inspect-structure",
                str(target),
            ]
        )
    finally:
        target.unlink(missing_ok=True)
        try:
            target.parent.rmdir()
        except OSError:
            pass


@mcp.tool()
async def patch_word_text(
    sharing_url: str,
    edits: list[dict[str, object]],
    operation_scope: str,
) -> dict:
    """Apply stable Word text-node edits, validate, and publish the same item.

    Each edit requires paragraphIndex, textNodeIndex, searchText, and
    replacementText values obtained from inspect_word_structure.
    """
    if not edits:
        raise ValueError("edits must not be empty.")
    required = {
        "paragraphIndex",
        "textNodeIndex",
        "searchText",
        "replacementText",
    }
    if any(
        not isinstance(edit, dict)
        or not required <= set(edit)
        or not str(edit.get("searchText") or "")
        for edit in edits
    ):
        raise ValueError(
            "Each edit requires paragraphIndex, textNodeIndex, "
            "searchText, and replacementText."
        )
    item = await drive_item(sharing_url)
    if not str(item.get("name") or "").lower().endswith(".docx"):
        raise ValueError("patch_word_text requires a DOCX file.")
    data = await download_drive_item(item)
    name = Path(str(item.get("name") or "document.docx")).name
    target = collaboration_workspace() / secrets.token_hex(12) / name
    target.parent.mkdir(parents=True, exist_ok=False)
    target.write_bytes(data)
    retained = False
    try:
        write_openxml_baseline(target)
        patch_result, validation = await apply_word_text_node_edits(
            target,
            edits,
        )
        try:
            uploaded = await upload_drive_item(
                item,
                target.read_bytes(),
                str(item.get("eTag") or ""),
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 423:
                raise
            cleanup_expired_pending_publishes()
            manifest = stage_pending_publish(
                target=target,
                item=item,
                sharing_url=sharing_url,
                publish_kind="word-text-node-patch",
                edits=edits,
                operation_scope=operation_scope,
            )
            retained = True
            return locked_publish_result(
                manifest,
                applied_locally=patch_result.get("Applied"),
                validation=validation,
                graph_error=graph_error_summary(exc.response),
            )
        return {
            "status": "completed",
            "applied": patch_result.get("Applied"),
            "validation": validation,
            "driveItem": uploaded,
        }
    finally:
        if not retained:
            remove_private_workdir(target.parent)


@mcp.tool()
async def retry_pending_office_publish(
    operation_id: str,
    operation_scope: str,
) -> dict:
    """Retry a retained same-item Office publish without repeating the edit.

    Stable Word text-node patches are reapplied to the latest source when its
    ETag changed. Other local edits fail closed on an ETag change.
    """
    cleanup_expired_pending_publishes()
    manifest, target = load_pending_publish(
        operation_id,
        operation_scope,
    )
    item = await drive_item(str(manifest["sharingUrl"]))
    current_etag = str(item.get("eTag") or "")
    expected_etag = str(manifest.get("expectedETag") or "")
    rebased = False
    patch_result = None
    validation = None
    if current_etag != expected_etag:
        if manifest.get("publishKind") != "word-text-node-patch":
            manifest["sourceChangedAt"] = utc_timestamp(time.time())
            manifest["latestETag"] = current_etag
            write_pending_publish_manifest(target.parent, manifest)
            return {
                "status": "source_changed",
                "retryable": False,
                "operationId": operation_id,
                "choices": ["send_copy", "cancel", "repeat_edit"],
                "cleanupRequiredBeforeRepeatEdit": True,
                "copyBasedOnSupersededSource": True,
                "message": (
                    "The original changed after the local edit. It was not "
                    "overwritten; repeat the edit against the latest version "
                    "or explicitly publish the retained result as a copy "
                    "based on the earlier source."
                ),
            }
        patch_result, validation = await rebase_pending_word_patch(
            manifest,
            target,
            item,
        )
        expected_etag = str(manifest["expectedETag"])
        rebased = True
    try:
        uploaded = await upload_drive_item(
            item,
            target.read_bytes(),
            expected_etag,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 423:
            raise
        return locked_publish_result(
            manifest,
            applied_locally=(
                patch_result.get("Applied")
                if isinstance(patch_result, dict)
                else None
            ),
            validation=validation,
            graph_error=graph_error_summary(exc.response),
        )
    remove_private_workdir(target.parent)
    return {
        "status": "completed",
        "operationId": operation_id,
        "rebased": rebased,
        "driveItem": uploaded,
    }


@mcp.tool()
async def publish_pending_office_copy(
    operation_id: str,
    recipient_identifier: str,
    operation_scope: str,
) -> dict:
    """Publish and share a retained Agent User copy after explicit choice."""
    cleanup_expired_pending_publishes()
    manifest, target = load_pending_publish(
        operation_id,
        operation_scope,
    )
    copied = await upload_agent_results_copy(
        target,
        str(
            manifest.get("contentType")
            or "application/octet-stream"
        ),
    )
    try:
        sharing = await invite_drive_item_user(
            copied,
            recipient_identifier,
            "write",
        )
    except (
        httpx.HTTPStatusError,
        RuntimeError,
        ValueError,
    ) as exc:
        await delete_drive_item(copied)
        raise RuntimeError(
            "The edited copy could not be shared with the recipient and was "
            "deleted; the retained edit remains available."
        ) from exc
    superseded_source = bool(manifest.get("sourceChangedAt"))
    remove_private_workdir(target.parent)
    return {
        "status": "completed_copy",
        "operationId": operation_id,
        "copyBasedOnSupersededSource": superseded_source,
        "driveItem": copied,
        "sharing": sharing,
        "message": (
            "The edited copy is in the Agent User's Hermes Results folder. "
            "Return it through Work IQ Teams. It has independent sharing, "
            "comments, and version history."
            + (
                " It is based on the source version from before another "
                "user changed the original."
                if superseded_source
                else ""
            )
        ),
    }


@mcp.tool()
async def share_office_file_with_user(
    sharing_url: str,
    recipient_identifier: str,
    role: str = "write",
) -> dict:
    """Grant one Microsoft 365 user explicit access to an Office file."""
    item = await drive_item(sharing_url)
    sharing = await invite_drive_item_user(
        item,
        recipient_identifier,
        role,
    )
    return {
        "status": "shared",
        "driveItem": {
            "id": item.get("id"),
            "name": item.get("name"),
            "webUrl": item.get("webUrl"),
        },
        "sharing": sharing,
    }


@mcp.tool()
async def cancel_pending_office_publish(
    operation_id: str,
    operation_scope: str,
) -> dict:
    """Cancel a retained Office publish and delete its private staged file."""
    manifest, target = load_pending_publish(
        operation_id,
        operation_scope,
    )
    remove_private_workdir(target.parent)
    return {
        "status": "cancelled",
        "operationId": manifest["operationId"],
    }


@mcp.tool()
async def find_pending_office_publishes(
    operation_scope: str,
) -> dict:
    """Recover pending publishes for the current private conversation scope."""
    scope_hash = operation_scope_hash(operation_scope)
    if not scope_hash:
        raise ValueError("A private Office operation scope is required.")
    cleanup_expired_pending_publishes()
    operations = []
    workspace = collaboration_workspace()
    if workspace.is_dir():
        for manifest_path in workspace.glob(
            f"*/{PENDING_PUBLISH_MANIFEST}"
        ):
            try:
                manifest = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                continue
            stored_scope_hash = str(
                manifest.get("operationScopeHash") or ""
            )
            if not stored_scope_hash or not secrets.compare_digest(
                stored_scope_hash,
                scope_hash,
            ):
                continue
            operations.append(
                {
                    "operationId": manifest.get("operationId"),
                    "fileName": manifest.get("fileName"),
                    "publishKind": manifest.get("publishKind"),
                    "createdAt": manifest.get("createdAt"),
                    "expiresAt": manifest.get("expiresAt"),
                    "expiresAtUnix": manifest.get(
                        "expiresAtUnix"
                    ),
                    "sourceChanged": bool(
                        manifest.get("sourceChangedAt")
                    ),
                }
            )
    operations.sort(key=lambda value: str(value.get("createdAt") or ""))
    return {"operations": operations}


def bind_pending_publish_scope(
    operation_id: str,
    operation_scope: str,
) -> dict:
    manifest, target = load_pending_publish_internal(operation_id)
    desired_hash = operation_scope_hash(operation_scope)
    if not desired_hash:
        raise ValueError("A private Office operation scope is required.")
    existing_hash = str(manifest.get("operationScopeHash") or "")
    if existing_hash and not secrets.compare_digest(
        existing_hash,
        desired_hash,
    ):
        raise ValueError(
            "The pending Office publish already belongs to another scope."
        )
    manifest["operationScopeHash"] = desired_hash
    write_pending_publish_manifest(target.parent, manifest)
    return {
        "status": "bound",
        "operationId": operation_id,
    }


@mcp.tool()
async def delete_local_office_file(local_path: str) -> dict:
    """Delete one downloaded Office file and its empty private work directory."""
    path = safe_local_path(local_path)
    deleted = path.is_file()
    if deleted:
        path.unlink()
    baseline_openxml_path(path).unlink(missing_ok=True)
    workspace = collaboration_workspace()
    parent = path.parent
    if parent != workspace and parent.parent == workspace:
        try:
            parent.rmdir()
        except OSError:
            pass
    return {"deleted": deleted, "localPath": str(path)}


@mcp.tool()
async def read_local_office_file(local_path: str) -> dict:
    """Extract bounded Markdown text from a downloaded Office or PDF file."""
    path = safe_local_path(local_path)
    if not path.is_file():
        raise ValueError("The local Office file does not exist.")
    return {
        "localPath": str(path),
        "text": read_local_markdown(path),
    }


@mcp.tool()
async def validate_local_office_file(local_path: str) -> dict:
    """Validate a downloaded DOCX, XLSX, or PPTX with Microsoft Open XML SDK."""
    path = safe_local_path(local_path)
    return run_local_tool(
        ["office-collaboration", "validate", str(path)]
    )


@mcp.tool()
async def minimax_docx_replace_text(
    local_path: str,
    search: str,
    replacement: str,
) -> dict:
    """Replace Word text with the pinned MIT MiniMax Open XML CLI."""
    path = safe_local_path(local_path)
    if normalize_markdown_text(search) not in normalize_markdown_text(
        read_local_markdown(path)
    ):
        raise ValueError("MiniMax DOCX input does not contain the requested text.")
    result = run_local_tool(
        [
            "minimax-docx",
            "edit",
            "replace-text",
            "--input",
            str(path),
            "--output",
            str(path),
            "--search",
            search,
            "--replace",
            replacement,
        ]
    )
    if normalize_markdown_text(
        replacement
    ) not in normalize_markdown_text(read_local_markdown(path)):
        raise RuntimeError(
            "MiniMax DOCX reported success but semantic verification failed."
        )
    return result


@mcp.tool()
async def minimax_docx_insert_paragraph(
    local_path: str,
    text: str,
    style: str = "",
) -> dict:
    """Insert a Word paragraph with the pinned MIT MiniMax Open XML CLI."""
    path = safe_local_path(local_path)
    command = [
        "minimax-docx",
        "edit",
        "insert-paragraph",
        "--input",
        str(path),
        "--output",
        str(path),
        "--text",
        text,
    ]
    if style:
        command.extend(["--style", style])
    result = run_local_tool(command)
    if normalize_markdown_text(text) not in normalize_markdown_text(
        read_local_markdown(path)
    ):
        raise RuntimeError(
            "MiniMax DOCX reported success but the paragraph is missing."
        )
    return result


@mcp.tool()
async def minimax_docx_fill_placeholders(
    local_path: str,
    values: dict[str, str],
) -> dict:
    """Fill {{name}} Word placeholders with the pinned MIT MiniMax CLI."""
    path = safe_local_path(local_path)
    mapping_path = path.parent / "placeholder-values.json"
    mapping_path.write_text(
        json.dumps(values, ensure_ascii=False),
        encoding="utf-8",
    )
    try:
        result = run_local_tool(
            [
                "minimax-docx",
                "edit",
                "fill-placeholders",
                "--input",
                str(path),
                "--output",
                str(path),
                "--mapping",
                str(mapping_path),
            ]
        )
        extracted = normalize_markdown_text(
            read_local_markdown(path)
        )
        missing = [
            value
            for value in values.values()
            if value
            and normalize_markdown_text(value) not in extracted
        ]
        if missing:
            raise RuntimeError(
                "MiniMax DOCX placeholder verification failed."
            )
        return result
    finally:
        mapping_path.unlink(missing_ok=True)


@mcp.tool()
async def minimax_docx_validate(local_path: str) -> dict:
    """Run MiniMax business rules and Microsoft Open XML validation as a hard gate."""
    path = safe_local_path(local_path)
    minimax_result = run_local_tool(
        [
            "minimax-docx",
            "validate",
            "--input",
            str(path),
            "--business",
            "--json",
        ]
    )
    openxml_result = run_local_tool(
        ["office-collaboration", "validate", str(path)]
    )
    openxml_change = compare_openxml_validation(
        openxml_result,
        read_openxml_baseline(path),
    )
    if (
        minimax_result.get("isValid") is not True
        or openxml_change["isValidChange"] is not True
    ):
        raise ValueError(
            "MiniMax DOCX validation failed: "
            + json.dumps(
                {
                    "businessRules": minimax_result,
                    "openXml": openxml_result,
                    "openXmlChange": openxml_change,
                },
                ensure_ascii=False,
            )
        )
    return {
        "isValid": True,
        "businessRules": minimax_result,
        "openXml": openxml_result,
        "openXmlChange": openxml_change,
    }


@mcp.tool()
async def word_append_tracked(
    local_path: str,
    text: str,
    author: str = "Hermes",
) -> dict:
    """Append one Microsoft Open XML tracked paragraph to a downloaded DOCX."""
    path = safe_local_path(local_path)
    return run_local_tool(
        [
            "office-collaboration",
            "word-append-tracked",
            str(path),
            text,
            author,
        ]
    )


@mcp.tool()
async def word_replace_tracked(
    local_path: str,
    old_text: str,
    new_text: str,
    author: str = "Hermes",
) -> dict:
    """Replace one exact Word text run with Microsoft Open XML tracked changes."""
    path = safe_local_path(local_path)
    return run_local_tool(
        [
            "office-collaboration",
            "word-replace-tracked",
            str(path),
            old_text,
            new_text,
            author,
        ]
    )


@mcp.tool()
async def powerpoint_replace_text(
    local_path: str,
    old_text: str,
    new_text: str,
) -> dict:
    """Replace one exact PowerPoint drawing text run with Microsoft Open XML."""
    path = safe_local_path(local_path)
    return run_local_tool(
        [
            "office-collaboration",
            "powerpoint-replace-text",
            str(path),
            old_text,
            new_text,
        ]
    )


def workbook_path(item: dict, suffix: str) -> str:
    drive_id, item_id = drive_coordinates(item)
    return (
        f"/drives/{quote(drive_id, safe='')}/items/"
        f"{quote(item_id, safe='')}/workbook/{suffix}"
    )


@mcp.tool()
async def read_excel_range(
    sharing_url: str,
    worksheet: str,
    address: str,
) -> dict:
    """Read a range from an existing Excel workbook under the Agent User identity."""
    item = await drive_item(sharing_url)
    if not str(item.get("name") or "").lower().endswith(".xlsx"):
        raise ValueError("read_excel_range requires an XLSX file.")
    response = await graph_request(
        "GET",
        workbook_path(
            item,
            (
                f"worksheets/{quote(worksheet, safe='')}/"
                f"range(address='{quote(address, safe=':!$')}')"
            ),
        ),
    )
    return response.json()


@mcp.tool()
async def write_excel_range(
    sharing_url: str,
    worksheet: str,
    address: str,
    values: list[list[object]],
) -> dict:
    """Write rectangular values to an Excel range under the Agent User identity."""
    if not values or not values[0]:
        raise ValueError("values must be a non-empty rectangular array.")
    width = len(values[0])
    if any(len(row) != width for row in values):
        raise ValueError("values must be rectangular.")
    item = await drive_item(sharing_url)
    if not str(item.get("name") or "").lower().endswith(".xlsx"):
        raise ValueError("write_excel_range requires an XLSX file.")
    response = await graph_request(
        "PATCH",
        workbook_path(
            item,
            (
                f"worksheets/{quote(worksheet, safe='')}/"
                f"range(address='{quote(address, safe=':!$')}')"
            ),
        ),
        json_body={"values": values},
    )
    return response.json()


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
