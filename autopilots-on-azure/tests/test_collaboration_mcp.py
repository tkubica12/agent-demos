import asyncio
import inspect
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from autopilots_identity.collaboration_mcp import (
    baseline_openxml_path,
    bind_pending_publish_scope,
    cancel_pending_office_publish,
    cleanup_expired_pending_publishes,
    drive_coordinates,
    find_pending_office_publishes,
    inspect_word_structure,
    invite_drive_item_user,
    minimax_docx_replace_text,
    minimax_docx_validate,
    normalize_markdown_text,
    patch_word_text,
    publish_pending_office_copy,
    retry_pending_office_publish,
    safe_local_path,
    share_id,
    stage_pending_publish,
    upload_drive_item,
    upload_agent_results_copy,
    upload_office_file,
    validate_sharing_url,
)


class CollaborationMcpTests(unittest.TestCase):
    def test_publish_tools_require_private_operation_scope(self):
        for tool in (
            upload_office_file,
            patch_word_text,
            retry_pending_office_publish,
            publish_pending_office_copy,
            cancel_pending_office_publish,
        ):
            self.assertIs(
                inspect.signature(tool).parameters[
                    "operation_scope"
                ].default,
                inspect.Parameter.empty,
            )

    def test_only_microsoft_sharing_urls_are_accepted(self):
        url = "https://contoso.sharepoint.com/sites/project/document.docx"

        self.assertEqual(validate_sharing_url(url), url)
        self.assertTrue(share_id(url).startswith("u!"))
        with self.assertRaisesRegex(ValueError, "Microsoft 365"):
            validate_sharing_url("https://example.com/document.docx")

    def test_local_files_are_confined_to_private_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            file_path = workspace / "turn" / "document.docx"
            file_path.parent.mkdir()
            file_path.write_bytes(b"document")

            with patch.dict(
                os.environ,
                {"M365_COLLABORATION_WORKSPACE": str(workspace)},
            ):
                self.assertEqual(
                    safe_local_path(str(file_path)),
                    file_path.resolve(),
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "private collaboration workspace",
                ):
                    safe_local_path(str(Path(temp_dir) / "outside.docx"))

    def test_drive_coordinates_require_drive_and_item_ids(self):
        self.assertEqual(
            drive_coordinates(
                {
                    "id": "item-1",
                    "parentReference": {"driveId": "drive-1"},
                }
            ),
            ("drive-1", "item-1"),
        )
        with self.assertRaisesRegex(RuntimeError, "drive and item IDs"):
            drive_coordinates({"id": "item-1"})

    def test_minimax_validation_combines_business_and_openxml_gates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            file_path = workspace / "document.docx"
            file_path.write_bytes(b"document")
            with (
                patch.dict(
                    os.environ,
                    {"M365_COLLABORATION_WORKSPACE": str(workspace)},
                ),
                patch(
                    "autopilots_identity.collaboration_mcp.run_local_tool",
                    side_effect=[
                        {"isValid": True, "errors": []},
                        {"Valid": True, "ErrorCount": 0},
                    ],
                ),
            ):
                result = asyncio.run(
                    minimax_docx_validate(str(file_path))
                )

        self.assertTrue(result["isValid"])
        self.assertTrue(result["openXml"]["Valid"])

    def test_minimax_validation_allows_unchanged_source_errors(self):
        existing_error = {
            "Id": "Sch_AttributeValueDataTypeDetailed",
            "Description": "The value is not a valid Int16.",
            "Path": "/w:styles[1]/w:style[1]",
            "Part": "/word/styles.xml",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            file_path = workspace / "document.docx"
            file_path.write_bytes(b"document")
            baseline_openxml_path(file_path).write_text(
                json.dumps(
                    {
                        "Valid": False,
                        "ErrorCount": 1,
                        "Errors": [existing_error],
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.dict(
                    os.environ,
                    {"M365_COLLABORATION_WORKSPACE": str(workspace)},
                ),
                patch(
                    "autopilots_identity.collaboration_mcp.run_local_tool",
                    side_effect=[
                        {"isValid": True, "errors": []},
                        {
                            "Valid": False,
                            "ErrorCount": 1,
                            "Errors": [existing_error],
                        },
                    ],
                ),
            ):
                result = asyncio.run(
                    minimax_docx_validate(str(file_path))
                )

        self.assertTrue(result["isValid"])
        self.assertEqual(
            result["openXmlChange"]["preExistingErrorCount"],
            1,
        )
        self.assertEqual(
            result["openXmlChange"]["newErrorCount"],
            0,
        )

    def test_minimax_validation_rejects_new_openxml_errors(self):
        existing_error = {
            "Id": "Existing",
            "Description": "Pre-existing",
            "Path": "/existing",
            "Part": "/word/styles.xml",
        }
        new_error = {
            "Id": "New",
            "Description": "Introduced by edit",
            "Path": "/new",
            "Part": "/word/document.xml",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            file_path = workspace / "document.docx"
            file_path.write_bytes(b"document")
            baseline_openxml_path(file_path).write_text(
                json.dumps(
                    {
                        "Valid": False,
                        "ErrorCount": 1,
                        "Errors": [existing_error],
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.dict(
                    os.environ,
                    {"M365_COLLABORATION_WORKSPACE": str(workspace)},
                ),
                patch(
                    "autopilots_identity.collaboration_mcp.run_local_tool",
                    side_effect=[
                        {"isValid": True, "errors": []},
                        {
                            "Valid": False,
                            "ErrorCount": 2,
                            "Errors": [existing_error, new_error],
                        },
                    ],
                ),
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "validation failed",
                ):
                    asyncio.run(
                        minimax_docx_validate(str(file_path))
                    )

    def test_minimax_replacement_requires_semantic_readback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            file_path = workspace / "document.docx"
            file_path.write_bytes(b"document")
            with (
                patch.dict(
                    os.environ,
                    {"M365_COLLABORATION_WORKSPACE": str(workspace)},
                ),
                patch(
                    "autopilots_identity.collaboration_mcp.run_local_tool",
                    return_value={"output": "Replaced 1 occurrence"},
                ),
                patch(
                    "autopilots_identity.collaboration_mcp.read_local_markdown",
                    side_effect=["Old value", "Old value"],
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "semantic verification failed",
                ):
                    asyncio.run(
                        minimax_docx_replace_text(
                            str(file_path),
                            "Old value",
                            "New value",
                        )
                    )

    def test_markdown_semantic_text_unescapes_form_underscores(self):
        rendered = (
            r"bytem \_\_\_\_\_\_, dítě \_\_\_\_\_\_"
        )

        self.assertEqual(
            normalize_markdown_text(rendered),
            "bytem ______, dítě ______",
        )

    def test_upload_retries_transient_sharepoint_lock(self):
        request = httpx.Request(
            "PUT",
            "https://graph.microsoft.com/content",
        )
        locked = httpx.Response(
            423,
            request=request,
            headers={"Retry-After": "1"},
        )
        success = httpx.Response(
            200,
            request=request,
            json={"id": "item-1", "eTag": "etag-2"},
        )

        async def run():
            with (
                patch(
                    "autopilots_identity.collaboration_mcp.graph_request",
                    side_effect=[
                        httpx.HTTPStatusError(
                            "locked",
                            request=request,
                            response=locked,
                        ),
                        success,
                    ],
                ) as graph,
                patch(
                    "autopilots_identity.collaboration_mcp.asyncio.sleep",
                ) as sleep,
            ):
                result = await upload_drive_item(
                    {
                        "id": "item-1",
                        "eTag": "etag-1",
                        "file": {"mimeType": "application/octet-stream"},
                        "parentReference": {"driveId": "drive-1"},
                    },
                    b"content",
                    "etag-1",
                )
            return result, graph, sleep

        result, graph, sleep = asyncio.run(run())

        self.assertEqual(result["eTag"], "etag-2")
        self.assertEqual(graph.call_count, 2)
        sleep.assert_awaited_once_with(1)

    def test_persistent_upload_lock_waits_thirty_seconds_per_tool_call(self):
        request = httpx.Request(
            "PUT",
            "https://graph.microsoft.com/content",
        )
        locked = httpx.Response(423, request=request)
        failure = httpx.HTTPStatusError(
            "locked",
            request=request,
            response=locked,
        )

        async def run():
            with (
                patch(
                    "autopilots_identity.collaboration_mcp.graph_request",
                    side_effect=failure,
                ) as graph,
                patch(
                    "autopilots_identity.collaboration_mcp.asyncio.sleep",
                ) as sleep,
            ):
                with self.assertRaises(httpx.HTTPStatusError):
                    await upload_drive_item(
                        {
                            "id": "item-1",
                            "eTag": "etag-1",
                            "parentReference": {"driveId": "drive-1"},
                        },
                        b"content",
                        "etag-1",
                    )
            return graph, sleep

        graph, sleep = asyncio.run(run())

        self.assertEqual(graph.call_count, 4)
        self.assertEqual(
            [call.args[0] for call in sleep.await_args_list],
            [5, 10, 15],
        )

    def test_upload_returns_structured_lock_outcome(self):
        request = httpx.Request(
            "PUT",
            "https://graph.microsoft.com/content",
        )
        locked = httpx.Response(423, request=request)
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            file_path = workspace / "document.docx"
            file_path.write_bytes(b"document")
            with (
                patch.dict(
                    os.environ,
                    {"M365_COLLABORATION_WORKSPACE": str(workspace)},
                ),
                patch(
                    "autopilots_identity.collaboration_mcp.drive_item",
                    return_value={
                        "id": "item-1",
                        "eTag": "etag-1",
                        "parentReference": {"driveId": "drive-1"},
                    },
                ),
                patch(
                    "autopilots_identity.collaboration_mcp.upload_drive_item",
                    side_effect=httpx.HTTPStatusError(
                        "locked",
                        request=request,
                        response=locked,
                    ),
                ),
            ):
                result = asyncio.run(
                    upload_office_file(
                        "https://contoso.sharepoint.com/document.docx",
                        str(file_path),
                        "etag-1",
                        "scope-1",
                    )
                )
                operation_directory = workspace / result["operationId"]
                retained = (
                    operation_directory
                    / "document.docx"
                ).read_bytes()

        self.assertEqual(result["status"], "locked")
        self.assertTrue(result["retryable"])
        self.assertEqual(result["retryAfterSeconds"], 30)
        self.assertRegex(result["operationId"], r"^[0-9a-f]{24}$")
        self.assertEqual(retained, b"document")
        self.assertEqual(
            result["choices"],
            ["retry_original", "send_copy", "cancel"],
        )
        self.assertEqual(
            result["graphError"]["statusCode"],
            423,
        )

    def test_pending_word_patch_rebases_after_source_change(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            operation_directory = workspace / ("b" * 24)
            operation_directory.mkdir()
            target = operation_directory / "form.docx"
            target.write_bytes(b"edited-old")
            edits = [
                {
                    "paragraphIndex": 1,
                    "textNodeIndex": 2,
                    "searchText": "______",
                    "replacementText": "Poděbradská",
                }
            ]
            old_item = {
                "id": "item-1",
                "name": "form.docx",
                "eTag": "etag-1",
                "file": {
                    "mimeType": (
                        "application/vnd.openxmlformats-officedocument."
                        "wordprocessingml.document"
                    )
                },
                "parentReference": {"driveId": "drive-1"},
            }
            with patch.dict(
                os.environ,
                {"M365_COLLABORATION_WORKSPACE": str(workspace)},
            ):
                stage_pending_publish(
                    target=target,
                    item=old_item,
                    sharing_url=(
                        "https://contoso.sharepoint.com/form.docx"
                    ),
                    publish_kind="word-text-node-patch",
                    edits=edits,
                )
                new_item = {**old_item, "eTag": "etag-2"}
                with (
                    patch(
                        "autopilots_identity.collaboration_mcp.drive_item",
                        return_value=new_item,
                    ),
                    patch(
                        "autopilots_identity.collaboration_mcp.download_drive_item",
                        return_value=b"fresh-source",
                    ),
                    patch(
                        "autopilots_identity.collaboration_mcp.run_local_tool",
                        return_value={"Success": True, "Applied": 1},
                    ),
                    patch(
                        "autopilots_identity.collaboration_mcp.read_local_markdown",
                        return_value="bytem Poděbradská",
                    ),
                    patch(
                        "autopilots_identity.collaboration_mcp.minimax_docx_validate",
                        return_value={"isValid": True},
                    ),
                    patch(
                        "autopilots_identity.collaboration_mcp.upload_drive_item",
                        return_value={
                            "id": "item-1",
                            "eTag": "etag-3",
                        },
                    ) as upload,
                ):
                    result = asyncio.run(
                        retry_pending_office_publish(
                            "b" * 24,
                            "scope-1",
                        )
                    )

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["rebased"])
        self.assertEqual(upload.call_args.args[2], "etag-2")
        self.assertFalse(operation_directory.exists())

    def test_pending_generic_edit_fails_closed_after_source_change(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            operation_directory = workspace / ("c" * 24)
            operation_directory.mkdir()
            target = operation_directory / "document.docx"
            target.write_bytes(b"edited")
            item = {
                "id": "item-1",
                "name": "document.docx",
                "eTag": "etag-1",
                "parentReference": {"driveId": "drive-1"},
            }

            with (
                patch.dict(
                    os.environ,
                    {"M365_COLLABORATION_WORKSPACE": str(workspace)},
                ),
                patch(
                    "autopilots_identity.collaboration_mcp.drive_item",
                    return_value={**item, "eTag": "etag-2"},
                ),
            ):
                stage_pending_publish(
                    target=target,
                    item=item,
                    sharing_url=(
                        "https://contoso.sharepoint.com/document.docx"
                    ),
                    publish_kind="generic-local-edit",
                )
                result = asyncio.run(
                    retry_pending_office_publish(
                        "c" * 24,
                        "scope-1",
                    )
                )
                retained = operation_directory.exists()

        self.assertEqual(result["status"], "source_changed")
        self.assertFalse(result["retryable"])
        self.assertTrue(retained)

    def test_rebased_word_patch_remains_staged_when_lock_persists(self):
        request = httpx.Request(
            "PUT",
            "https://graph.microsoft.com/content",
        )
        locked = httpx.Response(423, request=request)
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            operation_directory = workspace / ("f" * 24)
            operation_directory.mkdir()
            target = operation_directory / "form.docx"
            target.write_bytes(b"edited-old")
            item = {
                "id": "item-1",
                "name": "form.docx",
                "eTag": "etag-1",
                "parentReference": {"driveId": "drive-1"},
            }

            with (
                patch.dict(
                    os.environ,
                    {"M365_COLLABORATION_WORKSPACE": str(workspace)},
                ),
                patch(
                    "autopilots_identity.collaboration_mcp.drive_item",
                    return_value={**item, "eTag": "etag-2"},
                ),
                patch(
                    "autopilots_identity.collaboration_mcp.download_drive_item",
                    return_value=b"fresh-source",
                ),
                patch(
                    "autopilots_identity.collaboration_mcp.run_local_tool",
                    return_value={"Success": True, "Applied": 1},
                ),
                patch(
                    "autopilots_identity.collaboration_mcp.read_local_markdown",
                    return_value="bytem Poděbradská",
                ),
                patch(
                    "autopilots_identity.collaboration_mcp.minimax_docx_validate",
                    return_value={"isValid": True},
                ),
                patch(
                    "autopilots_identity.collaboration_mcp.upload_drive_item",
                    side_effect=httpx.HTTPStatusError(
                        "locked",
                        request=request,
                        response=locked,
                    ),
                ),
            ):
                manifest = stage_pending_publish(
                    target=target,
                    item=item,
                    sharing_url=(
                        "https://contoso.sharepoint.com/form.docx"
                    ),
                    publish_kind="word-text-node-patch",
                    edits=[
                        {
                            "paragraphIndex": 1,
                            "textNodeIndex": 2,
                            "searchText": "______",
                            "replacementText": "Poděbradská",
                        }
                    ],
                    operation_scope="scope-1",
                )
                result = asyncio.run(
                    retry_pending_office_publish(
                        "f" * 24,
                        "scope-1",
                    )
                )
                retained = target.is_file()
                retained_manifest = json.loads(
                    (
                        operation_directory / "pending-publish.json"
                    ).read_text(encoding="utf-8")
                )

        self.assertEqual(result["status"], "locked")
        self.assertEqual(result["operationId"], "f" * 24)
        self.assertTrue(retained)
        self.assertEqual(
            retained_manifest["expectedETag"],
            "etag-2",
        )

    def test_pending_edit_can_publish_copy_or_cancel(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            with patch.dict(
                os.environ,
                {"M365_COLLABORATION_WORKSPACE": str(workspace)},
            ):
                copy_directory = workspace / ("d" * 24)
                copy_directory.mkdir()
                copy_target = copy_directory / "document.docx"
                copy_target.write_bytes(b"edited")
                stage_pending_publish(
                    target=copy_target,
                    item={
                        "eTag": "etag-1",
                        "file": {
                            "mimeType": "application/octet-stream"
                        },
                    },
                    sharing_url=(
                        "https://contoso.sharepoint.com/document.docx"
                    ),
                    publish_kind="generic-local-edit",
                )
                with (
                    patch(
                        "autopilots_identity.collaboration_mcp.upload_agent_results_copy",
                        return_value={
                            "id": "copy-1",
                            "webUrl": "https://contoso/copy",
                        },
                    ),
                    patch(
                        "autopilots_identity.collaboration_mcp.invite_drive_item_user",
                        return_value={
                            "recipient": "user-1",
                            "role": "write",
                            "permissions": [{"id": "permission-1"}],
                        },
                    ),
                ):
                    copied = asyncio.run(
                        publish_pending_office_copy(
                            "d" * 24,
                            "user-1",
                            "scope-1",
                        )
                    )

                cancel_directory = workspace / ("e" * 24)
                cancel_directory.mkdir()
                cancel_target = cancel_directory / "document.docx"
                cancel_target.write_bytes(b"edited")
                stage_pending_publish(
                    target=cancel_target,
                    item={"eTag": "etag-1"},
                    sharing_url=(
                        "https://contoso.sharepoint.com/document.docx"
                    ),
                    publish_kind="generic-local-edit",
                )
                cancelled = asyncio.run(
                    cancel_pending_office_publish(
                        "e" * 24,
                        "scope-1",
                    )
                )

        self.assertEqual(copied["status"], "completed_copy")
        self.assertFalse(copy_directory.exists())
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertFalse(cancel_directory.exists())

    def test_copy_sharing_grants_specific_user_write_access(self):
        request = httpx.Request(
            "POST",
            "https://graph.microsoft.com/invite",
        )
        response = httpx.Response(
            200,
            request=request,
            json={
                "value": [
                    {
                        "id": "permission-1",
                        "roles": ["write"],
                    }
                ]
            },
        )
        with patch(
            "autopilots_identity.collaboration_mcp.graph_request",
            return_value=response,
        ) as graph:
            result = asyncio.run(
                invite_drive_item_user(
                    {
                        "id": "item-1",
                        "parentReference": {"driveId": "drive-1"},
                    },
                    "user-1",
                    "write",
                )
            )

        self.assertEqual(result["role"], "write")
        self.assertEqual(
            graph.call_args.kwargs["json_body"],
            {
                "recipients": [{"objectId": "user-1"}],
                "requireSignIn": True,
                "sendInvitation": False,
                "roles": ["write"],
            },
        )

    def test_pending_edit_is_bound_to_private_conversation_scope(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            operation_directory = workspace / ("2" * 24)
            operation_directory.mkdir()
            target = operation_directory / "document.docx"
            target.write_bytes(b"edited")
            with patch.dict(
                os.environ,
                {"M365_COLLABORATION_WORKSPACE": str(workspace)},
            ):
                stage_pending_publish(
                    target=target,
                    item={"eTag": "etag-1"},
                    sharing_url=(
                        "https://contoso.sharepoint.com/document.docx"
                    ),
                    publish_kind="generic-local-edit",
                    operation_scope="conversation-a",
                )
                recovered = asyncio.run(
                    find_pending_office_publishes("conversation-a")
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "another conversation",
                ):
                    asyncio.run(
                        cancel_pending_office_publish(
                            "2" * 24,
                            "conversation-b",
                        )
                    )
                cancelled = asyncio.run(
                    cancel_pending_office_publish(
                        "2" * 24,
                        "conversation-a",
                    )
                )

        self.assertEqual(
            recovered["operations"][0]["operationId"],
            "2" * 24,
        )
        self.assertEqual(cancelled["status"], "cancelled")

    def test_legacy_pending_edit_can_be_bound_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            operation_directory = workspace / ("3" * 24)
            operation_directory.mkdir()
            target = operation_directory / "document.docx"
            target.write_bytes(b"edited")
            with patch.dict(
                os.environ,
                {"M365_COLLABORATION_WORKSPACE": str(workspace)},
            ):
                stage_pending_publish(
                    target=target,
                    item={"eTag": "etag-1"},
                    sharing_url=(
                        "https://contoso.sharepoint.com/document.docx"
                    ),
                    publish_kind="generic-local-edit",
                )
                bound = bind_pending_publish_scope(
                    "3" * 24,
                    "scope-1",
                )
                recovered = asyncio.run(
                    find_pending_office_publishes("scope-1")
                )

        self.assertEqual(bound["status"], "bound")
        self.assertEqual(
            recovered["operations"][0]["operationId"],
            "3" * 24,
        )

    def test_expired_pending_edit_is_removed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            operation_directory = workspace / ("1" * 24)
            operation_directory.mkdir()
            target = operation_directory / "document.docx"
            target.write_bytes(b"edited")
            with patch.dict(
                os.environ,
                {"M365_COLLABORATION_WORKSPACE": str(workspace)},
            ):
                stage_pending_publish(
                    target=target,
                    item={"eTag": "etag-1"},
                    sharing_url=(
                        "https://contoso.sharepoint.com/document.docx"
                    ),
                    publish_kind="generic-local-edit",
                )
                manifest_path = (
                    operation_directory / "pending-publish.json"
                )
                manifest = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                )
                manifest["expiresAtUnix"] = 0
                manifest_path.write_text(
                    json.dumps(manifest),
                    encoding="utf-8",
                )
                removed = cleanup_expired_pending_publishes()

        self.assertEqual(removed, 1)
        self.assertFalse(operation_directory.exists())

    def test_agent_results_copy_uses_rename_conflict_behavior(self):
        request = httpx.Request(
            "PUT",
            "https://graph.microsoft.com/content",
        )
        response = httpx.Response(
            201,
            request=request,
            json={"id": "copy-1", "name": "document - Hermes edit.docx"},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "document.docx"
            target.write_bytes(b"edited")
            with (
                patch(
                    "autopilots_identity.collaboration_mcp.ensure_agent_results_folder",
                    return_value={"id": "folder-1"},
                ),
                patch(
                    "autopilots_identity.collaboration_mcp.graph_request",
                    return_value=response,
                ) as graph,
            ):
                result = asyncio.run(
                    upload_agent_results_copy(
                        target,
                        "application/octet-stream",
                    )
                )

        self.assertEqual(result["id"], "copy-1")
        self.assertIn(
            "conflictBehavior=rename",
            graph.call_args.args[1],
        )

    def test_universal_word_structure_inspection_cleans_up(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            with (
                patch.dict(
                    os.environ,
                    {"M365_COLLABORATION_WORKSPACE": str(workspace)},
                ),
                patch(
                    "autopilots_identity.collaboration_mcp.drive_item",
                    return_value={
                        "id": "item-1",
                        "name": "form.docx",
                        "eTag": "etag-1",
                        "parentReference": {"driveId": "drive-1"},
                    },
                ),
                patch(
                    "autopilots_identity.collaboration_mcp.download_drive_item",
                    return_value=b"docx",
                ),
                patch(
                    "autopilots_identity.collaboration_mcp.run_local_tool",
                    return_value={
                        "Paragraphs": [
                            {
                                "ParagraphIndex": 1,
                                "TextNodes": [
                                    {
                                        "TextNodeIndex": 2,
                                        "Text": "bytem ______",
                                    }
                                ],
                            }
                        ],
                    },
                ) as local_tool,
            ):
                result = asyncio.run(
                    inspect_word_structure(
                        "https://contoso.sharepoint.com/form.docx"
                    )
                )

        self.assertEqual(
            result["Paragraphs"][0]["ParagraphIndex"],
            1,
        )
        command = local_tool.call_args.args[0]
        self.assertIn("word-inspect-structure", command)
        self.assertFalse(any(workspace.rglob("*.docx")))

    def test_universal_word_patch_verifies_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            with (
                patch.dict(
                    os.environ,
                    {"M365_COLLABORATION_WORKSPACE": str(workspace)},
                ),
                patch(
                    "autopilots_identity.collaboration_mcp.drive_item",
                    return_value={
                        "id": "item-1",
                        "name": "form.docx",
                        "eTag": "etag-1",
                        "parentReference": {"driveId": "drive-1"},
                    },
                ),
                patch(
                    "autopilots_identity.collaboration_mcp.download_drive_item",
                    return_value=b"docx",
                ),
                patch(
                    "autopilots_identity.collaboration_mcp.run_local_tool",
                    return_value={"Success": True, "Applied": 1},
                ) as local_tool,
                patch(
                    "autopilots_identity.collaboration_mcp.read_local_markdown",
                    return_value="bytem Poděbradská",
                ),
                patch(
                    "autopilots_identity.collaboration_mcp.minimax_docx_validate",
                    return_value={"isValid": True},
                ),
                patch(
                    "autopilots_identity.collaboration_mcp.upload_drive_item",
                    return_value={"id": "item-1", "eTag": "etag-2"},
                ),
            ):
                result = asyncio.run(
                    patch_word_text(
                        "https://contoso.sharepoint.com/form.docx",
                        [
                            {
                                "paragraphIndex": 1,
                                "textNodeIndex": 2,
                                "searchText": "______",
                                "replacementText": "Poděbradská",
                            }
                        ],
                        "scope-1",
                    )
                )

        self.assertEqual(result["applied"], 1)
        command = local_tool.call_args.args[0]
        self.assertIn("word-patch-text-nodes", command)
        self.assertFalse(any(workspace.rglob("*.docx")))


if __name__ == "__main__":
    unittest.main()
