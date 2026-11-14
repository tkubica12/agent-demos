"""Real simulator/Agents SDK HTTP smoke with explicitly stubbed Hermes and local test identities."""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import io
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import zipfile


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--simulator-root", type=Path, required=True)
parser.add_argument("--simulator-site-packages", type=Path)
parser.add_argument("--mode", choices=("standard", "demo"), default="standard")
parser.add_argument("--widgets-browser", action="store_true", help="Exercise widgets in installed Chromium-based Edge.")
parser.add_argument("--media-dir", type=Path, help="Optional external directory for widget demo media.")
args = parser.parse_args()
root = args.simulator_root.resolve()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(root))
site_packages = root / ".venv" / "Lib" / "site-packages" if os.name == "nt" else root / ".venv" / "lib" / "python3.12" / "site-packages"
sys.path.append(str(args.simulator_site_packages or site_packages))
temporary = tempfile.TemporaryDirectory(prefix="bridge-simulator-")
directory = Path(temporary.name)

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Bridge simulator smoke CA")])
now = datetime.now(timezone.utc)
certificate = (
    x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
    .public_key(key.public_key()).serial_number(x509.random_serial_number())
    .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
    .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
    .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
    .sign(key, hashes.SHA256())
)
cert_path, key_path = directory / "cert.pem", directory / "key.pem"
cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
os.environ["SSL_CERT_FILE"] = str(cert_path)
os.environ["REQUESTS_CA_BUNDLE"] = str(cert_path)

import httpx
import jwt
import uvicorn
from microsoft_agents.activity import Activity
from microsoft_agents.hosting.core.app.proactive import Conversation

from backend.app import create_app
from backend.config import Settings

spec = importlib.util.spec_from_file_location("smoke_test_idp", root / "tests" / "test_idp.py")
idp_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(idp_module)
AGENT = "20000000-0000-4000-8000-000000000001"
CHANNEL = "10000000-0000-4000-8000-000000000001"
demo = args.mode == "demo"
servers, tasks = [], []


def listener(*, tls=False):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    sock.setblocking(False)
    return sock, f"{'https' if tls else 'http'}://127.0.0.1:{sock.getsockname()[1]}"


async def serve(application, sock, *, tls=False):
    options = {"ssl_certfile": str(cert_path), "ssl_keyfile": str(key_path)} if tls else {}
    server = uvicorn.Server(uvicorn.Config(
        application, log_level="critical", access_log=False, timeout_graceful_shutdown=5, **options,
    ))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    servers.append(server)
    tasks.append(task)
    for _ in range(500):
        if server.started:
            return
        if task.done():
            await task
        await asyncio.sleep(.01)
    raise RuntimeError("Server startup timed out")


