"""Synthetic, isolated protocol/JWKS tests; not real Entra or SDK conformance evidence."""

import asyncio
import base64
import copy
import hashlib
import json
import socket
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, quote, urlparse
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import jwt
import pytest
import uvicorn
from azure.core.credentials import AccessToken
from azure.core.exceptions import ClientAuthenticationError
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.testclient import TestClient
from microsoft_agents.activity import Activity
from sqlalchemy import select

from backend import db, domain as d
from backend.app import create_app
from backend.channel import ADAPTIVE_CARD, FILE_DOWNLOAD, ChannelAuth
from backend.config import Settings, TEST_PERSONAS
from tests.test_realtime import authenticate, connect


CHANNEL_ID = "10000000-0000-4000-8000-000000000001"
AGENT_A = "20000000-0000-4000-8000-000000000001"
AGENT_B = "20000000-0000-4000-8000-000000000002"
SUBMIT = {"type": "Action.Submit", "title": "Submit", "data": {"choice": "yes"}}
EXECUTE = {"type": "Action.Execute", "title": "Refresh", "verb": "test.refresh", "data": {"operation": "refresh"}}
CARD = {"type": "AdaptiveCard", "version": "1.5",
        "body": [{"type": "Input.Text", "id": "answer"}, {"type": "TextBlock", "text": "A test card"}],
        "actions": [SUBMIT, EXECUTE]}
REFRESHED = {"type": "AdaptiveCard", "version": "1.5",
             "body": [{"type": "TextBlock", "text": "Refreshed"}]}


def bearer(issuer, audience, caller, **overrides):
    claims = {"aud": audience, "azp": caller, "scp": None, "idtyp": "app", **overrides}
    return {"Authorization": f"Bearer {issuer.mint(overrides=claims)}"}


@pytest.fixture
def app(settings, issuer):
    configured = replace(settings, channel_client_id=CHANNEL_ID, channel_client_secret="synthetic-test-only")
    app = create_app(configured)
    state = {"activities": [], "tokenScopes": [], "deliveryStatus": 202,
             "invokeResponse": {"statusCode": 200, "type": ADAPTIVE_CARD, "value": REFRESHED}}

    async def token_request(request):
        assert str(request.url) == f"https://login.microsoftonline.com/{configured.tenant_id}/oauth2/v2.0/token"
        form = parse_qs(request.content.decode())
        assert form["client_id"] == [CHANNEL_ID]
        assert form["grant_type"] == ["client_credentials"]
        scope = form["scope"][0]
        state["tokenScopes"].append(scope)
        audience = scope.removesuffix("/.default")
        claims = {"aud": audience, "azp": CHANNEL_ID, "scp": None, "idtyp": "app",
                  **state.get("tokenOverrides", {})}
        return httpx.Response(state.get("tokenStatus", 200), json={
            "token_type": "Bearer", "access_token": issuer.mint(overrides=claims),
        })

    async def agent_request(request):
        assert str(request.url) in {"http://127.0.0.1:9101/api/messages", "http://127.0.0.1:9102/api/messages"}
        audience = AGENT_A if request.url.port == 9101 else AGENT_B
        claims = jwt.decode(request.headers["authorization"].partition(" ")[2], issuer.key.public_key(),
                            algorithms=["RS256"], issuer=issuer.issuer, audience=audience)
        assert claims["azp"] == CHANNEL_ID
        assert claims["tid"] == configured.tenant_id
        activity = json.loads(request.content)
        Activity.model_validate(activity)
        state["activities"].append(activity)
        if "onActivity" in state:
            await state["onActivity"](activity)
        if activity["type"] == "invoke":
            return httpx.Response(state.get("invokeStatus", 200), json=state["invokeResponse"])
        return httpx.Response(state["deliveryStatus"])

    # Replacing transport is an explicit local network fixture, never a verification bypass.
    app.state.channel.auth.client = httpx.AsyncClient(transport=httpx.MockTransport(token_request), trust_env=False)
    app.state.channel.client = httpx.AsyncClient(transport=httpx.MockTransport(agent_request), trust_env=False)
    app.state.protocol_fixture = state
    return app


@pytest.fixture
def client(app):
    with TestClient(app) as value:
        yield value


@pytest.fixture
def agents(client, app, accounts, issuer):
    result = {}
    for name, client_id, port in [("A", AGENT_A, 9101), ("B", AGENT_B, 9102)]:
        response = client.post("/api/agents", headers=accounts["owner"]["headers"], json={
            "name": f"Agent {name}", "clientId": client_id, "endpointUrl": f"http://127.0.0.1:{port}/api/messages",
        })
        assert response.status_code == 201
        result[name] = {**response.json(), "headers": bearer(issuer, CHANNEL_ID, client_id)}
    return result


@pytest.fixture
def installed(client, app, accounts, resources, agents):
    response = client.post(f"/api/teams/{resources['team']['id']}/members",
                           headers=accounts["owner"]["headers"], json={"userId": agents["A"]["userId"]})
    assert response.status_code == 200
    return {**resources, "agent": agents["A"], "otherAgent": agents["B"]}


