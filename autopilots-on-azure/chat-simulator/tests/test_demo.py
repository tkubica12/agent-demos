import base64
import json
import secrets
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid5

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from backend import db
from backend.app import create_app
from backend.config import DEMO_NAMESPACE, Settings


@pytest.fixture
def demo_settings(tmp_path):
    return Settings(
        auth_mode="demo", demo_access_code=secrets.token_urlsafe(24),
        demo_expires_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        service_url="http://127.0.0.1:8000", allowed_origins=("http://testserver",),
        database_url=f"sqlite:///{(tmp_path / 'demo.db').as_posix()}",
        storage_path=tmp_path / "files",
    )


def spec(key=None, **changes):
    return {"name": "agent-1", "displayName": "Agent One",
            "clientId": "20000000-0000-4000-8000-000000000001",
            "messagingEndpoint": "http://127.0.0.1:9101/api/messages",
            "demoKey": key or secrets.token_urlsafe(32), **changes}


def login(client, settings, name="Alice"):
    response = client.post("/api/demo/login", json={"accessCode": settings.demo_access_code, "displayName": name})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['accessToken']}"}


@pytest.mark.parametrize("changes", [
    {"auth_mode": "Demo"}, {"demo_access_code": "short"}, {"demo_expires_at": "not-a-date"},
    {"demo_expires_at": "2030-01-01T00:00:00Z"},
    {"demo_expires_at": "2020-01-01T00:00:00Z"},
    {"demo_expires_at": "2026-11-14T01:00:00"},
    {"demo_expires_at": "2026-11-14T01:00:00+01:00"},
    {"test_idp": True}, {"channel_client_secret": "not-allowed"},
])
def test_demo_requires_explicit_safe_startup(demo_settings, changes):
    with pytest.raises(ValueError):
        replace(demo_settings, **changes)


def test_production_requires_additional_acknowledgement(demo_settings):
    values = {"environment": "production", "redirect_uri": "https://chat.example.test/",
              "service_url": "https://chat.example.test", "allowed_origins": ("https://chat.example.test",)}
    with pytest.raises(ValueError, match="ACKNOWLEDGE"):
        replace(demo_settings, **values)
    assert replace(demo_settings, **values, demo_acknowledge="test-data-only").auth_mode == "demo"


