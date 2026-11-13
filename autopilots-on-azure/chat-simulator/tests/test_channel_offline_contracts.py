"""Code-local policy probes: synthetic RSA/JWKS, in-memory DB, no listeners or native claims."""

import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from backend import db, domain as d
from backend.app import create_app
from backend.config import Settings
from tests.test_idp import TEST_AUTH


CHANNEL_ID = "50000000-0000-4000-8000-000000000001"
AGENT_ID = "60000000-0000-4000-8000-000000000001"
CONVERSATION_ID = "70000000-0000-4000-8000-000000000001"


@pytest.fixture
def offline_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def application_headers(settings, key, **overrides):
    now = int(time.time())
    claims = {"iss": settings.issuer, "aud": CHANNEL_ID, "tid": settings.tenant_id, "azp": AGENT_ID,
              "iat": now - 5, "nbf": now - 5, "exp": now + 120, "idtyp": "app", **overrides}
    token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "offline-synthetic-key"})
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def offline_backend(path, key, jwks_available=True):
    settings = Settings(**TEST_AUTH, database_url="sqlite://", storage_path=path / "files",
                        channel_client_id=CHANNEL_ID, service_url="https://offline.invalid")
    app = create_app(settings)
    public_key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public_key.update({"kid": "offline-synthetic-key", "alg": "RS256", "use": "sig"})

    async def keys(request):
        assert str(request.url) == settings.jwks_url
        return httpx.Response(200 if jwks_available else 503, json={"keys": [public_key]})

    async def unexpected_network(request):
        raise AssertionError("This code-local test must not acquire tokens or deliver activities")

    await app.state.verifier._client.aclose()
    await app.state.channel.auth.client.aclose()
    await app.state.channel.client.aclose()
    app.state.verifier._client = httpx.AsyncClient(transport=httpx.MockTransport(keys), trust_env=False)
    app.state.channel.auth.client = httpx.AsyncClient(transport=httpx.MockTransport(unexpected_network), trust_env=False)
    app.state.channel.client = httpx.AsyncClient(transport=httpx.MockTransport(unexpected_network), trust_env=False)
    agent_id, user_id, chat_id = db.new_id(), db.new_id(), db.new_id()
    with app.state.sessions() as session:
        session.add(db.User(id=user_id, source="agent", subject=agent_id, tenant_id=settings.tenant_id,
                            upn="offline-agent@synthetic.invalid", display_name="Offline synthetic agent", roles=[]))
        session.add(db.Conversation(id=CONVERSATION_ID, kind="personal", name="Offline conversation"))
        session.flush()
        session.add(db.Agent(id=agent_id, user_id=user_id, client_id=AGENT_ID,
                             endpoint_url="https://agent.invalid/messages", enabled=True))
        session.add(db.Chat(id=chat_id, conversation_id=CONVERSATION_ID, name="Offline conversation"))
        session.flush()
        session.add(db.ChatMember(chat_id=chat_id, user_id=user_id))
        session.commit()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://offline.invalid") as client:
            yield app, client, settings, agent_id, user_id, chat_id


def test_offline_matching_public_identity_still_requires_enabled_registry_and_membership(tmp_path, offline_key):
    async def check():
        async with offline_backend(tmp_path, offline_key) as (app, client, settings, agent_id, user_id, chat_id):
            headers = application_headers(settings, offline_key)
            route = f"/v3/conversations/{CONVERSATION_ID}/members"
            response = await client.get(route, headers=headers)
            assert response.status_code == 200
            assert response.json() == [{"id": f"28:{AGENT_ID}", "name": "Offline synthetic agent"}]
            with app.state.sessions() as session:
                session.get(db.Agent, agent_id).enabled = False
                session.commit()
            response = await client.get(route, headers=headers)
            assert response.status_code == 401
            assert response.json()["detail"] == "Calling application is not an enabled registered agent"
            with app.state.sessions() as session:
                session.get(db.Agent, agent_id).enabled = True
                session.delete(session.get(db.ChatMember, (chat_id, user_id)))
                session.commit()
            response = await client.get(route, headers=headers)
            assert response.status_code == 403
            assert response.json()["detail"] == "Conversation membership required"
            with app.state.sessions() as session:
                session.delete(session.get(db.Agent, agent_id))
                session.commit()
            response = await client.get(route, headers=headers)
            assert response.status_code == 401
            assert response.json()["detail"] == "Calling application is not an enabled registered agent"
    asyncio.run(check())


def test_offline_locally_valid_signature_does_not_replace_backend_key_availability(tmp_path, offline_key):
    async def check():
        async with offline_backend(tmp_path, offline_key, jwks_available=False) as (_, client, settings, _, _, _):
            headers = application_headers(settings, offline_key)
            token = headers["Authorization"].partition(" ")[2]
            claims = jwt.decode(token, offline_key.public_key(), algorithms=["RS256"],
                                audience=CHANNEL_ID, issuer=settings.issuer)
            assert claims["azp"] == AGENT_ID and claims["tid"] == settings.tenant_id
            response = await client.get(f"/v3/conversations/{CONVERSATION_ID}/members", headers=headers)
            assert response.status_code == 401
            assert response.json()["detail"] == "Signing keys unavailable"
    asyncio.run(check())


@pytest.mark.parametrize("overrides", [{"idtyp": "user"}, {"scp": "access_as_user"}])
def test_offline_matching_identity_does_not_waive_application_token_policy(tmp_path, offline_key, overrides):
    async def check():
        async with offline_backend(tmp_path, offline_key) as (_, client, settings, _, _, _):
            response = await client.get(f"/v3/conversations/{CONVERSATION_ID}/members",
                                        headers=application_headers(settings, offline_key, **overrides))
            assert response.status_code == 401
            assert response.json()["detail"] == "Client-credentials application token required"
    asyncio.run(check())


@pytest.mark.parametrize("requested", ["2026-11-08T23:50:00Z", "2026-11-12T20:11:00Z"])
def test_offline_native_timestamp_is_not_a_backdating_contract(tmp_path, offline_key, requested):
    async def check():
        async with offline_backend(tmp_path, offline_key) as (app, client, settings, _, user_id, _):
            before = datetime.now(timezone.utc)
            response = await client.post(f"/v3/conversations/{CONVERSATION_ID}/activities",
                                         headers=application_headers(settings, offline_key),
                                         json={"type": "message", "text": "Synthetic timestamp probe", "timestamp": requested})
            after = datetime.now(timezone.utc)
            assert response.status_code == 200
            with app.state.sessions() as session:
                message = d.require_message(session, response.json()["id"], user_id, own=True)
                snapshot = d.message_json(session, message)
                assert before <= datetime.fromisoformat(snapshot["createdAt"]) <= after
                assert snapshot["createdAt"] == message.activity["timestamp"]
                assert snapshot["createdAt"] != requested
    asyncio.run(check())
