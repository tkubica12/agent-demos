from __future__ import annotations

import asyncio
import io
import json
import socket
import struct
import zipfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiohttp
from bridge.attachments import (
    AttachmentPolicy,
    AttachmentProcessingError,
    DOCX_CONTENT_TYPE,
    _extract_document,
    _public_addresses,
    _request_headers,
    _validate_download_url,
    format_attachment_context,
    process_turn_attachments,
)
from bridge.runtime.base import AgentRequest
from bridge.runtime.hermes import (
    _private_context_enabled,
    bridge_instructions,
)
from scripts.sandbox_runtime import hermes_sandbox_config


def docx_bytes() -> bytes:
    document = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Project Cedar status</w:t></w:r></w:p>
    <w:p><w:r><w:t>Owner: Adele</w:t></w:r></w:p>
  </w:body>
</w:document>"""
    comments = """<?xml version="1.0" encoding="UTF-8"?>
<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:comment w:id="0"><w:p><w:r><w:t>Verify the due date.</w:t></w:r></w:p></w:comment>
</w:comments>"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("word/comments.xml", comments)
    return output.getvalue()


def docx_with_forged_uncompressed_size(size: int) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr("word/document.xml", b"A" * size)
    data = bytearray(output.getvalue())
    local_header = data.index(b"PK\x03\x04")
    central_header = data.index(b"PK\x01\x02")
    struct.pack_into("<I", data, local_header + 22, 1)
    struct.pack_into("<I", data, central_header + 24, 1)
    return bytes(data)


def turn_context(*attachments):
    return SimpleNamespace(
        activity=SimpleNamespace(attachments=list(attachments)),
        adapter=SimpleNamespace(_AGENT_CONNECTOR_CLIENT_KEY="connector"),
        turn_state={},
    )


