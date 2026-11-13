import asyncio
import time
from dataclasses import replace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from backend.app import create_app
from backend.auth import TokenVerifier
from backend.config import Settings
from backend import db
from tests.test_idp import TEST_AUTH


@pytest.mark.parametrize(("overrides", "reason"), [
    ({"exp": 1}, "expired"),
    ({"aud": "another-api"}, "audience"),
    ({"tid": "another-tenant"}, "tenant"),
    ({"azp": "another-client"}, "authorized client"),
    ({"iss": "https://attacker.invalid"}, "issuer"),
    ({"scp": ""}, "access_as_user"),
    ({"scp": "openid profile"}, "access_as_user"),
    ({"scp": None}, "access_as_user"),
    ({"nbf": int(time.time()) + 3600}, "claims"),
    ({"oid": "not-an-object-id"}, "identity"),
    ({"aud": [TEST_AUTH["client_id"]]}, "audience"),
    ({"exp": None}, "time claims"),
    ({"iat": []}, "time claims"),
    ({"nbf": "0"}, "time claims"),
    ({"exp": True}, "time claims"),
])
def test_invalid_access_tokens_are_401(client, issuer, overrides, reason):
    token = issuer.mint(overrides=overrides)
    response = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert reason in response.json()["detail"]


def test_no_token_id_token_and_signature_rejection(client, issuer):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/users", headers={"Authorization": "Basic not-a-token"}).status_code == 401
    assert client.get("/api/me", headers={"Authorization": "Bearer malformed"}).status_code == 401
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    response = client.get("/api/me", headers={"Authorization": f"Bearer {issuer.mint(key=wrong_key)}"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid token signature"
    token = jwt.encode({"iss": issuer.issuer}, "synthetic-hmac-value-for-algorithm-rejection", algorithm="HS256")
    response = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert "RS256" in response.json()["detail"]
    id_token = issuer.mint(overrides={"scp": None})
    response = client.get("/api/teams", headers={"Authorization": f"Bearer {id_token}"})
    assert response.status_code == 401
    assert "Delegated" in response.json()["detail"]


@pytest.mark.parametrize("roles", [[], ["Other"], None, "User", [1]])
def test_assignment_required(client, issuer, roles):
    token = issuer.mint(overrides={"roles": roles})
    response = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


def test_unknown_kid_and_jku_cannot_change_fetch_target(client, issuer):
    token = issuer.mint(kid="unknown")
    response = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Unknown signing key"
    payload = jwt.decode(issuer.mint(), options={"verify_signature": False})
    token = jwt.encode(payload, issuer.key, algorithm="RS256", headers={
        "kid": issuer.kid, "jku": "http://127.0.0.1:1/never-fetch", "x5u": "http://127.0.0.1:1/key",
    })
    response = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200


def test_role_boundaries_and_registration(client, accounts):
    member = accounts["member"]["headers"]
    owner = accounts["owner"]["headers"]
    body = {"upn": "future.user@example.test", "displayName": "Future User"}
    assert client.post("/api/users", headers=member, json=body).status_code == 403
    registered = client.post("/api/users", headers=owner, json=body)
    assert registered.status_code == 201
    assert registered.json()["roles"] == []
    assert registered.json()["id"] in {u["id"] for u in client.get("/api/users", headers=member).json()}
    spoofed = {**body, "id": accounts["owner"]["user"]["id"]}
    assert client.post("/api/users", headers=owner, json=spoofed).status_code == 422


def test_preregistration_reconciles_only_signed_entra_identity(settings, issuer):
    # Test-only fixed JWKS location, still a real network fetch + complete RSA/JWT validation.
    # Synthetic claims signed here do not represent real Entra C01 evidence.
    class LocalJWKSSettings(Settings):
        @property
        def jwks_url(self):
            return f"{issuer.issuer}/jwks"

    configured = LocalJWKSSettings(**settings.__dict__)
    app = create_app(configured)
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        admin = {"Authorization": f"Bearer {issuer.mint(overrides={'iss': configured.issuer})}"}
        assert client.get("/api/me", headers=admin).json()["authSource"] == "entra"
        upn = "pending.user@example.test"
        prereg = client.post("/api/users", headers=admin, json={"upn": upn, "displayName": "Pending"}).json()
        # Local test identities cannot claim an Entra preregistration even with the same signed UPN.
        synthetic = {"Authorization": f"Bearer {issuer.mint('member', overrides={'preferred_username': upn})}"}
        local_user = client.get("/api/me", headers=synthetic).json()
        assert local_user["id"] != prereg["id"]
        real_format = {"Authorization": f"Bearer {issuer.mint('member', overrides={
            'iss': configured.issuer, 'preferred_username': upn, 'name': 'Authoritative name',
        })}"}
        response = client.get("/api/me", headers=real_format)
        assert response.status_code == 200
        assert response.json()["id"] == prereg["id"]
        assert response.json()["displayName"] == "Authoritative name"
        different_oid = {"Authorization": f"Bearer {issuer.mint('outsider', overrides={
            'iss': configured.issuer, 'preferred_username': upn,
        })}"}
        assert client.get("/api/me", headers=different_oid).status_code == 403


