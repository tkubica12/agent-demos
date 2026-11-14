from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import socket
import struct
import zipfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web
from bridge.attachments import (
    AttachmentPolicy,
    AttachmentProcessingError,
    DOCX_CONTENT_TYPE,
    _PinnedResolver,
    _extract_document,
    _public_addresses,
    _request_headers,
    _validate_download_url,
    attachment_download_origins,
    format_attachment_context,
    process_turn_attachments,
    send_file_attachment,
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
    def test_private_origin_dns_requires_both_exact_origin_and_private_mode(self):
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.20.1.5", 443))]
        for origin, mode in (
            ("https://simulator.example", "false"),
            ("https://simulator.example:8443", "true"),
            ("", "true"),
        ):
            with (
                self.subTest(origin=origin, mode=mode),
                patch.dict(os.environ, {"ATTACHMENT_DOWNLOAD_ORIGINS": origin, "PRIVATE_NETWORK_ENABLED": mode}),
                patch("bridge.attachments.socket.getaddrinfo", return_value=addresses),
                self.assertRaises(AttachmentProcessingError),
            ):
                _public_addresses("https://simulator.example/api/file")

    def test_configured_private_dns_is_validated_and_pinned_without_rebinding(self):
        for address in ("10.20.1.5", "172.20.1.5", "192.168.10.5", "100.64.10.5", "fd00::5"):
            with (
                self.subTest(address=address),
                patch.dict(os.environ, {
                    "ATTACHMENT_DOWNLOAD_ORIGINS": "https://simulator.example", "PRIVATE_NETWORK_ENABLED": "true",
                }),
                patch("bridge.attachments.socket.getaddrinfo", return_value=[
                    (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443)),
                ]) as lookup,
            ):
                resolver = _PinnedResolver()
                resolver.pin("https://simulator.example/api/file")
                lookup.return_value = [
                    (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("169.254.169.254", 443)),
                ]
                self.assertEqual(asyncio.run(resolver.resolve("simulator.example", 443))[0]["host"], address)
                lookup.assert_called_once()

    def test_private_mode_still_rejects_metadata_loopback_unsafe_dns_and_ip_literals(self):
        for address in ("169.254.169.254", "127.0.0.1", "0.0.0.0", "224.0.0.1", "240.0.0.1", "192.0.2.1",
                        "::1", "::", "fe80::1", "ff02::1"):
            with (
                self.subTest(address=address),
                patch.dict(os.environ, {
                    "ATTACHMENT_DOWNLOAD_ORIGINS": "https://simulator.example", "PRIVATE_NETWORK_ENABLED": "true",
                }),
                patch("bridge.attachments.socket.getaddrinfo", return_value=[
                    (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.20.1.5", 443)),
                    (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443)),
                ]),
                self.assertRaises(AttachmentProcessingError),
            ):
                _public_addresses("https://simulator.example/api/file")
        with patch.dict(os.environ, {
            "ATTACHMENT_DOWNLOAD_ORIGINS": "https://10.20.1.5", "PRIVATE_NETWORK_ENABLED": "true",
        }), self.assertRaises(AttachmentProcessingError):
            _public_addresses("https://10.20.1.5/api/file")

    def test_explicit_origin_keeps_public_dns_and_exact_origin_boundary(self):
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("20.30.40.50", 443))]
        with (
            patch.dict(os.environ, {"ATTACHMENT_DOWNLOAD_ORIGINS": "https://simulator.example:8443"}),
            patch("bridge.attachments.socket.getaddrinfo", return_value=addresses),
        ):
            self.assertEqual(_public_addresses("https://simulator.example:8443/api/file")[0], "simulator.example")
            for url in (
                "https://simulator.example/api/file",
                "https://simulator.example.evil:8443/api/file",
                "https://user:password@simulator.example:8443/api/file",
            ):
                with self.assertRaises(AttachmentProcessingError):
                    _public_addresses(url)
        with (
            patch.dict(os.environ, {"ATTACHMENT_DOWNLOAD_ORIGINS": "https://simulator.example"}),
            patch("bridge.attachments.socket.getaddrinfo", return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("169.254.169.254", 443)),
            ]),
        ):
            with self.assertRaisesRegex(AttachmentProcessingError, "private address"):
                _public_addresses("https://simulator.example/api/file")

    def test_attachment_origins_require_https_or_explicit_loopback_opt_in(self):
        for value in (
            "http://simulator.example", "https://simulator.example/path",
            "https://simulator.example?query=1", "https://user@simulator.example",
            "https://simulator.example:99999", "http://127.0.0.1:8000",
        ):
            with patch.dict(os.environ, {"ATTACHMENT_DOWNLOAD_ORIGINS": value}, clear=True):
                with self.assertRaises(AttachmentProcessingError):
                    attachment_download_origins()
        with patch.dict(os.environ, {
            "ATTACHMENT_DOWNLOAD_ORIGINS": "http://127.0.0.1:8123",
            "ALLOW_LOOPBACK_ATTACHMENTS": "1",
        }):
            self.assertEqual(_public_addresses("http://127.0.0.1:8123/file")[0], "127.0.0.1")

    def test_capability_downloads_never_receive_service_tokens(self):
        with patch.dict(os.environ, {"ATTACHMENT_DOWNLOAD_ORIGINS": "https://simulator.example"}):
            self.assertEqual(_request_headers(
                "https://simulator.example/api/agent-files/file-1/content?capability=bounded",
                "https://simulator.example/v3/attachments/file-1/views/original",
                {"Authorization": "Bearer channel-token"},
            ), {})

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


class SimulatorAttachmentNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_loopback_capability_and_connector_downloads_extract_docx_and_txt(self):
        observed = []
        application = web.Application()

        async def download(incoming):
            observed.append(incoming.headers.get("Authorization"))
            if incoming.path.startswith("/api/"):
                self.assertEqual(incoming.query["capability"], "single-file-test-capability")
                return web.Response(body=docx_bytes(), content_type=DOCX_CONTENT_TYPE)
            self.assertEqual(incoming.headers.get("Authorization"), "Bearer channel-token")
            return web.Response(body=b"Connector text", content_type="text/plain")

        application.router.add_get("/api/agent-files/file-1/content", download)
        application.router.add_get("/v3/attachments/file-2/views/original", download)
        runner = web.AppRunner(application)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        origin = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        try:
            with patch.dict(os.environ, {
                "ATTACHMENT_DOWNLOAD_ORIGINS": origin, "ALLOW_LOOPBACK_ATTACHMENTS": "1",
            }):
                context = turn_context(
                    SimpleNamespace(content_type=DOCX_CONTENT_TYPE, name="status.docx", content=None,
                                    content_url=f"{origin}/api/agent-files/file-1/content?capability=single-file-test-capability"),
                    SimpleNamespace(content_type="text/plain", name="note.txt", content=None,
                                    content_url=f"{origin}/v3/attachments/file-2/views/original"),
                )
                context.activity.service_url = origin
                context.turn_state["connector"] = SimpleNamespace(client=SimpleNamespace(
                    headers={"Authorization": "Bearer channel-token"},
                ))
                processed = await process_turn_attachments(context)
                self.assertIn("Project Cedar status", processed[0].text)
                self.assertEqual(processed[1].text, "Connector text")
                self.assertEqual(observed, [None, "Bearer channel-token"])
        finally:
            await runner.cleanup()

    async def test_real_connector_upload_base64_and_file_activity(self):
        uploaded = []
        application = web.Application()

        async def upload(incoming):
            uploaded.append(await incoming.json())
            return web.json_response({"id": "uploaded-file"})

        application.router.add_post("/v3/conversations/conversation-1/attachments", upload)
        runner = web.AppRunner(application)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        origin = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        try:
            async with aiohttp.ClientSession(base_url=origin + "/") as client:
                document = docx_bytes()
                context = turn_context()
                context.activity.conversation = SimpleNamespace(id="conversation-1")
                context.send_activity = AsyncMock(return_value={"id": "file-message"})
                context.turn_state["connector"] = SimpleNamespace(client=client, base_uri=origin)
                result = await send_file_attachment(context, name="result.docx", content_type=DOCX_CONTENT_TYPE, data=document)
                self.assertEqual(result["id"], "file-message")
                self.assertEqual(base64.b64decode(uploaded[0]["originalBase64"]), document)
                attachment = context.send_activity.await_args.args[0].attachments[0]
                self.assertEqual(attachment.content_url, f"{origin}/v3/attachments/uploaded-file/views/original")
                self.assertEqual(attachment.content_type, DOCX_CONTENT_TYPE)
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    unittest.main()