def connector_message(client, installed, text="Agent reply", attachments=None):
    response = client.post(
        f"/v3/conversations/{installed['conversation']}/activities/{installed['message']['id']}",
        headers=installed["agent"]["headers"],
        json={"type": "message", "text": text, "attachments": attachments or []},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_registry_admin_directory_metadata_and_no_credential_redisclosure(client, app, accounts, agents):
    owner, member = accounts["owner"]["headers"], accounts["member"]["headers"]
    assert client.get("/api/agents", headers=member).status_code == 403
    assert client.post("/api/agents", headers=member, json={
        "name": "Forbidden", "clientId": AGENT_A, "endpointUrl": "https://example.test/api/messages",
    }).status_code == 403
    agent = agents["A"]
    assert set(client.get(f"/api/agents/{agent['id']}", headers=owner).json()) == {
        "id", "userId", "name", "clientId", "endpointUrl", "enabled",
    }
    assert "synthetic-test-only" not in client.get("/api/config").text
    assert "synthetic-test-only" not in repr(app.state.settings)
    directory = next(u for u in client.get("/api/users", headers=member).json() if u["id"] == agent["userId"])
    assert directory["isAgent"] is True and directory["agentId"] == agent["id"]
    assert not {"endpointUrl", "clientId", "clientSecret", "secret"} & directory.keys()
    assert client.post("/api/agents", headers=owner, json={
        "name": "Duplicate", "clientId": AGENT_A, "endpointUrl": "http://127.0.0.1:9101/api/messages",
    }).status_code == 409
    assert client.post("/api/agents", headers=owner, json={
        "name": "No secret storage", "clientId": AGENT_A, "endpointUrl": "http://127.0.0.1:9101/api/messages",
        "clientSecret": "synthetic-test-only",
    }).status_code == 422
    assert client.patch(f"/api/agents/{agent['id']}", headers=member, json={"enabled": False}).status_code == 403
    assert client.patch(f"/api/agents/{agent['id']}", headers=owner, json={"name": "Renamed"}).json()["name"] == "Renamed"
    for body in ({}, {"name": None}, {"name": "   "}, {"endpointUrl": "http://evil.invalid/api/messages"}):
        assert client.patch(f"/api/agents/{agent['id']}", headers=owner, json=body).status_code == 422
    assert client.patch(f"/api/agents/{agent['id']}", headers=owner, json={"enabled": False}).status_code == 200
    assert client.get("/v3/conversations/unknown/members", headers=agent["headers"]).status_code == 401


@pytest.mark.parametrize("test_idp", [False, True], ids=["ordinary-auth", "explicit-test-auth"])
@pytest.mark.parametrize("configured_channel", [False, True], ids=["unconfigured-channel", "distinct-channel"])
def test_public_agent_channel_discovery_preserves_spa_auth_and_excludes_credentials(
    settings, test_idp, configured_channel
):
    private_value = "synthetic-private-discovery-only"
    configured = replace(settings, test_idp=test_idp,
                         channel_client_id=CHANNEL_ID if configured_channel else "",
                         channel_client_secret=private_value)
    with TestClient(create_app(configured)) as api:
        response = api.get("/api/config")
        assert response.status_code == 200
        value = response.json()
        assert value["agentChannel"] == {
            "clientId": configured.channel_client_id, "serviceUrl": configured.service_url,
            "tenantId": configured.tenant_id,
        }
        assert {key: value[key] for key in ("tenantId", "clientId", "scope", "redirectUri", "channelId")} == {
            "tenantId": settings.tenant_id, "clientId": settings.client_id, "scope": settings.scope,
            "redirectUri": settings.redirect_uri, "channelId": settings.channel_id,
        }
        assert value["agentChannel"]["clientId"] != value["clientId"]
        if test_idp:
            assert value["testAuth"] == {
                "issuer": configured.test_issuer, "tokenEndpoint": f"{configured.test_issuer}/token",
                "personas": TEST_PERSONAS,
            }
        else:
            assert "testAuth" not in value
        assert value["authMode"] == "entra"
        assert set(value) == {
            "authMode", "tenantId", "clientId", "scope", "redirectUri", "channelId", "agentChannel",
        } | ({"testAuth"} if test_idp else set())
        assert set(value["agentChannel"]) == {"clientId", "serviceUrl", "tenantId"}
        assert private_value not in response.text
        assert not {"clientSecret", "secret", "password", "token"} & value["agentChannel"].keys()


@pytest.mark.parametrize("endpoint", [
    "http://example.test/api/messages", "http://127.0.0.1.evil.test/api/messages",
    "https://user:password@example.test/api/messages", "https://example.test/api/messages?target=other",
    "https://example.test/api/messages#fragment", "file:///agent.py", "http://127.0.0.1:99999/api/messages",
    "https://example.test\\evil/api/messages",
])
def test_insecure_registration_endpoints_rejected(client, accounts, endpoint):
    assert client.post("/api/agents", headers=accounts["owner"]["headers"], json={
        "name": "Unsafe", "clientId": AGENT_A, "endpointUrl": endpoint,
    }).status_code == 422


def test_production_channel_and_origin_guards(settings):
    production = replace(settings, test_idp=False, environment="production",
                         redirect_uri="https://simulator.example.test/",
                         allowed_origins=("https://simulator.example.test",))
    with pytest.raises(ValueError, match="HTTPS"):
        replace(production, channel_client_id=CHANNEL_ID)
    valid = replace(production, channel_client_id=CHANNEL_ID, service_url="https://simulator.example.test")
    assert valid.service_url.startswith("https://")
    for service_url in ("https://user@example.test", "https://example.test/v3",
                        "https://example.test?redirect=other", "https://localhost"):
        with pytest.raises(ValueError):
            replace(valid, service_url=service_url)
    for origin in ("https://user@example.test", "https://example.test/path", "https://localhost"):
        with pytest.raises(ValueError):
            replace(valid, allowed_origins=(origin,))
    with pytest.raises(ValueError, match="separate"):
        replace(settings, channel_client_id=settings.client_id)


def test_managed_identity_requires_an_unambiguous_channel_identity(settings, monkeypatch):
    identity = "30000000-0000-4000-8000-000000000003"
    with pytest.raises(ValueError, match="Managed identity"):
        replace(settings, channel_managed_identity_client_id=identity)
    with pytest.raises(ValueError, match="Managed identity"):
        replace(settings, channel_client_id=CHANNEL_ID, channel_client_secret="synthetic",
                channel_managed_identity_client_id=identity)
    monkeypatch.setenv("TSIM_CHANNEL_CLIENT_ID", CHANNEL_ID)
    monkeypatch.setenv("TSIM_CHANNEL_MANAGED_IDENTITY_CLIENT_ID", identity)
    monkeypatch.delenv("TSIM_CHANNEL_CLIENT_SECRET", raising=False)
    assert Settings.from_env().channel_managed_identity_client_id == identity


@pytest.mark.parametrize("failure", [None, "acquisition", "caller", "audience"])
def test_federated_channel_exchanges_assertion_and_validates_app_token(settings, issuer, monkeypatch, failure):
    identity_id = "30000000-0000-4000-8000-000000000003"
    identity = Mock()
    identity.get_token.return_value = AccessToken("synthetic-exchange-assertion", int(time.time()) + 3600)
    managed_factory = Mock(return_value=identity)
    credential = Mock()

    def create_federated(tenant, client_id, assertion, **kwargs):
        assert (tenant, client_id) == (settings.tenant_id, CHANNEL_ID)

        def acquire(scope):
            assert assertion() == "synthetic-exchange-assertion"
            if failure == "acquisition":
                raise ClientAuthenticationError("Synthetic failed exchange")
            claims = {"aud": scope.removesuffix("/.default"), "azp": CHANNEL_ID,
                      "idtyp": "app", "scp": None}
            if failure == "caller":
                claims["azp"] = identity_id
            elif failure == "audience":
                claims["aud"] = CHANNEL_ID
            return AccessToken(issuer.mint(overrides=claims), int(time.time()) + 3600)

        credential.get_token.side_effect = acquire
        return credential

    monkeypatch.setattr("backend.channel.ManagedIdentityCredential", managed_factory)
    monkeypatch.setattr("backend.channel.ClientAssertionCredential", create_federated)
    verifier = SimpleNamespace(_key=AsyncMock(return_value=issuer.key.public_key()))
    configured = replace(settings, channel_client_id=CHANNEL_ID, channel_managed_identity_client_id=identity_id)

    async def exercise():
        auth = ChannelAuth(configured, verifier)
        async def reject_secret_transport(request):
            raise AssertionError("Federation must not fall back to client-secret transport")
        await auth.client.aclose()
        auth.client = httpx.AsyncClient(transport=httpx.MockTransport(reject_secret_transport))
        try:
            if failure:
                with pytest.raises(HTTPException) as error:
                    await auth.token(AGENT_A)
                assert error.value.status_code == 502
                assert not auth._tokens
            else:
                first = await auth.token(AGENT_A)
                assert await auth.token(AGENT_A) == first
                assert await auth.token(AGENT_B) != first
                assert credential.get_token.call_count == 2
                assert [call.args for call in credential.get_token.call_args_list] == [
                    (f"{AGENT_A}/.default",), (f"{AGENT_B}/.default",),
                ]
        finally:
            await auth.close()

    asyncio.run(exercise())
    assert managed_factory.call_args.kwargs["client_id"] == identity_id
    identity.get_token.assert_called_with("api://AzureADTokenExchange/.default")
    credential.close.assert_called_once()
    identity.close.assert_called_once()


@pytest.mark.parametrize("overrides", [
    {"exp": 1}, {"nbf": int(time.time()) + 3600}, {"aud": AGENT_A}, {"aud": [CHANNEL_ID]},
    {"tid": "other-tenant"}, {"iss": "https://attacker.invalid"}, {"azp": "not-a-client-id"},
    {"azp": "30000000-0000-4000-8000-000000000003"}, {"scp": "access_as_user"},
    {"idtyp": "user"}, {"exp": True}, {"iat": "0"}, {"nbf": None},
])
def test_connector_strict_application_identity_rejections(client, issuer, installed, overrides):
    headers = bearer(issuer, CHANNEL_ID, AGENT_A, **overrides)
    response = client.get(f"/v3/conversations/{installed['conversation']}/members", headers=headers)
    assert response.status_code == 401


def test_connector_missing_forged_and_user_tokens_and_foreign_membership(client, issuer, accounts, installed):
    route = f"/v3/conversations/{installed['conversation']}/members"
    for headers in ({}, {"Authorization": "Bearer not-a-jwt"}, accounts["owner"]["headers"],
                    {"Authorization": "Bearer synthetic-test-only"}):
        assert client.get(route, headers=headers).status_code == 401
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = issuer.mint(key=wrong_key, overrides={"aud": CHANNEL_ID, "azp": AGENT_A, "scp": None})
    assert client.get(route, headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    assert client.get(route, headers=installed["otherAgent"]["headers"]).status_code == 403
    assert client.get("/api/me", headers=installed["agent"]["headers"]).status_code == 401


def test_installation_channel_thread_mentions_do_not_infer_targeted_private_delivery(client, app, accounts, installed):
    state = app.state.protocol_fixture
    installation = state["activities"][0]
    agent = installed["agent"]
    bot_id = f"28:{AGENT_A}"
    assert installation["type"] == "conversationUpdate"
    assert installation["membersAdded"] == [{"id": bot_id, "name": "Agent A"}]
    assert installation["recipient"] == {"id": bot_id, "name": "Agent A", "isTargeted": False}
    assert installation["channelData"]["eventType"] == "teamMemberAdded"
    assert installation["conversation"]["id"] == installed["conversation"]
    assert installation["serviceUrl"] == app.state.settings.service_url
    assert installation["channelId"] == "msteams" and installation["locale"] == "en-US"
    # Repeated addition is idempotent and does not deliver a second installation.
    assert client.post(f"/api/teams/{installed['team']['id']}/members", headers=accounts["owner"]["headers"],
                       json={"userId": agent["userId"]}).status_code == 200
    assert len(state["activities"]) == 1
    root = client.post(f"/api/conversations/{installed['conversation']}/messages",
                      headers=accounts["owner"]["headers"], json={"text": "Unmentioned root"}).json()
    assert state["activities"][-1]["recipient"]["isTargeted"] is False
    assert state["activities"][-1]["conversation"]["id"] == f"{installed['conversation']};messageid={root['id']}"
    response = client.post(f"/api/conversations/{installed['conversation']}/messages",
                           headers=accounts["owner"]["headers"], json={
                               "text": "Hello <at>Spoof</at>", "replyToId": root["id"],
                               "mentions": [{"userId": agent["userId"], "displayName": "Spoof"}],
                           })
    assert response.status_code == 201
    activity = state["activities"][-1]
    assert activity["recipient"]["isTargeted"] is False
    assert activity["conversation"]["id"] == f"{installed['conversation']};messageid={root['id']}"
    assert activity["replyToId"] == root["id"]
    assert activity["entities"] == [{"type": "mention", "text": "<at>Agent A</at>",
                                    "mentioned": {"id": bot_id, "name": "Agent A"}}]
    assert activity["channelData"] == {
        "tenant": {"id": app.state.settings.tenant_id},
        "team": {"id": installed["team"]["id"]}, "channel": {"id": installed["team"]["channels"][0]["id"]},
    }
    assert state["tokenScopes"] == [f"{AGENT_A}/.default"]
    new_channel = client.post(f"/api/teams/{installed['team']['id']}/channels",
                              headers=accounts["owner"]["headers"], json={"name": "Agent room"})
    assert new_channel.status_code == 201
    assert state["activities"][-1]["type"] == "conversationUpdate"
    assert state["activities"][-1]["conversation"]["id"] == new_channel.json()["conversationId"]


@pytest.mark.parametrize("group", [False, True])
def test_directory_agent_chat_installation_and_messages(client, app, accounts, agents, group):
    body = {"memberIds": [agents["A"]["userId"]]}
    if group:
        body.update({"name": "Agents group", "memberIds": [agents["A"]["userId"], accounts["member"]["user"]["id"]]})
    response = client.post("/api/chats", headers=accounts["owner"]["headers"], json=body)
    assert response.status_code == 201
    chat = response.json()
    assert chat["conversationType"] == ("groupChat" if group else "personal")
    state = app.state.protocol_fixture
    assert state["activities"][-1]["type"] == "conversationUpdate"
    assert len(state["activities"][-1]["membersAdded"]) == (3 if group else 2)
    assert client.post(f"/api/conversations/{chat['conversationId']}/messages",
                       headers=accounts["owner"]["headers"], json={"text": "Hello agent"}).status_code == 201
    activity = state["activities"][-1]
    assert activity["conversation"]["id"] == chat["conversationId"]
    assert activity["conversation"]["isGroup"] is group
    assert activity["recipient"]["isTargeted"] is False
    members = client.get(f"/v3/conversations/{chat['conversationId']}/members", headers=agents["A"]["headers"])
    assert members.status_code == 200
    assert {m["id"] for m in members.json()} == {
        f"28:{AGENT_A}", accounts["owner"]["user"]["id"],
        *([accounts["member"]["user"]["id"]] if group else []),
    }


def test_ordinary_personal_message_submit_and_invoke_are_not_targeted_private(client, app, accounts, agents):
    owner = accounts["owner"]["headers"]
    response = client.post("/api/chats", headers=owner, json={"memberIds": [agents["A"]["userId"]]})
    assert response.status_code == 201
    cid = response.json()["conversationId"]
    response = client.post(f"/api/conversations/{cid}/messages", headers=owner, json={"text": "Normal personal turn"})
    assert response.status_code == 201
    activity = app.state.protocol_fixture["activities"][-1]
    assert activity["type"] == "message" and activity["conversation"]["conversationType"] == "personal"
    assert activity["recipient"] == {"id": f"28:{AGENT_A}", "name": "Agent A", "isTargeted": False}
    response = client.post(f"/v3/conversations/{cid}/activities", headers=agents["A"]["headers"],
                           json={"type": "message", "attachments": [{"contentType": ADAPTIVE_CARD, "content": CARD}]})
    assert response.status_code == 200
    mid = response.json()["id"]
    for action, kind in ((SUBMIT, "message"), (EXECUTE, "invoke")):
        response = client.post(f"/api/messages/{mid}/actions", headers=owner, json={"action": action})
        assert response.status_code == 200
        activity = app.state.protocol_fixture["activities"][-1]
        assert activity["type"] == kind and activity["conversation"]["conversationType"] == "personal"
        assert activity["recipient"]["isTargeted"] is False


def test_explicit_protocol_targeted_private_marker_is_preserved_only_for_its_recipient(
    client, app, accounts, agents
):
    chat = client.post("/api/chats", headers=accounts["owner"]["headers"],
                       json={"memberIds": [agents["A"]["userId"]]}).json()
    with app.state.sessions() as session:
        conversation = session.get(db.Conversation, chat["conversationId"])
        user = session.get(db.User, accounts["owner"]["user"]["id"])
        agent = session.get(db.Agent, agents["A"]["id"])
        activity = d.make_activity(session, app.state.settings, conversation, user, db.new_id(), "Explicit", "plain")
        assert app.state.channel.envelope(session, conversation, user, agent, activity)["recipient"]["isTargeted"] is False
        activity["recipient"] = {"id": f"28:{AGENT_A}", "isTargeted": True}
        assert app.state.channel.envelope(session, conversation, user, agent, activity)["recipient"]["isTargeted"] is True
        activity["recipient"]["id"] = f"28:{AGENT_B}"
        assert app.state.channel.envelope(session, conversation, user, agent, activity)["recipient"]["isTargeted"] is False


def test_connector_typing_reply_update_delete_and_ws_isolation(client, app, issuer, accounts, installed):
    cid, agent_headers = installed["conversation"], installed["agent"]["headers"]
    thread_id = f"{cid};messageid={installed['message']['id']}"
    route = f"/v3/conversations/{thread_id}/activities"
    with connect(client, issuer, "member") as member_socket, connect(client, issuer, "outsider") as outsider_socket:
        authenticate(member_socket, issuer, "member")
        authenticate(outsider_socket, issuer, "outsider")
        started = time.time()
        response = client.post(route, headers=agent_headers, json={"type": "typing"})
        assert response.status_code == 200
        typing = member_socket.receive_json()
        assert typing["type"] == "typing" and typing["actor"]["isAgent"] is True
        assert typing["conversationId"] == cid and typing["replyToId"] == installed["message"]["id"]
        assert isinstance(typing["expiresAt"], str) and typing["expiresAt"].endswith("Z")
        expires_at = datetime.fromisoformat(typing["expiresAt"])
        assert datetime.fromtimestamp(started, timezone.utc) < expires_at <= (
            datetime.now(timezone.utc) + timedelta(seconds=5)
        )
        mid = connector_message(client, installed)
        created = member_socket.receive_json()
        assert created["type"] == "message.created" and created["message"]["id"] == mid
        assert created["message"]["author"]["id"] == installed["agent"]["userId"]
        assert created["message"]["author"]["agentId"] == installed["agent"]["id"]
        updated = client.put(f"{route}/{mid}", headers=agent_headers, json={"type": "message", "text": "Changed"})
        assert updated.status_code == 200 and updated.json() == {"id": mid}
        assert member_socket.receive_json()["message"]["text"] == "Changed"
        assert client.delete(f"{route}/{mid}", headers=agent_headers).status_code == 204
        deleted = member_socket.receive_json()
        assert deleted["type"] == "message.deleted" and deleted["message"]["deleted"] is True
        assert client.put(f"{route}/{mid}", headers=agent_headers,
                          json={"type": "message", "text": "Resurrect"}).status_code == 409
        marker = client.post("/api/teams", headers=accounts["outsider"]["headers"],
                             json={"name": "Outsider marker"}).json()
        assert outsider_socket.receive_json() == {"type": "membership.changed", "teamId": marker["id"]}
    exported = client.get("/api/activities/export", headers=accounts["owner"]["headers"]).json()
    revisions = [a for a in exported if a["id"] == mid]
    assert [a["type"] for a in revisions] == ["message", "messageUpdate", "messageDelete"]
    assert all(a["conversation"]["id"] == thread_id for a in revisions)
    assert all(Activity.model_validate(a) for a in exported)


def test_agent_cannot_change_human_or_foreign_thread_messages(client, accounts, installed):
    cid, headers = installed["conversation"], installed["agent"]["headers"]
    root = installed["message"]["id"]
    assert client.put(f"/v3/conversations/{cid}/activities/{root}", headers=headers,
                      json={"type": "message", "text": "Stolen"}).status_code == 403
    assert client.delete(f"/v3/conversations/{cid}/activities/{root}", headers=headers).status_code == 403
    own = connector_message(client, installed)
    another = client.post(f"/api/conversations/{cid}/messages", headers=accounts["owner"]["headers"],
                          json={"text": "Different root"}).json()["id"]
    foreign_thread = f"/v3/conversations/{cid};messageid={another}/activities/{own}"
    assert client.put(foreign_thread, headers=headers, json={"type": "message", "text": "Wrong thread"}).status_code == 403
    assert client.delete(foreign_thread, headers=headers).status_code == 403
    assert client.put(foreign_thread + "/reactions/like", headers=headers).status_code == 403
    assert client.post(f"/v3/conversations/{cid};messageid={another}/activities/{root}", headers=headers,
                       json={"type": "message", "text": "Wrong reply"}).status_code == 403
    assert client.post(f"/v3/conversations/{cid}/activities", headers=headers,
                       json={"type": "message", "text": "Spoof", "conversation": {"id": "foreign"}}).status_code == 403
    assert client.post(f"/v3/conversations/{cid}/activities", headers=headers,
                       json={"type": "event", "name": "unsupported"}).status_code == 422


@pytest.mark.parametrize("kind", ["personal", "groupChat", "channel"])
def test_typing_resource_response_updates_preserve_identity_and_realtime_contract(
    client, app, issuer, accounts, installed, kind
):
    owner = accounts["owner"]["headers"]
    agent_headers = installed["agent"]["headers"]
    if kind == "channel":
        cid, root = installed["conversation"], installed["message"]["id"]
        conversation_route = f"{cid};messageid={root}"
    else:
        members = [installed["agent"]["userId"]]
        if kind == "groupChat":
            members.append(accounts["member"]["user"]["id"])
        response = client.post("/api/chats", headers=owner, json={"name": "Native typing group", "memberIds": members})
        assert response.status_code == 201, response.text
        chat = response.json()
        cid = chat["conversationId"]
        root = client.post(f"/api/conversations/{cid}/messages", headers=owner,
                           json={"text": "Native typing then update"}).json()["id"]
        conversation_route = cid
    route = f"/v3/conversations/{conversation_route}/activities"
    baseline = {m["id"] for m in client.get(f"/api/conversations/{cid}/messages", headers=owner).json()}
    with connect(client, issuer, "owner") as socket:
        authenticate(socket, issuer, "owner")
        started = time.time()
        response = client.post(f"{route}/{root}", headers=agent_headers,
                               json={"type": "typing", "text": "Working on the response"})
        assert response.status_code == 200
        handle = response.json()["id"]
        typing = socket.receive_json()
        assert typing["type"] == "typing" and typing["conversationId"] == cid
        assert typing["replyToId"] == (root if kind == "channel" else None)
        assert typing["actor"]["id"] == installed["agent"]["userId"]
        assert typing["actor"]["isAgent"] is True and typing["actor"]["agentId"] == installed["agent"]["id"]
        assert isinstance(typing["expiresAt"], str) and typing["expiresAt"].endswith("Z")
        assert started < datetime.fromisoformat(typing["expiresAt"]).timestamp() <= time.time() + 5
        hidden = client.get(f"/api/messages/{handle}", headers=owner).json()
        assert hidden["isHidden"] is True and hidden["text"] == ""
        assert {m["id"] for m in client.get(f"/api/conversations/{cid}/messages", headers=owner).json()} == baseline
        with app.state.sessions() as session:
            placeholder = d.require_message(session, handle, installed["agent"]["userId"], own=True)
            assert placeholder.activity["id"] == handle and placeholder.activity["type"] == "typing"
        response = client.put(f"{route}/{handle}", headers=agent_headers,
                              json={"type": "message", "id": handle, "text": "First actual message"})
        assert response.status_code == 200 and response.json() == {"id": handle}
        created = socket.receive_json()
        assert created["type"] == "message.created" and created["message"]["id"] == handle
        assert created["message"]["text"] == "First actual message"
        response = client.put(f"{route}/{handle}", headers=agent_headers,
                              json={"type": "message", "id": handle, "text": "Final actual message"})
        assert response.status_code == 200 and response.json() == {"id": handle}
        updated = socket.receive_json()
        assert updated["type"] == "message.updated" and updated["message"]["text"] == "Final actual message"
        assert client.delete(f"{route}/{handle}", headers=agent_headers).status_code == 204
        assert socket.receive_json()["type"] == "message.deleted"


def test_typing_handles_reject_unowned_missing_and_revoked_updates_and_delete_invisibly(
    client, accounts, installed
):
    cid, headers = installed["conversation"], installed["agent"]["headers"]
    route = f"/v3/conversations/{cid}/activities"
    response = client.post(route, headers=headers, json={"type": "typing"})
    assert response.status_code == 200
    handle = response.json()["id"]
    body = {"type": "message", "text": "Authorized completion only"}
    assert client.put(f"{route}/{handle}", json=body).status_code == 401
    assert client.put(f"{route}/{handle}", headers=installed["otherAgent"]["headers"], json=body).status_code == 403
    assert client.put(f"{route}/{installed['message']['id']}", headers=headers, json=body).status_code == 403
    assert client.put(f"{route}/{db.new_id()}", headers=headers, json=body).status_code == 404
    assert client.delete(f"{route}/{handle}", headers=headers).status_code == 204
    hidden = client.get(f"/api/messages/{handle}", headers=accounts["owner"]["headers"]).json()
    assert hidden["deleted"] is True and hidden["isHidden"] is True
    assert handle not in {m["id"] for m in client.get(
        f"/api/conversations/{cid}/messages", headers=accounts["owner"]["headers"]
    ).json()}
    assert client.put(f"{route}/{handle}", headers=headers, json=body).status_code == 409
    another = client.post(route, headers=headers, json={"type": "typing"}).json()["id"]
    assert client.delete(f"/api/teams/{installed['team']['id']}/members/{installed['agent']['userId']}",
                         headers=accounts["owner"]["headers"]).status_code == 204
    assert client.put(f"{route}/{another}", headers=headers, json=body).status_code == 403


def test_frozen_bridge_eyes_reaction_id_add_delete_and_textual_rejection(client, accounts, installed):
    root = installed["message"]["id"]
    route = f"/v3/conversations/{installed['conversation']}/activities/{root}/reactions"
    headers = installed["agent"]["headers"]
    for method in ("put", "delete"):
        assert client.request(method, f"{route}/1f440_eyes").status_code == 401
        assert client.request(method, f"{route}/1f440_eyes",
                              headers=installed["otherAgent"]["headers"]).status_code == 403
        for invalid in ("arbitrary_eyes", "1f441_eyes", "watching"):
            assert client.request(method, f"{route}/{invalid}", headers=headers).status_code == 422
        assert client.request(method, f"{route}/1f440_eyes", headers=headers).status_code == 204
        message = client.get(f"/api/messages/{root}", headers=accounts["owner"]["headers"]).json()
        if method == "put":
            assert message["reactions"][0]["emoji"] == "👀"
            assert message["reactions"][0]["userIds"] == [installed["agent"]["userId"]]
        else:
            assert message["reactions"] == []


@pytest.mark.parametrize("kind", ["personal", "groupChat", "channel", "channelThread"])
@pytest.mark.parametrize("reply_route", [False, True])
def test_native_continuation_reply_uuid_is_context_not_a_phantom_thread(
    client, app, accounts, installed, kind, reply_route
):
    owner = accounts["owner"]["headers"]
    if kind in {"channel", "channelThread"}:
        cid = installed["conversation"]
        root = installed["message"]["id"] if kind == "channelThread" else None
    else:
        members = [installed["agent"]["userId"]]
        if kind == "groupChat":
            members.append(accounts["member"]["user"]["id"])
        response = client.post("/api/chats", headers=owner, json={"name": "Native continuation", "memberIds": members})
        assert response.status_code == 201
        cid, root = response.json()["conversationId"], None
    conversation_id = f"{cid};messageid={root}" if root else cid
    event_id = db.new_id()
    route = f"/v3/conversations/{conversation_id}/activities"
    body = {"type": "message", "text": "Native continuation response", "conversation": {"id": conversation_id}}
    if reply_route:
        route += f"/{event_id}"
    else:
        body["replyToId"] = event_id
    response = client.post(route, headers=installed["agent"]["headers"], json=body)
    assert response.status_code == 200, response.text
    mid = response.json()["id"]
    assert mid != event_id
    message = client.get(f"/api/messages/{mid}", headers=owner).json()
    assert message["replyToId"] == root and message["author"]["id"] == installed["agent"]["userId"]
    with app.state.sessions() as session:
        assert session.scalar(select(db.Message).where(db.Message.id == event_id)) is None
        stored = d.require_message(session, mid, installed["agent"]["userId"], own=True)
        assert stored.activity["id"] == mid
        assert stored.activity["conversation"]["id"] == conversation_id
        assert stored.activity.get("replyToId") == root
    assert client.put(f"/v3/conversations/{conversation_id}/activities/{mid}",
                      headers=installed["agent"]["headers"], json={"type": "message", "text": "Updated continuation"}).status_code == 200
    assert client.put(f"/v3/conversations/{conversation_id}/activities/{event_id}",
                      headers=installed["agent"]["headers"], json={"type": "message", "text": "Not an update handle"}).status_code == 404


def test_native_installation_event_welcome_replies_for_each_registered_recipient(client, app, accounts, agents):
    response = client.post("/api/chats", headers=accounts["owner"]["headers"], json={
        "name": "Two agent welcomes", "memberIds": [agents["A"]["userId"], agents["B"]["userId"]],
    })
    assert response.status_code == 201
    cid = response.json()["conversationId"]
    installations = [a for a in app.state.protocol_fixture["activities"]
                     if a["type"] == "conversationUpdate" and a["conversation"]["id"] == cid]
    assert len(installations) == 2
    for name, client_id in (("A", AGENT_A), ("B", AGENT_B)):
        event = next(a for a in installations if a["recipient"]["id"] == f"28:{client_id}")
        response = client.post(f"/v3/conversations/{cid}/activities/{event['id']}",
                               headers=agents[name]["headers"], json={"type": "message", "text": f"Welcome from {name}"})
        assert response.status_code == 200, response.text
        message = client.get(f"/api/messages/{response.json()['id']}",
                             headers=accounts["owner"]["headers"]).json()
        assert message["replyToId"] is None and message["author"]["id"] == agents[name]["userId"]


def test_invoke_event_reply_uses_its_canonical_channel_thread_and_rejects_other_threads(
    client, app, accounts, installed
):
    cid, root = installed["conversation"], installed["message"]["id"]
    mid = connector_message(client, installed, attachments=[{"contentType": ADAPTIVE_CARD, "content": CARD}])
    assert client.post(f"/api/messages/{mid}/actions", headers=accounts["owner"]["headers"],
                       json={"action": EXECUTE}).status_code == 200
    event = app.state.protocol_fixture["activities"][-1]
    assert event["type"] == "invoke"
    response = client.post(f"/v3/conversations/{cid}/activities/{event['id']}",
                           headers=installed["agent"]["headers"], json={"type": "message", "text": "Invoke follow-up"})
    assert response.status_code == 200, response.text
    message = client.get(f"/api/messages/{response.json()['id']}", headers=accounts["owner"]["headers"]).json()
    assert message["replyToId"] == root
    other = client.post(f"/api/conversations/{cid}/messages", headers=accounts["owner"]["headers"],
                        json={"text": "Other thread"}).json()["id"]
    assert client.post(f"/v3/conversations/{cid};messageid={other}/activities/{event['id']}",
                       headers=installed["agent"]["headers"], json={"type": "message", "text": "Wrong thread"}).status_code == 403


def test_continuation_reply_references_preserve_membership_and_event_isolation(client, app, accounts, installed):
    cid, headers = installed["conversation"], installed["agent"]["headers"]
    route = f"/v3/conversations/{cid}/activities"
    body = {"type": "message", "text": "Authorized continuation"}
    event_id = db.new_id()
    assert client.post(f"{route}/{event_id}", json=body).status_code == 401
    assert client.post(f"{route}/{event_id}", headers=installed["otherAgent"]["headers"], json=body).status_code == 403
    assert client.post(f"{route}/not-an-event", headers=headers, json=body).status_code == 422
    response = client.post("/api/chats", headers=accounts["owner"]["headers"],
                           json={"memberIds": [installed["agent"]["userId"]]})
    assert response.status_code == 201
    other_cid = response.json()["conversationId"]
    event = app.state.protocol_fixture["activities"][-1]
    assert event["type"] == "conversationUpdate" and event["conversation"]["id"] == other_cid
    assert client.post(f"{route}/{event['id']}", headers=headers, json=body).status_code == 403
    foreign = client.post(f"/api/conversations/{other_cid}/messages", headers=accounts["owner"]["headers"],
                          json={"text": "Known foreign message"}).json()["id"]
    assert client.post(f"{route}/{foreign}", headers=headers, json=body).status_code == 403
    assert client.delete(f"/api/teams/{installed['team']['id']}/members/{installed['agent']['userId']}",
                         headers=accounts["owner"]["headers"]).status_code == 204
    assert client.post(f"{route}/{event_id}", headers=headers, json=body).status_code == 403


def test_both_reaction_directions_and_idempotence(client, app, accounts, installed):
    mid = connector_message(client, installed)
    member = accounts["member"]["headers"]
    for method, route, kwargs, field in [
        ("post", f"/api/messages/{mid}/reactions", {"json": {"emoji": "👍"}}, "reactionsAdded"),
        ("delete", f"/api/messages/{mid}/reactions/{quote('👍', safe='')}", {}, "reactionsRemoved"),
    ]:
        before = len(app.state.protocol_fixture["activities"])
        assert client.request(method, route, headers=member, **kwargs).status_code == 200
        reaction = app.state.protocol_fixture["activities"][-1]
        assert reaction["type"] == "messageReaction" and reaction["replyToId"] == mid
        assert reaction[field] == [{"type": "like"}]
        assert reaction["from"]["id"] == accounts["member"]["user"]["id"]
        assert reaction["conversation"]["id"] == (
            f"{installed['conversation']};messageid={installed['message']['id']}"
        )
        assert client.request(method, route, headers=member, **kwargs).status_code == 200
        assert len(app.state.protocol_fixture["activities"]) == before + 1
    route = f"/v3/conversations/{installed['conversation']}/activities/{installed['message']['id']}/reactions/eyes"
    assert client.put(route, headers=installed["agent"]["headers"]).status_code == 204
    response = client.get(f"/api/messages/{installed['message']['id']}", headers=member).json()
    assert response["reactions"][0]["emoji"] == "👀"
    assert response["reactions"][0]["users"][0]["isAgent"] is True
    assert client.delete(route, headers=installed["agent"]["headers"]).status_code == 204
    assert client.get(f"/api/messages/{installed['message']['id']}", headers=member).json()["reactions"] == []


@pytest.mark.parametrize("group", [False, True])
def test_proactive_conversation_creation_initial_message_and_persistent_members(client, accounts, agents, group):
    members = [{"id": accounts["outsider"]["user"]["id"]}]
    if group:
        members.append({"id": accounts["member"]["user"]["id"]})
    parameters = {"bot": {"id": f"28:{AGENT_A}"}, "members": members,
                  "isGroup": group, "topicName": "Proactive", "activity": {"type": "message", "text": "Proactive text"}}
    response = client.post("/v3/conversations", headers=agents["A"]["headers"], json=parameters)
    assert response.status_code == 200, response.text
    conversation = response.json()
    cid = conversation["id"]
    assert conversation["serviceUrl"].startswith("http://127.0.0.1")
    messages = client.get(f"/api/conversations/{cid}/messages", headers=accounts["outsider"]["headers"]).json()
    assert messages[0]["id"] == conversation["activityId"] and messages[0]["text"] == "Proactive text"
    assert client.get(f"/api/conversations/{cid}/messages", headers=accounts["owner"]["headers"]).status_code == 403
    assert client.get(f"/v3/conversations/{cid}/members", headers=agents["B"]["headers"]).status_code == 403
    assert len(client.get(f"/v3/conversations/{cid}/members", headers=agents["A"]["headers"]).json()) == len(members) + 1
    if not group:
        assert client.post("/v3/conversations", headers=agents["A"]["headers"],
                           json={k: v for k, v in parameters.items() if k != "activity"}).json()["id"] == cid


def test_proactive_existing_channel_requires_agent_membership(client, installed):
    body = {"channelData": {"channel": {"id": installed["team"]["channels"][0]["id"]},
                            "team": {"id": installed["team"]["id"]}},
            "activity": {"type": "message", "text": "Channel proactive"}}
    assert client.post("/v3/conversations", headers=installed["otherAgent"]["headers"], json=body).status_code == 403
    response = client.post("/v3/conversations", headers=installed["agent"]["headers"], json=body)
    assert response.status_code == 200 and response.json()["id"] == installed["conversation"]
    for invalid in ({"bot": []}, {"channelData": {"tenant": []}}, {"members": [{}]},
                    {"members": [], "tenantId": "wrong"}, {"members": [], "unsupported": True}):
        assert client.post("/v3/conversations", headers=installed["agent"]["headers"], json=invalid).status_code in {403, 422}


def test_submit_value_and_synchronous_execute_card_refresh(client, app, accounts, installed):
    mid = connector_message(client, installed, text="", attachments=[{"contentType": ADAPTIVE_CARD, "content": CARD}])
    response = client.post(f"/api/messages/{mid}/actions", headers=accounts["member"]["headers"],
                           json={"action": SUBMIT, "inputs": {"answer": "42"}})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["invokeResponse"] is None and result["message"]["id"] == mid
    submit = app.state.protocol_fixture["activities"][-1]
    assert submit["type"] == "message" and submit["value"] == {"choice": "yes", "answer": "42"}
    assert submit["replyToId"] == mid and submit["id"] == result["activityId"]
    response = client.post(f"/api/messages/{mid}/actions", headers=accounts["member"]["headers"],
                           json={"action": EXECUTE, "inputs": {"answer": "updated"}})
    assert response.status_code == 200, response.text
    result = response.json()
    invoke = app.state.protocol_fixture["activities"][-1]
    assert invoke["type"] == "invoke" and invoke["name"] == "adaptiveCard/action"
    assert invoke["value"] == {"action": {**EXECUTE, "data": {"operation": "refresh", "answer": "updated"}},
                               "trigger": "manual"}
    assert result["invokeResponse"] == {"statusCode": 200, "type": ADAPTIVE_CARD, "value": REFRESHED}
    assert result["message"]["id"] == mid
    assert result["message"]["activityAttachments"] == [{"contentType": ADAPTIVE_CARD, "content": REFRESHED}]
    assert client.get(f"/api/messages/{mid}", headers=accounts["owner"]["headers"]).json() == result["message"]


def test_action_authorization_fallback_and_invalid_responses_are_explicit(client, app, accounts, installed):
    card = copy.deepcopy(CARD)
    card["actions"][1]["fallback"] = SUBMIT
    mid = connector_message(client, installed, attachments=[{"contentType": ADAPTIVE_CARD, "content": card}])
    route = f"/api/messages/{mid}/actions"
    assert client.post(route, headers=accounts["outsider"]["headers"], json={"action": SUBMIT}).status_code == 403
    assert client.post(route, headers=accounts["owner"]["headers"],
                       json={"action": {**SUBMIT, "data": {"choice": "forged"}}}).status_code == 403
    assert client.post(route, headers=accounts["owner"]["headers"],
                       json={"action": SUBMIT, "inputs": {"unknown": "value"}}).status_code == 422
    assert client.post(route, headers=accounts["owner"]["headers"],
                       json={"action": {"type": "Action.OpenUrl", "url": "https://example.test"}}).status_code == 422
    state = app.state.protocol_fixture
    for invalid in ({}, {"statusCode": 200}, {"statusCode": 400, "type": "application/vnd.microsoft.error", "value": {}},
                    {"statusCode": 200, "type": ADAPTIVE_CARD, "value": "not-a-card"}):
        state["invokeResponse"] = invalid
        response = client.post(route, headers=accounts["owner"]["headers"], json={"action": card["actions"][1]})
        assert response.status_code == 502
        assert response.json()["detail"]["code"] == "agent_delivery_failed"
        assert client.get(f"/api/messages/{mid}", headers=accounts["owner"]["headers"]).json()["text"] == "Agent reply"
    failures = client.get(f"/api/agents/{installed['agent']['id']}/deliveries",
                          headers=accounts["owner"]["headers"]).json()
    assert len([d for d in failures if d["status"] == "failed"]) == 4


def test_agent_file_upload_info_view_attach_and_user_download(client, app, accounts, installed):
    headers = installed["agent"]["headers"]
    cid = installed["conversation"]
    payload = b"\x00\xffsynthetic agent bytes <svg>"
    upload = client.post(f"/v3/conversations/{cid}/attachments", headers=headers, json={
        "name": "../../report.bin", "type": "application/octet-stream",
        "originalBase64": base64.b64encode(payload).decode(),
    })
    assert upload.status_code == 200
    fid = upload.json()["id"]
    info = client.get(f"/v3/attachments/{fid}", headers=headers)
    assert info.json() == {"name": "../../report.bin", "type": "application/octet-stream",
                           "views": [{"viewId": "original", "size": len(payload)}]}
    url = f"{app.state.settings.service_url}/v3/attachments/{fid}/views/original"
    view = client.get(f"/v3/attachments/{fid}/views/original", headers=headers)
    assert view.content == payload and view.headers["content-type"] == "application/octet-stream"
    assert view.headers["content-disposition"].startswith("attachment;")
    assert client.get(f"/v3/attachments/{fid}/views/thumbnail", headers=headers).status_code == 404
    assert client.get(f"/v3/attachments/{fid}", headers=installed["otherAgent"]["headers"]).status_code == 403
    assert client.get(f"/v3/attachments/{fid}/views/original").status_code == 401
    mid = connector_message(client, installed, attachments=[{"contentType": "application/octet-stream",
                                                             "contentUrl": url, "name": "Spoof"}])
    message = client.get(f"/api/messages/{mid}", headers=accounts["member"]["headers"]).json()
    assert message["attachments"][0]["name"] == "../../report.bin"
    assert message["attachments"][0]["sha256"] == hashlib.sha256(payload).hexdigest()
    assert client.get(message["attachments"][0]["downloadUrl"], headers=accounts["member"]["headers"]).content == payload
    assert client.post(f"/v3/conversations/{cid}/activities", headers=headers, json={
        "type": "message", "text": "Duplicate", "attachments": [{"contentType": "application/octet-stream", "contentUrl": url}],
    }).status_code == 409
    foreign_url = "https://attacker.invalid/v3/attachments/" + fid + "/views/original"
    assert client.post(f"/v3/conversations/{cid}/activities", headers=headers, json={
        "type": "message", "text": "Foreign", "attachments": [{"contentType": "application/octet-stream",
                                                               "contentUrl": foreign_url}],
    }).status_code == 403


def test_single_file_capability_expiry_revocation_and_byte_identity(client, app, accounts, installed):
    payload = b"download-capability bytes"
    cid = installed["conversation"]
    upload = client.post(f"/api/conversations/{cid}/files", headers=accounts["owner"]["headers"],
                         files={"file": ("shared.txt", payload, "text/plain")}).json()
    other = client.post(f"/api/conversations/{cid}/files", headers=accounts["owner"]["headers"],
                        files={"file": ("other.txt", b"other", "text/plain")}).json()
    before = time.time()
    response = client.post(f"/api/conversations/{cid}/messages", headers=accounts["owner"]["headers"],
                           json={"text": "File", "attachmentIds": [upload["id"]]})
    assert response.status_code == 201
    issued_by = time.time()
    attachment = app.state.protocol_fixture["activities"][-1]["attachments"][0]
    assert attachment["contentType"] == FILE_DOWNLOAD
    url = attachment["content"]["downloadUrl"]
    parsed = urlparse(url)
    grant = parse_qs(parsed.query)["capability"][0]
    assert url.startswith(app.state.settings.service_url + "/api/agent-files/")
    path = parsed.path + "?" + parsed.query
    assert client.get(path).content == payload
    assert client.get(path.replace(upload["id"], other["id"])).status_code == 403
    assert client.get(parsed.path).status_code == 403
    assert client.get(path.replace(grant, "x" * len(grant))).status_code == 403
    with app.state.sessions() as session:
        capability = session.get(db.AgentFileCapability, hashlib.sha256(grant.encode()).hexdigest())
        assert int(before) + 900 <= capability.expires_at <= int(issued_by) + 900
        capability.expires_at = int(time.time()) - 1
        session.commit()
    assert client.get(path).status_code == 403
    # A fresh message yields a different capability, immediately revoked on membership removal.
    assert client.post(f"/api/conversations/{cid}/messages", headers=accounts["owner"]["headers"],
                       json={"text": "Other file", "attachmentIds": [other["id"]]}).status_code == 201
    fresh = app.state.protocol_fixture["activities"][-1]["attachments"][0]["content"]["downloadUrl"]
    assert client.delete(f"/api/teams/{installed['team']['id']}/members/{installed['agent']['userId']}",
                         headers=accounts["owner"]["headers"]).status_code == 204
    fresh_path = urlparse(fresh).path + "?" + urlparse(fresh).query
    assert client.get(fresh_path).status_code == 403
    assert client.get(f"/v3/attachments/{upload['id']}", headers=installed["agent"]["headers"]).status_code == 403


def test_connector_actual_25mib_boundary_overflow_and_invalid_base64_cleanup(client, app, installed):
    route = f"/v3/conversations/{installed['conversation']}/attachments"
    headers = installed["agent"]["headers"]
    payload = b"x" * (25 * 1024 * 1024)
    encoded = base64.b64encode(payload).decode()
    response = client.post(route, headers=headers, json={
        "name": "boundary.bin", "type": "application/octet-stream", "originalBase64": encoded,
    })
    assert response.status_code == 200
    fid = response.json()["id"]
    assert client.get(f"/v3/attachments/{fid}", headers=headers).json()["views"][0]["size"] == len(payload)
    assert client.get(f"/api/files/{fid}", headers=installed["agent"]["headers"]).status_code == 401
    before = set(app.state.storage.root.iterdir())
    response = client.post(route, headers=headers, json={
        "name": "overflow.bin", "type": "application/octet-stream",
        "originalBase64": base64.b64encode(payload + b"x").decode(),
    })
    assert response.status_code == 413
    for body in ({"name": "bad.bin", "originalBase64": "%%%"},
                 {"name": "bad.bin", "originalBase64": "eA==", "thumbnailBase64": "eA=="},
                 {"name": "bad\n.bin", "originalBase64": "eA=="}):
        assert client.post(route, headers=headers, json=body).status_code == 422
    assert set(app.state.storage.root.iterdir()) == before
    assert not list(app.state.storage.root.glob("*.partial"))


def test_failed_deliveries_are_persistent_retryable_not_misleading_success(client, app, accounts, installed):
    app.state.protocol_fixture["deliveryStatus"] = 503
    response = client.post(f"/api/conversations/{installed['conversation']}/messages",
                           headers=accounts["owner"]["headers"], json={"text": "Persist despite transport failure"})
    assert response.status_code == 502
    failure = response.json()["detail"]
    assert failure["resourceCommitted"] is True
    mid = failure["resourceId"]
    assert client.get(f"/api/messages/{mid}", headers=accounts["owner"]["headers"]).json()["text"].startswith("Persist")
    aid = installed["agent"]["id"]
    deliveries = client.get(f"/api/agents/{aid}/deliveries", headers=accounts["owner"]["headers"]).json()
    failed = next(d for d in deliveries if d["activityId"] == mid)
    assert failed["status"] == "failed" and failed["attempts"] == 1
    assert "HTTP 503" in failed["error"]
    retry_route = f"/api/agents/{aid}/deliveries/{failed['id']}/retry"
    assert client.post(retry_route, headers=accounts["member"]["headers"]).status_code == 403
    app.state.protocol_fixture["deliveryStatus"] = 202
    retried = client.post(retry_route, headers=accounts["owner"]["headers"])
    assert retried.status_code == 200
    assert retried.json()["status"] == "delivered" and retried.json()["attempts"] == 2
    assert client.post(retry_route, headers=accounts["owner"]["headers"]).status_code == 409
    assert len([m for m in client.get(f"/api/conversations/{installed['conversation']}/messages",
                                    headers=accounts["owner"]["headers"]).json() if m["id"] == mid]) == 1


@pytest.mark.parametrize("overrides", [{"aud": AGENT_B}, {"azp": AGENT_B}, {"tid": "wrong"}, {"exp": 1}])
def test_outbound_tokens_are_validated_before_agent_delivery(client, app, accounts, agents, resources, overrides):
    app.state.protocol_fixture["tokenOverrides"] = overrides
    response = client.post(f"/api/teams/{resources['team']['id']}/members",
                           headers=accounts["owner"]["headers"], json={"userId": agents["A"]["userId"]})
    assert response.status_code == 502
    assert app.state.protocol_fixture["activities"] == []
    assert "identity validation" in response.json()["detail"]["failures"][0]["delivery"]["error"]


def test_missing_channel_configuration_and_token_endpoint_failure_are_explicit(client, app, accounts, agents, resources):
    channel = app.state.channel
    channel.auth.settings = replace(channel.auth.settings, channel_client_secret="")
    response = client.post(f"/api/teams/{resources['team']['id']}/members",
                           headers=accounts["owner"]["headers"], json={"userId": agents["A"]["userId"]})
    assert response.status_code == 503
    assert response.json()["detail"]["resourceCommitted"] is True
    channel.auth.settings = app.state.settings
    app.state.protocol_fixture["tokenStatus"] = 400
    response = client.post(f"/api/conversations/{resources['conversation']}/messages",
                           headers=accounts["owner"]["headers"], json={"text": "No token available"})
    assert response.status_code == 502
    assert app.state.protocol_fixture["activities"] == []


def test_all_connector_operations_require_application_authentication(client, app, installed):
    ids = {"conversationId": installed["conversation"], "replyToId": installed["message"]["id"],
           "activityId": installed["message"]["id"], "attachmentId": installed["file"]["id"],
           "reactionType": "like", "viewId": "original"}
    observed = set()
    for template, methods in app.openapi()["paths"].items():
        if not template.startswith("/v3/"):
            continue
        for method in methods:
            if method not in {"get", "post", "put", "delete"}:
                continue
            response = client.request(method, template.format(**ids), json={})
            assert response.status_code == 401, (method, template, response.text)
            observed.add((method, template))
    assert len(observed) == 11


def test_removed_or_disabled_agents_cannot_install_or_receive_historical_reactions(
    client, app, accounts, installed
):
    mid = connector_message(client, installed)
    owner = accounts["owner"]["headers"]
    agent = installed["agent"]
    assert client.delete(f"/api/teams/{installed['team']['id']}/members/{agent['userId']}",
                         headers=owner).status_code == 204
    before = len(app.state.protocol_fixture["activities"])
    assert client.post(f"/api/messages/{mid}/reactions", headers=owner, json={"emoji": "👍"}).status_code == 200
    assert len(app.state.protocol_fixture["activities"]) == before
    assert client.patch(f"/api/agents/{agent['id']}", headers=owner, json={"enabled": False}).status_code == 200
    assert client.post(f"/api/teams/{installed['team']['id']}/members", headers=owner,
                       json={"userId": agent["userId"]}).status_code == 403
    assert client.post("/api/chats", headers=owner, json={"memberIds": [agent["userId"]]}).status_code == 403


def test_channel_jku_unknown_key_redirect_and_attachment_disk_failures_are_closed(
    client, app, accounts, installed, issuer, monkeypatch
):
    claims = {"aud": CHANNEL_ID, "azp": AGENT_A, "scp": None}
    payload = jwt.decode(issuer.mint(overrides=claims), options={"verify_signature": False})
    token = jwt.encode(payload, issuer.key, algorithm="RS256",
                       headers={"kid": issuer.kid, "jku": "https://attacker.invalid/jwks"})
    route = f"/v3/conversations/{installed['conversation']}/members"
    assert client.get(route, headers={"Authorization": f"Bearer {token}"}).status_code == 200
    unknown = issuer.mint(overrides=claims, kid="unknown-key-for-channel")
    assert client.get(route, headers={"Authorization": f"Bearer {unknown}"}).status_code == 401
    app.state.protocol_fixture["deliveryStatus"] = 307
    before = len(app.state.protocol_fixture["activities"])
    assert client.post(f"/api/conversations/{installed['conversation']}/messages",
                       headers=accounts["owner"]["headers"], json={"text": "No redirects"}).status_code == 502
    assert len(app.state.protocol_fixture["activities"]) == before + 1
    stored = set(app.state.storage.root.iterdir())
    def fail(_):
        raise OSError("Synthetic Connector disk failure")
    monkeypatch.setattr(app.state.storage, "complete", fail)
    with pytest.raises(OSError, match="Synthetic Connector disk failure"):
        client.post(f"/v3/conversations/{installed['conversation']}/attachments",
                    headers=installed["agent"]["headers"],
                    json={"name": "failed.bin", "type": "application/octet-stream", "originalBase64": "eA=="})
    assert set(app.state.storage.root.iterdir()) == stored


def test_root_agent_message_update_with_thread_route_preserves_author_identity(client, accounts, installed):
    cid = installed["conversation"]
    headers = installed["agent"]["headers"]
    response = client.post(f"/v3/conversations/{cid}/activities", headers=headers,
                           json={"type": "message", "text": "Root"})
    assert response.status_code == 200
    mid = response.json()["id"]
    thread = f"{cid};messageid={mid}"
    response = client.put(f"/v3/conversations/{thread}/activities/{mid}", headers=headers,
                          json={"type": "message", "text": "Updated root", "conversation": {"id": thread},
                                "from": {"id": accounts["owner"]["user"]["id"], "name": "Spoof"}})
    assert response.status_code == 200
    message = client.get(f"/api/messages/{mid}", headers=accounts["owner"]["headers"]).json()
    assert message["text"] == "Updated root"
    assert message["author"]["id"] == installed["agent"]["userId"]


def test_invalid_initial_activity_does_not_create_a_proactive_chat(client, accounts, agents):
    before = client.get("/api/chats", headers=accounts["outsider"]["headers"]).json()
    for activity in ([], {"type": "event", "name": "unsupported"}, {"type": "message", "replyToId": []}):
        response = client.post("/v3/conversations", headers=agents["A"]["headers"], json={
            "members": [{"id": accounts["outsider"]["user"]["id"]}], "activity": activity,
        })
        assert response.status_code == 422
    assert client.get("/api/chats", headers=accounts["outsider"]["headers"]).json() == before


def test_real_loopback_reentrant_connector_callback_has_no_held_sqlite_transaction(settings, issuer):
    """Real HTTP and app-token verification, but still synthetic—not an official SDK/Entra test."""
    listeners, servers = [], []
    try:
        for _ in range(2):
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen(64)
            listeners.append(listener)
        backend_url = f"http://127.0.0.1:{listeners[0].getsockname()[1]}"
        agent_url = f"http://127.0.0.1:{listeners[1].getsockname()[1]}"
        configured = replace(settings, channel_client_id=CHANNEL_ID, channel_client_secret="synthetic-test-only",
                             service_url=backend_url)
        backend = create_app(configured)

        async def token_request(request):
            target = parse_qs(request.content.decode())["scope"][0].removesuffix("/.default")
            return httpx.Response(200, json={"token_type": "Bearer", "access_token": issuer.mint(overrides={
                "aud": target, "azp": CHANNEL_ID, "scp": None, "idtyp": "app",
            })})
        backend.state.channel.auth.client = httpx.AsyncClient(
            transport=httpx.MockTransport(token_request), trust_env=False,
        )
        fake_agent = FastAPI()

        @fake_agent.post("/api/messages")
        async def messages(request: Request):
            claims = jwt.decode(request.headers["authorization"].partition(" ")[2], issuer.key.public_key(),
                                algorithms=["RS256"], issuer=issuer.issuer, audience=AGENT_A)
            assert claims["azp"] == CHANNEL_ID and claims["tid"] == configured.tenant_id
            incoming = await request.json()
            if incoming["type"] == "message":
                assert incoming["serviceUrl"] == backend_url
                conversation = quote(incoming["conversation"]["id"], safe="")
                async with httpx.AsyncClient(timeout=5, trust_env=False) as callback:
                    reply = await callback.post(
                        f"{backend_url}/v3/conversations/{conversation}/activities/{incoming['id']}",
                        headers=bearer(issuer, CHANNEL_ID, AGENT_A),
                        json={"type": "message", "text": "Loopback echo"},
                    )
                assert reply.status_code == 200, reply.text
            return Response(status_code=202)

        for hosted, listener in zip((backend, fake_agent), listeners):
            server = uvicorn.Server(uvicorn.Config(hosted, access_log=False, log_level="critical"))
            thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
            servers.append((server, thread))
            thread.start()
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started
        with httpx.Client(base_url=backend_url, timeout=10, trust_env=False) as api:
            owner = {"Authorization": f"Bearer {issuer.mint()}"}
            assert api.get("/api/health").status_code == 200
            assert api.get("/api/me", headers=owner).status_code == 200
            agent = api.post("/api/agents", headers=owner, json={
                "name": "Local fake echo", "clientId": AGENT_A, "endpointUrl": f"{agent_url}/api/messages",
            }).json()
            chat = api.post("/api/chats", headers=owner, json={"memberIds": [agent["userId"]]}).json()
            response = api.post(f"/api/conversations/{chat['conversationId']}/messages",
                                headers=owner, json={"text": "Reentrant request"})
            assert response.status_code == 201, response.text
            messages = api.get(f"/api/conversations/{chat['conversationId']}/messages", headers=owner).json()
            assert [m["text"] for m in messages] == ["Reentrant request", "Loopback echo"]
            assert messages[1]["author"]["isAgent"] is True
    finally:
        for server, _ in servers:
            server.should_exit = True
        for _, thread in servers:
            thread.join(timeout=10)
            assert not thread.is_alive(), "Owned local HTTP server did not stop"
        for listener in listeners:
            listener.close()


def test_additive_tables_preserve_existing_collaboration_storage_and_message_data(settings, client, accounts, resources):
    engine, sessions = db.database(settings.database_url)
    with sessions() as session:
        before = session.scalar(select(db.Message).where(db.Message.id == resources["message"]["id"])).activity
    engine.dispose()
    reopened = create_app(settings)
    with TestClient(reopened) as restarted:
        response = restarted.get(f"/api/messages/{resources['message']['id']}", headers=accounts["owner"]["headers"])
        assert response.status_code == 200 and response.json()["text"] == "Hello"
        with reopened.state.sessions() as session:
            assert session.scalar(select(db.Message).where(db.Message.id == resources["message"]["id"])).activity == before
