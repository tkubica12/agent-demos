"""Loopback-only test OIDC provider, not evidence of real tenant authentication."""

import base64
import os
import time
import uuid

import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

from backend.config import TEST_PERSONAS, Settings


TEST_AUTH = {
    "tenant_id": "11111111-1111-4111-8111-111111111111",
    "client_id": "22222222-2222-4222-8222-222222222222",
    "scope": "api://22222222-2222-4222-8222-222222222222/access_as_user",
}


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    userId: str


def integer(value):
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class TestIssuer:
    __test__ = False

    def __init__(self, issuer: str, settings: Settings | None = None):
        self.settings = settings or Settings(**TEST_AUTH)
        # Reuse the production guard and loopback validation, not a second weaker policy.
        Settings(**TEST_AUTH, test_idp=True, test_issuer=issuer, environment=os.getenv("ENV", "development"))
        self.issuer = issuer.rstrip("/")
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = str(uuid.uuid4())
        numbers = self.key.public_key().public_numbers()
        self.jwk = {"kty": "RSA", "kid": self.kid, "alg": "RS256", "use": "sig",
                    "n": integer(numbers.n), "e": integer(numbers.e)}

    def mint(self, persona="owner", overrides=None, key=None, kid=None):
        account = next(p for p in TEST_PERSONAS if p["id"] == persona)
        timestamp = int(time.time())
        oid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"teams-simulator/test/{persona}"))
        claims = {
            "iss": self.issuer, "aud": self.settings.client_id, "azp": self.settings.client_id,
            "tid": self.settings.tenant_id, "sub": oid, "oid": oid,
            "iat": timestamp, "nbf": timestamp - 1, "exp": timestamp + 3600,
            "scp": "access_as_user", "roles": account["roles"],
            "preferred_username": account["upn"], "name": account["displayName"],
        }
        if overrides:
            claims.update(overrides)
        return jwt.encode(claims, key or self.key, algorithm="RS256", headers={"kid": kid or self.kid})

    def app(self):
        app = FastAPI(title="Teams Simulator TEST IdP", docs_url=None, redoc_url=None)
        app.state.issuer = self
        app.add_middleware(CORSMiddleware, allow_origins=list(self.settings.allowed_origins),
                           allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

        @app.get("/.well-known/openid-configuration")
        async def metadata():
            return {
                "issuer": self.issuer, "jwks_uri": f"{self.issuer}/jwks",
                "token_endpoint": f"{self.issuer}/token",
                "id_token_signing_alg_values_supported": ["RS256"],
                "response_types_supported": ["token"],
                "subject_types_supported": ["public"],
                "grant_types_supported": ["urn:teams-simulator:test-persona"],
            }

        @app.get("/jwks")
        async def jwks():
            return {"keys": [self.jwk]}

        @app.post("/token")
        async def token(body: TokenRequest):
            if body.userId not in {p["id"] for p in TEST_PERSONAS}:
                raise HTTPException(400, "Unknown synthetic persona")
            return {"access_token": self.mint(body.userId), "token_type": "Bearer", "expires_in": 3600}

        return app


def create_app():
    settings = Settings.from_env()
    if not settings.test_idp:
        raise ValueError("Set TSIM_TEST_IDP=1 explicitly to run the test IdP")
    return TestIssuer(settings.test_issuer, settings).app()


if __name__ == "__main__":
    from urllib.parse import urlparse

    settings = Settings.from_env()
    target = urlparse(settings.test_issuer)
    app = create_app()
    uvicorn.run(app, host=target.hostname, port=target.port or 8001,
                access_log=False, log_level="warning")