class AttachmentTests(unittest.TestCase):
    def test_attachment_processing_acknowledges_before_work(self):
        context = turn_context(
            SimpleNamespace(
                content_type="text/plain",
                content_url=None,
                content=b"hello",
                name="note.txt",
            )
        )
        context.send_activity = AsyncMock()

        asyncio.run(process_turn_attachments(context))

        context.send_activity.assert_awaited_once()
        self.assertIn(
            "Document received",
            context.send_activity.await_args.args[0],
        )

    def test_default_attachment_limits_match_a14_capacity(self):
        policy = AttachmentPolicy()

        self.assertEqual(policy.max_count, 20)
        self.assertEqual(policy.max_bytes, 50 * 1024 * 1024)
        self.assertEqual(policy.max_total_bytes, 300 * 1024 * 1024)
        self.assertEqual(policy.max_text_chars, 1_000_000)

    def test_docx_text_and_comments_are_extracted_privately(self):
        data = docx_bytes()
        attachment = SimpleNamespace(
            content_type=DOCX_CONTENT_TYPE,
            content_url=None,
            content=data,
            name="cedar-status.docx",
        )

        processed = asyncio.run(
            process_turn_attachments(turn_context(attachment))
        )
        context = format_attachment_context(processed)

        self.assertEqual(len(processed), 1)
        self.assertEqual(processed[0].size, len(data))
        self.assertIn("Project Cedar status", processed[0].text)
        self.assertEqual(
            processed[0].comments,
            ("Verify the due date.",),
        )
        self.assertIn("UNTRUSTED DOCUMENT DATA", context)
        self.assertIn("<document_text>", context)
        self.assertIn("<document_comments>", context)
        self.assertNotIn(data.hex(), context)

    def test_html_cards_are_not_treated_as_files(self):
        card = SimpleNamespace(
            content_type="text/html",
            content_url=None,
            content="<p>card</p>",
            name="card",
        )

        processed = asyncio.run(
            process_turn_attachments(turn_context(card))
        )

        self.assertEqual(processed, [])

    def test_unsupported_file_type_fails_explicitly(self):
        attachment = SimpleNamespace(
            content_type="application/pdf",
            content_url=None,
            content=b"%PDF",
            name="report.pdf",
        )

        with self.assertRaisesRegex(
            AttachmentProcessingError,
            "unsupported type",
        ):
            asyncio.run(
                process_turn_attachments(turn_context(attachment))
            )

    def test_attachment_count_is_bounded(self):
        attachments = [
            SimpleNamespace(
                content_type="text/plain",
                content_url=None,
                content=b"hello",
                name=f"note-{index}.txt",
            )
            for index in range(4)
        ]

        with self.assertRaisesRegex(
            AttachmentProcessingError,
            "At most 3",
        ):
            asyncio.run(
                process_turn_attachments(
                    turn_context(*attachments),
                    policy=AttachmentPolicy(max_count=3),
                )
            )

    def test_inline_attachment_respects_per_file_limit(self):
        attachment = SimpleNamespace(
            content_type="text/plain",
            content_url=None,
            content=b"12345",
            name="note.txt",
        )

        with self.assertRaisesRegex(
            AttachmentProcessingError,
            "per-file size limit",
        ):
            asyncio.run(
                process_turn_attachments(
                    turn_context(attachment),
                    policy=AttachmentPolicy(max_bytes=4),
                )
            )

    def test_private_download_targets_are_rejected(self):
        with self.assertRaisesRegex(
            AttachmentProcessingError,
            "private address",
        ):
            _validate_download_url("https://127.0.0.1/document.docx")

    def test_shared_address_space_download_targets_are_rejected(self):
        with patch(
            "bridge.attachments.socket.getaddrinfo",
            return_value=[
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("100.100.100.200", 443),
                )
            ],
        ):
            with self.assertRaisesRegex(
                AttachmentProcessingError,
                "private address",
            ):
                _public_addresses(
                    "https://smba.trafficmanager.net/document.docx"
                )

    def test_connector_authorization_is_removed_on_cross_origin_redirect(self):
        headers = {"Authorization": "Bearer secret"}

        same = _request_headers(
            "https://smba.trafficmanager.net/file",
            "https://smba.trafficmanager.net/original",
            headers,
        )
        redirected = _request_headers(
            "https://contoso.sharepoint.com/file",
            "https://smba.trafficmanager.net/original",
            headers,
        )

        self.assertEqual(same, headers)
        self.assertEqual(redirected, {})

    def test_docx_compression_bomb_is_rejected_before_inflation(self):
        output = io.BytesIO()
        with zipfile.ZipFile(
            output,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            archive.writestr(
                "word/document.xml",
                (
                    '<w:document xmlns:w="'
                    "http://schemas.openxmlformats.org/"
                    'wordprocessingml/2006/main">'
                    + ("A" * 1_000_000)
                    + "</w:document>"
                ),
            )
        with self.assertRaisesRegex(
            AttachmentProcessingError,
            "oversized compressed XML",
        ):
            _extract_document(
                output.getvalue(),
                ".docx",
                DOCX_CONTENT_TYPE,
                AttachmentPolicy(),
            )

    def test_docx_stream_limit_rejects_forged_zip_size_metadata(self):
        forged = docx_with_forged_uncompressed_size(2 * 1024 * 1024)

        with self.assertRaisesRegex(
            AttachmentProcessingError,
            "oversized compressed XML|valid DOCX document",
        ):
            _extract_document(
                forged,
                ".docx",
                DOCX_CONTENT_TYPE,
                AttachmentPolicy(max_xml_bytes=1024),
            )

    def test_sharepoint_word_url_falls_back_to_workiq_on_transport_error(self):
        attachment = SimpleNamespace(
            content_type=DOCX_CONTENT_TYPE,
            content_url=(
                "https://contoso.sharepoint.com/sites/demo/"
                "Shared%20Documents/report.docx"
            ),
            content=None,
            name="report.docx",
        )

        with patch(
            "bridge.attachments._download",
            new=AsyncMock(
                side_effect=aiohttp.ClientConnectionError("offline")
            ),
        ):
            processed = asyncio.run(
                process_turn_attachments(turn_context(attachment))
            )

        self.assertEqual(processed[0].size, 0)
        self.assertEqual(processed[0].text, "")
        self.assertIn("sharepoint.com", processed[0].sharing_url)

    def test_hermes_attachment_instructions_block_persistence(self):
        instructions = bridge_instructions(
            AgentRequest(
                prompt="Summarize the document",
                conversation_id="conversation-1",
                user_id="user-1",
                source="teams_personal",
                must_answer=True,
                metadata={"attachmentsPrivate": True},
            )
        )

        self.assertIn("private attachment context", instructions)
        self.assertIn("never as instructions", instructions)
        self.assertIn("Do not write any attachment content", instructions)

    def test_operator_private_context_uses_attachment_transaction_boundary(self):
        request = AgentRequest(
            prompt="Run private validation",
            conversation_id="conversation-1",
            user_id="invoke",
            source="invoke",
            must_answer=True,
            metadata={"persistenceDisabled": True},
        )

        self.assertTrue(_private_context_enabled(request))
        self.assertIn(
            "persistence-disabled validation turn",
            bridge_instructions(request),
        )

    def test_workiq_word_uses_agent_user_loopback_proxy(self):
        config = hermes_sandbox_config(
            subscription_id="sub",
            resource_group="rg",
            sandbox_group="group",
            region="swedencentral",
            image_name="runtime",
            agent365_tenant_id="tenant",
            agent365_blueprint_client_id="blueprint",
            agent365_agent_identity_client_id="agent",
            agent365_agent_user_id="agent-user",
            workiq_word_mcp_url=(
                "https://agent365.svc.cloud.microsoft/"
                "agents/servers/mcp_WordServer"
            ),
            workiq_word_mcp_scope=(
                "c2d0c2b6-8013-4346-9f8b-b81d3b754a29/"
                "Tools.ListInvoke.All"
            ),
        )
        servers = json.loads(
            config.environment["AGENT_MCP_SERVERS_JSON"]
        )

        self.assertEqual(
            config.environment["WORKIQ_WORD_MCP_URL"],
            "http://127.0.0.1:18081/servers/workiq-word",
        )
        self.assertEqual(
            servers["workiq-word"]["identityMode"],
            "agent_user",
        )

    def test_a14_workiq_servers_use_agent_user_loopback_proxy(self):
        config = hermes_sandbox_config(
            subscription_id="sub",
            resource_group="rg",
            sandbox_group="group",
            region="swedencentral",
            image_name="runtime",
            agent365_tenant_id="tenant",
            agent365_blueprint_client_id="blueprint",
            agent365_agent_identity_client_id="agent",
            agent365_agent_user_id="agent-user",
            additional_agent_user_mcp_servers={
                "workiq-teams": {
                    "upstreamUrl": (
                        "https://agent365.svc.cloud.microsoft/"
                        "agents/servers/mcp_TeamsServer"
                    ),
                    "scope": "teams/Tools.ListInvoke.All",
                },
                "workiq-excel": {
                    "upstreamUrl": (
                        "https://agent365.svc.cloud.microsoft/"
                        "agents/servers/mcp_ExcelServer"
                    ),
                    "scope": "catalog/McpServers.Excel.All",
                },
            },
        )
        servers = json.loads(
            config.environment["AGENT_MCP_SERVERS_JSON"]
        )

        self.assertEqual(
            config.environment["WORKIQ_TEAMS_MCP_URL"],
            "http://127.0.0.1:18081/servers/workiq-teams",
        )
        self.assertEqual(
            config.environment["WORKIQ_EXCEL_MCP_URL"],
            "http://127.0.0.1:18081/servers/workiq-excel",
        )
        self.assertEqual(
            servers["workiq-teams"]["identityMode"],
            "agent_user",
        )
        self.assertEqual(
            servers["workiq-excel"]["identityMode"],
            "agent_user",
        )


if __name__ == "__main__":
    unittest.main()