def test_real_auth_default_and_production_guards(settings, issuer, monkeypatch):
    with pytest.raises(ValueError, match="TSIM_TENANT_ID"):
        Settings()
    assert Settings(**TEST_AUTH).test_idp is False
    for key in ("TSIM_TEST_IDP", "ENV", "TSIM_ALLOWED_ORIGINS"):
        monkeypatch.delenv(key, raising=False)
    assert Settings.from_env().test_idp is False
    with pytest.raises(ValueError, match="production"):
        replace(settings, environment="production", test_idp=True)
    with pytest.raises(ValueError, match="HTTPS"):
        replace(settings, environment="production", test_idp=False)
    production = replace(settings, environment="production", test_idp=False,
                         redirect_uri="https://simulator.example.test/",
                         allowed_origins=("https://simulator.example.test",))
    assert production.test_idp is False
    for target in ("https://attacker.invalid", "http://example.test", "http://127.0.0.1.evil.test",
                   "http://127.0.0.1/path", "http://user@127.0.0.1", "http://127.0.0.1?query"):
        with pytest.raises(ValueError, match="loopback"):
            replace(settings, test_issuer=target)
    from fastapi.testclient import TestClient

    with TestClient(create_app(replace(settings, test_idp=False))) as client:
        assert "testAuth" not in client.get("/api/config").json()
        response = client.get("/api/me", headers={"Authorization": f"Bearer {issuer.mint()}"})
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid issuer"


def test_jwks_outage_is_closed(settings, issuer):
    configured = replace(settings, test_issuer="http://127.0.0.1:1")
    verifier = TokenVerifier(configured)

    async def verify():
        try:
            with pytest.raises(HTTPException) as error:
                await verifier.verify(issuer.mint(overrides={"iss": configured.test_issuer}))
            assert error.value.status_code == 401
            assert error.value.detail == "Signing keys unavailable"
        finally:
            await verifier.close()
    asyncio.run(verify())


def test_standalone_idp_endpoint_has_fixed_personas_and_public_keys_only(issuer):
    import httpx

    with httpx.Client(base_url=issuer.issuer, trust_env=False) as client:
        metadata = client.get("/.well-known/openid-configuration").json()
        assert metadata["issuer"] == issuer.issuer
        keys = client.get("/jwks").json()["keys"]
        assert keys == [issuer.jwk]
        assert not any(field in keys[0] for field in ("d", "p", "q", "dp", "dq", "qi"))
        response = client.post("/token", json={"userId": "member"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["token_type"] == "Bearer"
        assert payload["expires_in"] == 3600
        claims = jwt.decode(payload["access_token"], issuer.key.public_key(),
                            algorithms=["RS256"], audience=issuer.settings.client_id, issuer=issuer.issuer)
        assert claims["roles"] == ["User"]
        assert client.post("/token", json={"userId": "unknown"}).status_code == 400
        assert client.post("/token", json={"userId": "member", "roles": ["Admin"]}).status_code == 422
