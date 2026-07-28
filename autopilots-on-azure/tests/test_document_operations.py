import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from autopilots_identity.collaboration_mcp import (
    stage_pending_publish,
)
from autopilots_identity.document_operations import (
    acknowledge_delivery,
    claim_delivery,
    configure_background_retry,
    copy_now,
    process_background_retry,
    read_receipt,
)


def source_item() -> dict:
    return {
        "id": "source-item",
        "name": "document.docx",
        "eTag": "etag-1",
        "file": {"mimeType": "application/octet-stream"},
        "parentReference": {"driveId": "source-drive"},
    }


class DocumentOperationTests(unittest.TestCase):
    def stage(
        self,
        workspace: Path,
        operation_id: str,
    ) -> Path:
        directory = workspace / operation_id
        directory.mkdir()
        target = directory / "document.docx"
        target.write_bytes(b"edited")
        stage_pending_publish(
            target=target,
            item=source_item(),
            sharing_url=(
                "https://contoso.sharepoint.com/document.docx"
            ),
            publish_kind="generic-local-edit",
            operation_scope="scope-1",
        )
        return target

    def make_due(self, target: Path) -> None:
        manifest_path = target.parent / "pending-publish.json"
        manifest = json.loads(
            manifest_path.read_text(encoding="utf-8")
        )
        manifest["background"]["nextAttemptUnix"] = 0
        manifest_path.write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )

    def test_background_retry_completes_and_records_delivery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            operation_id = "a" * 24
            with patch.dict(
                os.environ,
                {"M365_COLLABORATION_WORKSPACE": str(workspace)},
            ):
                target = self.stage(workspace, operation_id)
                configured = configure_background_retry(
                    operation_id,
                    "scope-1",
                    "user-1",
                    {"boundary": "one_to_one", "conversation": {}},
                )
                self.make_due(target)
                with (
                    patch(
                        "autopilots_identity.document_operations.collaboration.drive_item",
                        return_value=source_item(),
                    ),
                    patch(
                        "autopilots_identity.document_operations.collaboration.upload_drive_item",
                        return_value={
                            "id": "source-item",
                            "name": "document.docx",
                            "webUrl": "https://contoso/document",
                        },
                    ),
                ):
                    result = asyncio.run(
                        process_background_retry(operation_id)
                    )
                claim = claim_delivery(operation_id)
                concurrent_claim = claim_delivery(operation_id)
                acknowledged = acknowledge_delivery(
                    operation_id,
                    "activity-1",
                    claim["deliveryAttemptId"],
                )
                duplicate = asyncio.run(
                    process_background_retry(operation_id)
                )

        self.assertEqual(configured["status"], "scheduled")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(claim["status"], "ready")
        self.assertEqual(
            concurrent_claim["status"],
            "in_progress",
        )
        self.assertEqual(acknowledged["status"], "completed")
        self.assertTrue(duplicate["delivered"])

    def test_locked_retry_records_graph_error_and_next_attempt(self):
        request = httpx.Request(
            "PUT",
            "https://graph.microsoft.com/content",
        )
        locked = httpx.Response(
            423,
            request=request,
            json={
                "error": {
                    "code": "notAllowed",
                    "message": "The resource is locked",
                }
            },
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            operation_id = "b" * 24
            with patch.dict(
                os.environ,
                {"M365_COLLABORATION_WORKSPACE": str(workspace)},
            ):
                target = self.stage(workspace, operation_id)
                configure_background_retry(
                    operation_id,
                    "scope-1",
                    "user-1",
                    {"boundary": "one_to_one", "conversation": {}},
                )
                self.make_due(target)
                with (
                    patch(
                        "autopilots_identity.document_operations.collaboration.drive_item",
                        return_value=source_item(),
                    ),
                    patch(
                        "autopilots_identity.document_operations.collaboration.upload_drive_item",
                        side_effect=httpx.HTTPStatusError(
                            "locked",
                            request=request,
                            response=locked,
                        ),
                    ),
                ):
                    result = asyncio.run(
                        process_background_retry(operation_id)
                    )

        self.assertEqual(result["status"], "locked")
        self.assertEqual(result["attempt"], 1)
        self.assertEqual(
            result["graphError"]["code"],
            "notAllowed",
        )
        self.assertGreater(result["nextAttemptUnix"], time.time())

    def test_copy_now_shares_and_is_idempotent(self):
        copied_item = {
            "id": "copy-item",
            "name": "document - Hermes edit.docx",
            "webUrl": "https://contoso/copy",
            "parentReference": {"driveId": "agent-drive"},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            operation_id = "c" * 24
            with patch.dict(
                os.environ,
                {"M365_COLLABORATION_WORKSPACE": str(workspace)},
            ):
                self.stage(workspace, operation_id)
                with (
                    patch(
                        "autopilots_identity.document_operations.collaboration.upload_agent_results_copy",
                        return_value=copied_item,
                    ),
                    patch(
                        "autopilots_identity.document_operations.collaboration.invite_drive_item_user",
                        return_value={
                            "recipient": "user-1",
                            "role": "write",
                            "permissions": [{"id": "permission-1"}],
                        },
                    ),
                ):
                    result = asyncio.run(
                        copy_now(
                            operation_id,
                            "scope-1",
                            "user-1",
                            {
                                "boundary": "one_to_one",
                                "conversation": {},
                            },
                        )
                    )
                    duplicate = asyncio.run(
                        copy_now(
                            operation_id,
                            "scope-1",
                            "user-1",
                            {
                                "boundary": "one_to_one",
                                "conversation": {},
                            },
                        )
                    )
                receipt = read_receipt(operation_id)

        self.assertEqual(result["status"], "completed_copy")
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(receipt["driveItem"]["id"], "copy-item")

    def test_expired_background_retry_falls_back_to_shared_copy(self):
        copied_item = {
            "id": "copy-item",
            "name": "document - Hermes edit.docx",
            "webUrl": "https://contoso/copy",
            "parentReference": {"driveId": "agent-drive"},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            operation_id = "d" * 24
            with patch.dict(
                os.environ,
                {"M365_COLLABORATION_WORKSPACE": str(workspace)},
            ):
                target = self.stage(workspace, operation_id)
                configure_background_retry(
                    operation_id,
                    "scope-1",
                    "user-1",
                    {"boundary": "one_to_one", "conversation": {}},
                )
                manifest_path = (
                    target.parent / "pending-publish.json"
                )
                manifest = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                )
                manifest["background"]["deadlineUnix"] = 0
                manifest_path.write_text(
                    json.dumps(manifest),
                    encoding="utf-8",
                )
                with (
                    patch(
                        "autopilots_identity.document_operations.collaboration.upload_agent_results_copy",
                        return_value=copied_item,
                    ),
                    patch(
                        "autopilots_identity.document_operations.collaboration.invite_drive_item_user",
                        return_value={
                            "recipient": "user-1",
                            "role": "write",
                            "permissions": [{"id": "permission-1"}],
                        },
                    ),
                ):
                    result = asyncio.run(
                        process_background_retry(operation_id)
                    )

        self.assertEqual(result["status"], "completed_copy")


if __name__ == "__main__":
    unittest.main()