def test_signed_sessions_stable_identity_restart_and_no_admin(demo_settings):
    for name in (" Alice ", "alice"):
        with TestClient(create_app(demo_settings)) as client:
            headers = login(client, demo_settings, name)
            user = client.get("/api/me", headers=headers).json()
            assert user["roles"] == ["User"]
            token = headers["Authorization"].split()[1]
            claims = jwt.decode(token, options={"verify_signature": False})
            assert claims["sub"] == str(uuid5(DEMO_NAMESPACE, "alice"))
            assert claims["exp"] <= demo_settings.demo_expiry
            assert "Admin" not in claims
            assert client.get("/api/agents", headers=headers).status_code == 403
            assert client.get("/api/users", headers=headers).status_code == 200
            config = client.get("/api/config").json()
            assert config["authMode"] == "demo"
            assert config["demo"]["banner"].startswith("DEMO MODE")
            assert demo_settings.demo_access_code not in json.dumps(config)
    with TestClient(create_app(demo_settings)) as restarted:
        assert restarted.get("/api/me", headers=headers).status_code == 200
        forged = jwt.encode(claims, secrets.token_bytes(32), algorithm="HS256")
        assert restarted.get("/api/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
        assert restarted.get("/api/me").status_code == 401


def test_access_code_attempts_rate_limited(demo_settings):
    with TestClient(create_app(demo_settings)) as client:
        for _ in range(5):
            assert client.post("/api/demo/login", json={
                "accessCode": "incorrect", "displayName": "Alice",
            }).status_code == 401
        response = client.post("/api/demo/login", json={
            "accessCode": demo_settings.demo_access_code, "displayName": "Alice",
        })
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "60"


def test_expiry_blocks_login_api_capabilities_connector_and_websocket(demo_settings, monkeypatch):
    app = create_app(demo_settings)
    with TestClient(app) as client:
        headers = login(client, demo_settings)
        with client.websocket_connect("/api/ws", headers={"Origin": "http://testserver"}) as socket:
            socket.send_json({"token": headers["Authorization"].split()[1]})
            assert socket.receive_json()["type"] == "ready"
            monkeypatch.setattr("time.time", lambda: demo_settings.demo_expiry + 1)
            for path in ("/api/me", "/api/users", "/api/health", "/api/agent-files/id/content?capability=test",
                         "/v3/conversations/id/members", "/demo/key/v3/conversations/id/members"):
                assert client.get(path, headers=headers).status_code == 410, path
            assert client.post("/api/demo/login", json={
                "accessCode": demo_settings.demo_access_code, "displayName": "Alice",
            }).status_code == 410
            config = client.get("/api/config")
            assert config.status_code == 410 and config.json()["demo"]["expiresAt"]
            with pytest.raises(WebSocketDisconnect) as error:
                client.websocket_connect("/api/ws", headers={"Origin": "http://testserver"}).__enter__()
            assert error.value.code == 1008
            app.state.hub.publish({"type": "expiry.check"})
            with pytest.raises(WebSocketDisconnect):
                socket.receive_json()


def test_demo_endpoint_cannot_enable_entra_mode(client):
    assert client.post("/api/demo/login", json={"accessCode": "anything", "displayName": "Alice"}).status_code == 404
    assert client.get("/demo/test/v3/conversations/anything/members").status_code in {404, 405}


def test_declarative_agents_idempotent_update_file_and_env(demo_settings, tmp_path, monkeypatch):
    registration = spec()
    path = tmp_path / "agents.json"
    path.write_text(json.dumps([registration]), encoding="utf-8")
    settings = replace(demo_settings, agents_file=str(path))
    with TestClient(create_app(settings)) as client:
        headers = login(client, settings)
        first = client.get("/api/users", headers=headers).json()
        agent = next(user for user in first if user.get("isAgent"))
        assert agent["displayName"] == "Agent One"
    registration["displayName"] = "Updated Agent"
    registration["messagingEndpoint"] = "https://bridge.example.test/api/messages"
    monkeypatch.setenv("TSIM_AUTH_MODE", "demo")
    monkeypatch.setenv("TSIM_TEST_IDP", "0")
    monkeypatch.setenv("TSIM_DEMO_ACCESS_CODE", demo_settings.demo_access_code)
    monkeypatch.setenv("TSIM_DEMO_EXPIRES_AT", demo_settings.demo_expires_at)
    monkeypatch.setenv("TSIM_AGENTS_JSON", json.dumps([registration]))
    monkeypatch.setenv("TSIM_DATABASE_URL", demo_settings.database_url)
    monkeypatch.setenv("TSIM_STORAGE_PATH", str(demo_settings.storage_path))
    from_env = Settings.from_env()
    with TestClient(create_app(from_env)) as client:
        users = client.get("/api/users", headers=login(client, from_env)).json()
        agents = [user for user in users if user.get("isAgent")]
        assert len(agents) == 1 and agents[0]["id"] == agent["id"]
        assert agents[0]["displayName"] == "Updated Agent"
        assert registration["demoKey"] not in json.dumps(users)


@pytest.mark.parametrize("change", [
    {"name": "bad name"}, {"displayName": " "}, {"clientId": "not-a-uuid"},
    {"messagingEndpoint": "http://public.example.test/messages"}, {"demoKey": "too-short"},
    {"demoKey": "a" * 43}, {"extra": "not-allowed"}, {"name": 1},
])
def test_agent_registration_rejects_invalid_input(demo_settings, change):
    with pytest.raises(ValueError, match="Invalid agent registrations"):
        create_app(replace(demo_settings, agents_json=json.dumps([spec(**change)])))


def test_agent_registration_rejects_duplicates_and_mixed_sources(demo_settings, tmp_path):
    value = spec()
    for agents in ([value, value], [value, {**spec(), "clientId": value["clientId"], "name": "agent-2"}],
                   [value, spec(demoKey=value["demoKey"], name="agent-2",
                                clientId="20000000-0000-4000-8000-000000000002")]):
        with pytest.raises(ValueError):
            create_app(replace(demo_settings, agents_json=json.dumps(agents)))
    with pytest.raises(ValueError, match="only one"):
        create_app(replace(demo_settings, agents_json="[]", agents_file=str(tmp_path / "agents.json")))


def test_entra_seeding_rejects_demo_keys_and_accepts_no_key(settings):
    value = spec()
    with pytest.raises(ValueError):
        create_app(replace(settings, agents_json=json.dumps([value])))
    del value["demoKey"]
    app = create_app(replace(settings, agents_json=json.dumps([value])))
    with TestClient(app):
        with app.state.sessions() as session:
            agent = session.scalar(select(db.Agent))
            assert agent.registration_name == "agent-1" and agent.demo_key is None


def test_seeding_refuses_reusing_a_persisted_other_agents_key_without_disclosing_it(demo_settings):
    first = spec()
    with TestClient(create_app(replace(demo_settings, agents_json=json.dumps([first])))):
        pass
    second = spec(name="agent-2", clientId="20000000-0000-4000-8000-000000000002",
                  demoKey=first["demoKey"])
    with pytest.raises(ValueError, match="already assigned") as error:
        create_app(replace(demo_settings, agents_json=json.dumps([second])))
    assert first["demoKey"] not in str(error.value)


def test_demo_connector_key_is_agent_scoped_and_disabled_agents_are_refused(demo_settings):
    first, second = spec(), spec(name="agent-2", clientId="20000000-0000-4000-8000-000000000002")
    settings = replace(demo_settings, agents_json=json.dumps([first, second]))
    app = create_app(settings)

    async def transport(_):
        return httpx.Response(202)

    with TestClient(app) as client:
        app.state.channel.client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        headers = login(client, settings)
        with app.state.sessions() as session:
            first_agent = session.scalar(select(db.Agent).where(db.Agent.registration_name == first["name"]))
            first_user_id = first_agent.user_id
        response = client.post("/api/chats", headers=headers, json={"memberIds": [first_user_id]})
        assert response.status_code == 201
        conversation = response.json()["conversationId"]
        route = f"/v3/conversations/{conversation}/members"
        assert client.get(f"/demo/{first['demoKey']}{route}").status_code == 200
        assert client.get(f"/demo/{second['demoKey']}{route}").status_code == 403
        with app.state.sessions.begin() as session:
            session.get(db.Agent, first_agent.id).enabled = False
        assert client.get(f"/demo/{first['demoKey']}{route}").status_code == 401


def test_demo_delivery_reply_typing_reactions_cards_and_files(demo_settings):
    value = spec()
    settings = replace(demo_settings, agents_json=json.dumps([value]))
    app = create_app(settings)
    activities = []

    async def transport(request):
        assert "authorization" not in request.headers
        assert request.headers["x-demo-channel-key"] == value["demoKey"]
        activity = json.loads(request.content)
        assert activity["serviceUrl"] == f"{settings.service_url}/demo/{value['demoKey']}"
        activities.append(activity)
        return httpx.Response(202)

    with TestClient(app) as client:
        app.state.channel.client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        alice = login(client, settings)
        bob = login(client, settings, "Bob")
        bob_user = client.get("/api/me", headers=bob).json()
        agent = next(u for u in client.get("/api/users", headers=alice).json() if u.get("isAgent"))
        response = client.post("/api/chats", headers=alice,
                               json={"name": "Demo group", "memberIds": [agent["id"], bob_user["id"]]})
        assert response.status_code == 201, response.text
        conversation = response.json()["conversationId"]
        prefix = f"/demo/{value['demoKey']}/v3"
        assert client.get(f"/v3/conversations/{conversation}/members").status_code == 401
        assert client.get(f"/demo/{secrets.token_urlsafe(32)}/v3/conversations/{conversation}/members").status_code == 401
        assert len(client.get(f"{prefix}/conversations/{conversation}/members").json()) == 3
        file = client.post(f"/api/conversations/{conversation}/files", headers=alice,
                           files={"file": ("sample.txt", b"sample bytes", "text/plain")}).json()
        response = client.post(f"/api/conversations/{conversation}/messages", headers=alice,
                               json={"text": "Hello", "attachmentIds": [file["id"]]})
        assert response.status_code == 201, response.text
        activity = next(a for a in reversed(activities) if a["type"] == "message")
        assert activity["from"]["aadObjectId"] == str(uuid5(DEMO_NAMESPACE, "alice"))
        download = activity["attachments"][0]["content"]["downloadUrl"]
        assert "/api/agent-files/" in download and client.get(download).content == b"sample bytes"
        reply_route = f"{prefix}/conversations/{conversation}/activities"
        assert client.post(reply_route, json={"type": "typing"}).status_code == 200
        response = client.post(reply_route, json={
            "type": "message", "text": "Agent reply",
            "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive",
                             "content": {"type": "AdaptiveCard", "version": "1.5",
                                         "body": [{"type": "TextBlock", "text": "A card"}]}}],
        })
        assert response.status_code == 200, response.text
        reply = response.json()["id"]
        assert client.put(f"{reply_route}/{reply}/reactions/like").status_code == 204
        assert client.post(f"/api/messages/{reply}/reactions", headers=alice,
                           json={"emoji": "\U0001f440"}).status_code == 200
        assert activities[-1]["type"] == "messageReaction"
        upload = client.post(f"{prefix}/conversations/{conversation}/attachments", json={
            "name": "agent.txt", "type": "text/plain", "originalBase64": base64.b64encode(b"agent file").decode(),
        })
        assert upload.status_code == 200, upload.text
        file_id = upload.json()["id"]
        assert client.get(f"{prefix}/attachments/{file_id}/views/original").content == b"agent file"
        response = client.post(reply_route, json={"type": "message", "attachments": [{
            "contentType": "text/plain", "contentUrl": f"{settings.service_url}{prefix}/attachments/{file_id}/views/original",
        }]})
        assert response.status_code == 200, response.text
        assert value["demoKey"] not in client.get("/api/activities/export", headers=alice).text
