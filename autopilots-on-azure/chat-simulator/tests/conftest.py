import socket
import threading
import time
from dataclasses import replace

import pytest
import uvicorn
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings
from tests.test_idp import TEST_AUTH, TestIssuer


@pytest.fixture(scope="session")
def issuer():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(64)
    port = listener.getsockname()[1]
    provider = TestIssuer(f"http://127.0.0.1:{port}")
    server = uvicorn.Server(uvicorn.Config(provider.app(), log_level="critical", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        raise RuntimeError("Local JWKS server did not start")
    yield provider
    server.should_exit = True
    thread.join(timeout=10)
    listener.close()
    if thread.is_alive():
        raise RuntimeError("Local JWKS server did not stop")


@pytest.fixture
def settings(tmp_path, issuer, monkeypatch):
    for name in ("tenant_id", "client_id", "scope"):
        monkeypatch.setenv(f"TSIM_{name.upper()}", TEST_AUTH[name])
    return Settings(
        **TEST_AUTH,
        test_idp=True, test_issuer=issuer.issuer,
        database_url=f"sqlite:///{(tmp_path / 'collaboration.db').as_posix()}",
        storage_path=tmp_path / "files",
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def idor_app(settings):
    return create_app(replace(settings, channel_client_id="10000000-0000-4000-8000-000000000001"))


@pytest.fixture
def idor_client(idor_app):
    with TestClient(idor_app) as value:
        yield value


@pytest.fixture
def accounts(client, issuer):
    values = {}
    for persona in ("owner", "member", "outsider", "admin"):
        headers = {"Authorization": f"Bearer {issuer.mint(persona)}"}
        response = client.get("/api/me", headers=headers)
        assert response.status_code == 200
        values[persona] = {"headers": headers, "user": response.json()}
    return values


@pytest.fixture
def resources(client, accounts):
    owner = accounts["owner"]["headers"]
    member = accounts["member"]["user"]
    team_response = client.post("/api/teams", headers=owner, json={"name": "Engineering"})
    assert team_response.status_code == 201
    team = team_response.json()
    response = client.post(f"/api/teams/{team['id']}/members", headers=owner, json={"userId": member["id"]})
    assert response.status_code == 200
    conversation = team["channels"][0]["conversationId"]
    response = client.post(f"/api/conversations/{conversation}/messages", headers=owner, json={"text": "Hello"})
    assert response.status_code == 201
    message = response.json()
    response = client.post(f"/api/conversations/{conversation}/files", headers=owner,
                           files={"file": ("sample.txt", b"sample bytes", "text/plain")})
    assert response.status_code == 201
    file = response.json()
    response = client.post("/api/chats", headers=owner, json={"memberIds": [member["id"]]})
    assert response.status_code == 201
    return {"team": team, "conversation": conversation, "message": message,
            "file": file, "chat": response.json()}