async def main():
    logging.getLogger("microsoft_agents").setLevel(logging.CRITICAL)
    sim_sock, sim_url = listener(tls=demo)
    bridge_sock, bridge_url = listener(tls=demo)
    for name in list(os.environ):
        if name.startswith(("AGENT365_", "CONNECTIONS", "DEMO_", "WORKIQ_", "TRANSPORT_")):
            del os.environ[name]
    os.environ.update({
        "USE_AGENTIC_AUTH": "false", "API_SERVER_KEY": secrets.token_urlsafe(32),
        "AUTOPILOT_TEAMS_NAME": "Agent", "AUTOPILOT_BRIDGE_DEBUG": "true",
        "ATTACHMENT_DOWNLOAD_ORIGINS": sim_url, "ALLOW_LOOPBACK_ATTACHMENTS": "1",
        "TRANSPORT_JOURNAL_PATH": str(directory / "bridge.db"),
        "TRANSPORT_JOURNAL_BACKUP": str(directory / "backup" / "bridge.db"),
    })
    demo_key = secrets.token_urlsafe(32)
    if demo:
        os.environ.update({"DEMO_CHANNEL_KEY": demo_key, "DEMO_SERVICE_URL": sim_url + "/demo/" + demo_key})
    else:
        os.environ.update({
            "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__AUTHTYPE": "ClientSecret",
            "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID": AGENT,
            "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID": idp_module.TEST_AUTH["tenant_id"],
            "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET": "synthetic-test-only",
            "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__SCOPES__0": CHANNEL + "/.default",
            "CONNECTIONSMAP__0__SERVICEURL": "^" + re.escape(sim_url) + "/?$",
            "CONNECTIONSMAP__0__CONNECTION": "SERVICE_CONNECTION",
        })
    from bridge import app as bridge
    from bridge.attachments import DOCX_CONTENT_TYPE, send_file_attachment
    from bridge.proactive_delivery import send_proactive_activity
    from bridge.runtime.base import AgentResponse
    from bridge.html_widgets import START, END, decode_submission, render_widget, _cipher

    widget_spec = {
        "title": "Interactive budget planner",
        "html": '<label>Budget <input aria-label="Budget" type="number" value="5" data-widget-field="budget"></label>'
                '<p>Estimated total: <output id="total">10</output></p>'
                '<button data-hermes-action="discuss">Discuss this plan</button>',
        "css": "label{display:block}input{margin:8px}button{padding:8px}",
        "javascript": "const input=document.querySelector('[data-widget-field]');"
                      "input.addEventListener('input',()=>document.getElementById('total').textContent=String(Number(input.value)*2));",
        "actions": [{"id": "discuss", "label": "Discuss this plan"}],
    }

    invocations, references, events, token_scopes, requests = [], [], [], [], []
    sdk_invokes, http_requests = [], []
    original_document_card = bridge.pending_document_card_activity

    async def invoke(**kwargs):
        invocations.append(kwargs)
        references.append(kwargs["metadata"]["deliveryReference"])
        await asyncio.sleep(.08)
        if kwargs.get("metadata", {}).get("widgetSelection"):
            return AgentResponse(text="Plan received in this same chat. " + kwargs["message"].split("Form fields")[1].split("\nThis")[0])
        if "widget please" in kwargs["message"].lower():
            return AgentResponse(text="Here is your interactive planner.\n" + START + json.dumps(widget_spec) + END)
        if "card please" in kwargs["message"].lower():
            return AgentResponse(text='Here is a card.\n<ADAPTIVE_CARD_REQUEST>{"kind":"display","title":"Smoke card","facts":[{"label":"Result","value":"Ready"}]}</ADAPTIVE_CARD_REQUEST>')
        return AgentResponse(text="Stub Hermes reply")

    async def no_pending_document(**kwargs):
        return None

    bridge.invoke_agent_runtime = invoke
    bridge.pending_document_card_activity = no_pending_document
    claimed = set()

    async def claim_widget(**kwargs):
        interaction = kwargs["interaction_id"]
        already = interaction in claimed
        claimed.add(interaction)
        return {"claimed": not already}

    bridge.runtime_adapter().claim_interaction_action = claim_widget
    common = {
        "database_url": "sqlite:///" + (directory / "simulator.db").as_posix(),
        "storage_path": directory / "files", "service_url": sim_url,
        "redirect_uri": sim_url + "/", "allowed_origins": (sim_url,),
    }
    if demo:
        settings = Settings(
            auth_mode="demo", demo_access_code=secrets.token_urlsafe(24),
            demo_expires_at=(now + timedelta(hours=1)).isoformat(),
            agents_json=json.dumps([{
                "name": "agent-1", "displayName": "Agent", "clientId": AGENT,
                "messagingEndpoint": bridge_url + "/api/messages", "demoKey": demo_key,
            }]), **common,
        )
    else:
        idp_sock, idp_url = listener()
        issuer = idp_module.TestIssuer(idp_url, Settings(**idp_module.TEST_AUTH, allowed_origins=(sim_url,)))
        await serve(issuer.app(), idp_sock)
        jwks = jwt.PyJWKClient(idp_url + "/jwks")

        async def local_keys(token):
            return (await asyncio.to_thread(jwks.get_signing_key_from_jwt, token)).key

        bridge.channel_authentication.validator._get_public_key_or_secret = local_keys
        settings = Settings(**idp_module.TEST_AUTH, test_idp=True, test_issuer=idp_url,
                            channel_client_id=CHANNEL, channel_client_secret="synthetic-test-only", **common)
    simulator = create_app(settings)
    publish = simulator.state.hub.publish

    def capture(event, *a, **kw):
        events.append(event)
        return publish(event, *a, **kw)

    simulator.state.hub.publish = capture

    @bridge.app.middleware("http")
    async def capture_sdk_invoke(request, call_next):
        if request.url.path == "/api/messages":
            incoming = json.loads(await request.body())
            if incoming.get("name") == "htmlwidget/calltool":
                sdk_invokes.append(incoming)
        return await call_next(request)

    @simulator.middleware("http")
    async def capture_connector(request, call_next):
        http_requests.append((request.method, request.url.path))
        if "/v3/" in request.url.path:
            requests.append((request.url.path, "authorization" in request.headers))
        return await call_next(request)

    if not demo:
        async def inbound_token(agent_client_id):
            return issuer.mint(overrides={"iss": settings.issuer, "aud": agent_client_id, "azp": CHANNEL,
                                          "idtyp": "app", "ver": "2.0", "scp": None})

        async def outbound_token(resource, scopes, force_refresh=False):
            token_scopes.append(scopes)
            return issuer.mint(overrides={"aud": CHANNEL, "azp": AGENT, "idtyp": "app", "ver": "2.0", "scp": None})

        simulator.state.channel.auth.token = inbound_token
        bridge.channel_connection_manager.get_default_connection().get_access_token = outbound_token
    await serve(bridge.app, bridge_sock, tls=demo)
    await serve(simulator, sim_sock, tls=demo)
    async with httpx.AsyncClient(base_url=sim_url, timeout=30, trust_env=False,
                                 verify=ssl.create_default_context(cafile=str(cert_path))) as client:
        async def call(method, path, *, headers=None, status=200, **kwargs):
            result = await client.request(method, path, headers=headers or owner_headers, **kwargs)
            assert result.status_code == status, (method, path, result.status_code, result.text)
            if method == "POST" and path.startswith("/api/conversations/") and path.endswith("/messages"):
                await asyncio.wait_for(
                    asyncio.gather(*list(bridge.background_turns)), timeout=10,
                )
            return result.json() if result.content else None

        async def login(name):
            if not demo:
                return {"Authorization": "Bearer " + issuer.mint(name)}
            result = await client.post("/api/demo/login", json={"accessCode": settings.demo_access_code, "displayName": name})
            result.raise_for_status()
            return {"Authorization": "Bearer " + result.json()["accessToken"]}

        owner_headers, member_headers = await login("owner"), await login("member")
        await call("GET", "/api/me")
        member = await call("GET", "/api/me", headers=member_headers)
        if demo:
            users = await call("GET", "/api/users")
            agent = {"userId": next(user["id"] for user in users if user.get("isAgent"))}
        else:
            agent = await call("POST", "/api/agents", status=201, json={
                "name": "Agent", "clientId": AGENT, "endpointUrl": bridge_url + "/api/messages",
            })
        personal = await call("POST", "/api/chats", status=201, json={"memberIds": [agent["userId"]]})
        group = await call("POST", "/api/chats", status=201, json={"name": "Smoke group", "memberIds": [agent["userId"], member["id"]]})
        pconv, gconv = personal["conversationId"], group["conversationId"]
        await call("POST", f"/api/conversations/{pconv}/messages", status=201, json={"text": "hello"})
        messages = await call("GET", f"/api/conversations/{pconv}/messages")
        assert any(message["text"] == "Stub Hermes reply" for message in messages)
        await call("POST", f"/api/conversations/{gconv}/messages", status=201, headers=member_headers, json={
            "text": "<at>Agent</at> hello group", "mentions": [{"userId": agent["userId"], "displayName": "Agent"}],
        })
        assert invocations[-1]["source"] == "teams_group"
        assert "Signal type: explicit_bot_mention" in invocations[-1]["message"]
        assert invocations[-1]["auth_context"].selected_mode == "none"
        assert invocations[-1]["user_id"] != invocations[0]["user_id"]
        assert any(event["type"] == "typing" for event in events)
        assert any(event["type"] == "reaction.changed" for event in events)
        print("PASS login, personal/group multi-user chat, @mention, typing and reactions")
        await call("POST", f"/api/conversations/{pconv}/messages", status=201, json={"text": "card please"})
        messages = await call("GET", f"/api/conversations/{pconv}/messages")
        assert any(attachment.get("contentType") == "application/vnd.microsoft.card.adaptive"
                   for message in messages for attachment in message.get("activityAttachments", []))
        document = io.BytesIO()
        with zipfile.ZipFile(document, "w") as archive:
            archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Smoke DOCX text</w:t></w:r></w:p></w:body></w:document>')
        for name, data, mime, text in (
            ("smoke.txt", b"Smoke TXT text", "text/plain", "Smoke TXT text"),
            ("smoke.docx", document.getvalue(), DOCX_CONTENT_TYPE, "Smoke DOCX text"),
        ):
            file = await call("POST", f"/api/conversations/{pconv}/files", status=201, files={"file": (name, data, mime)})
            await call("POST", f"/api/conversations/{pconv}/messages", status=201, json={"text": "read the file", "attachmentIds": [file["id"]]})
            assert text in invocations[-1]["message"]
            assert invocations[-1]["metadata"]["attachmentsPrivate"] is True
        print("PASS Adaptive Cards and capability DOCX/TXT input")
        await call("POST", f"/api/conversations/{pconv}/messages", status=201, json={"text": "widget please"})
        messages = await call("GET", f"/api/conversations/{pconv}/messages")
        widget_message = next(message for message in messages if message["textFormat"] == "extendedmarkdown")
        payload = json.loads(widget_message["text"].split("```html-widget\n")[1].split("\n```")[0])
        token = re.search(r'"discuss": "([^"]+)"', payload["html"]).group(1)
        submitted = {"name": "autopilot.widget.submit", "arguments": {
            "token": token, "values": {"budget": "7"}, "__msteams__": {"correlationId": "spoofed"},
        }}
        decoded, values = decode_submission(submitted, conversation_id=pconv)
        assert decoded["sessionKey"] == invocations[-1]["session_key"] and values == {"budget": "7"}
        widget_path = f"/api/messages/{widget_message['id']}/widgets/0"
        before = len(invocations)
        response = await call("POST", widget_path + "/tools", json=submitted)
        assert response["responseType"] == "htmlwidget/calltoolresult"
        assert response["callToolResult"]["structuredContent"]["status"] == "accepted"
        assert response["callToolResult"]["isError"] is False
        observed = sdk_invokes[-1]
        assert observed["type"] == "invoke" and observed["name"] == "htmlwidget/calltool"
        assert observed["conversation"]["id"] == pconv
        assert observed["conversation"]["conversationType"] == "personal"
        assert observed["recipient"]["id"] == f"28:{AGENT}"
        assert observed["from"]["aadObjectId"] == decoded["userId"]
        assert observed["value"]["arguments"]["__msteams__"] == {}
        assert decode_submission(observed["value"], conversation_id=pconv)[1] == {"budget": "7"}
        for _ in range(100):
            followup = await call("GET", f"/api/conversations/{pconv}/messages")
            if any(message["text"].startswith("Plan received") for message in followup):
                break
            await asyncio.sleep(.03)
        else:
            raise AssertionError("Widget proactive continuation did not arrive")
        assert len(invocations) == before + 1
        assert invocations[-1]["session_key"] == decoded["sessionKey"]
        duplicate = await call("POST", widget_path + "/tools", json=submitted)
        assert duplicate["callToolResult"]["structuredContent"]["status"] == "already_submitted"
        expired = {**decoded, "expiresAtUnix": time.time() - 1}
        expired_token = _cipher().encrypt(json.dumps(expired).encode()).decode()
        rejected = await call("POST", widget_path + "/tools", json={
            "name": submitted["name"], "arguments": {"token": expired_token, "values": {}},
        })
        assert rejected["callToolResult"]["isError"] is True
        print("PASS real render_widget -> Connector extendedmarkdown -> simulator deliver -> real SDK HTTP invokeResponse")
        print("PASS valid Fernet binding, real handle_teams_invoke/decode_submission/tool_result, duplicate and expired capability, same-session proactive follow-up")
        if args.widgets_browser:
            from widget_browser_checks import check_widgets

            async def post_widget(activity):
                saved_widget = Conversation.from_json_to_store_item(references[-1]["conversation"])
                sent = []

                async def send_widget(context):
                    sent.append((await context.send_activity(activity)).id)

                await bridge.agent365_adapter.continue_conversation_with_claims(
                    saved_widget.identity_from_claims(saved_widget.claims),
                    saved_widget.conversation_reference.get_continuation_activity(), send_widget,
                )
                return sent[0]

            await check_widgets(
                simulator=simulator, client=client, call=call, sim_url=sim_url, conversation=pconv,
                owner_headers=owner_headers, spec=widget_spec, post_widget=post_widget, render_widget=render_widget,
                cipher=_cipher(), session_key=decoded["sessionKey"], user_id=decoded["userId"],
                media_dir=args.media_dir, demo=demo, access_code=settings.demo_access_code,
                request_log=http_requests,
            )
        await send_proactive_activity(bridge.agent365_adapter, references[-1], "Proactive smoke")
        without_claims = json.loads(json.dumps(references[-1]))
        without_claims["conversation"]["claims"] = {}
        await send_proactive_activity(bridge.agent365_adapter, without_claims, "Proactive app-ID fallback")
        saved = Conversation.from_json_to_store_item(references[-1]["conversation"])
        continuation = saved.conversation_reference.get_continuation_activity()
        uploaded_ids = []

        async def pending_choices(scope):
            return {"operations": [{"operationId": "a" * 24, "fileName": "smoke.docx",
                                    "expiresAt": "later", "expiresAtUnix": time.time() + 300}]}

        async def connector_operations(context):
            sent = await send_file_attachment(context, name="result.docx", content_type=DOCX_CONTENT_TYPE,
                                             data=document.getvalue(), text="Output file")
            uploaded_ids.append(sent.id)
            card = await original_document_card(adapter=SimpleNamespace(pending_document_choices=pending_choices),
                                                conversation_id=pconv, invoking_user_id=invocations[0]["user_id"])
            await context.send_activity(card)
            sent = await context.send_activity("temporary SDK message")
            await context.update_activity(Activity(type="message", id=sent.id, text="updated SDK message"))
            reference = context.activity.get_conversation_reference()
            reference.activity_id = sent.id
            await context.delete_activity(reference)

        await bridge.agent365_adapter.continue_conversation_with_claims(
            saved.identity_from_claims(saved.claims), continuation, connector_operations,
        )
        messages = await call("GET", f"/api/conversations/{pconv}/messages")
        assert any(message["text"] == "Proactive smoke" for message in messages)
        assert any(message["text"] == "Proactive app-ID fallback" for message in messages)
        assert any((message.get("suggestedActions") or {}).get("actions") for message in messages)
        output = next(message for message in messages if message["id"] == uploaded_ids[0])
        returned = await client.get(output["attachments"][0]["downloadUrl"], headers=owner_headers)
        assert returned.status_code == 200 and returned.content == document.getvalue()
        assert any(event["type"] == "message.updated" for event in events)
        assert any(event["type"] == "message.deleted" for event in events)
        assert requests
        if demo:
            assert not token_scopes
            assert all(not authenticated and path.startswith("/demo/" + demo_key + "/v3/") for path, authenticated in requests)
        else:
            assert token_scopes and all(scopes == [CHANNEL + "/.default"] for scopes in token_scopes)
        if demo:
            headers = {"X-Demo-Channel-Key": demo_key}
            probe = {"type": "message", "serviceUrl": sim_url + "/demo/" + demo_key, "text": "forbidden"}
            for supplied, service_url, expected in (({}, probe["serviceUrl"], 401),
                                                   (headers, sim_url, 403),
                                                   ({"X-Demo-Channel-Key": "wrong"}, probe["serviceUrl"], 401)):
                response = await client.post(bridge_url + "/api/messages", headers=supplied, json={**probe, "serviceUrl": service_url})
                assert response.status_code == expected
        assert (directory / "backup" / "bridge.db").exists()
        session_key = next(iter(bridge._teams_memory))
        retained = list(bridge.teams_memory(session_key))
        bridge._teams_memory.clear()
        bridge.durable_transport.journal.cache_clear()
        (directory / "bridge.db").unlink()
        assert list(bridge.teams_memory(session_key)) == retained
        denied = await client.get(bridge_url + "/internal/transport/status")
        status = await client.get(bridge_url + "/internal/transport/status",
                                  headers={"X-Autopilot-Key": os.environ["API_SERVER_KEY"]})
        assert denied.status_code == 401 and status.status_code == 200
        assert status.json()["active"] == 0 and status.json()["enabled"] is False
        print("PASS Connector DOCX output/download, existing document-choice card, proactive, update/delete")
        print("PASS transport-independent conversation restore and authenticated quiescence status")
        print(json.dumps({"result": "passed", "mode": args.mode, "runtime": "explicit stub Hermes",
                          "auth": "DEMO over trusted temporary TLS" if demo else "local RSA Test IdP; synthetic token acquisition; not real Entra/FIC",
                          "runtimeInvocations": len(invocations), "connectorRequests": len(requests),
                          "connectorTokens": len(token_scopes), "events": sorted({event["type"] for event in events})}, indent=2))


async def entrypoint():
    try:
        await main()
    finally:
        for server in servers:
            server.should_exit = True
        try:
            if tasks:
                await asyncio.wait_for(asyncio.gather(*tasks), timeout=15)
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    asyncio.run(entrypoint())
