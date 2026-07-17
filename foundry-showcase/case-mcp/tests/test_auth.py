from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.auth import AccessToken
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.middleware.authorization import AuthContext


def load_server(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CASE_STORE_BACKEND", "memory")
    monkeypatch.setenv("MCP_AUTH_MODE", "none")
    import case_mcp.server as server

    return importlib.reload(server)


@pytest.mark.asyncio
async def test_auth_provider_accepts_configured_jwt(monkeypatch: pytest.MonkeyPatch) -> None:
    server = load_server(monkeypatch)
    monkeypatch.setenv("MCP_AUTH_MODE", "entra_agent_identity")
    test_secret = "test-secret-with-at-least-32-bytes"
    monkeypatch.setenv("MCP_JWT_SECRET", test_secret)
    monkeypatch.setenv("MCP_JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("MCP_JWT_ISSUER", "https://login.microsoftonline.com/demo/v2.0")
    monkeypatch.setenv("MCP_JWT_AUDIENCE", "case-api")
    provider = server.create_auth_provider()
    token = jwt.encode(
        {
            "aud": "case-api",
            "iss": "https://login.microsoftonline.com/demo/v2.0",
            "azp": "agent-client",
            "oid": "agent-object",
            "roles": ["Case.ReadWrite.All"],
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        test_secret,
        algorithm="HS256",
    )

    assert isinstance(provider, JWTVerifier)
    assert await provider.verify_token(token) is not None


def test_auth_check_rejects_unapproved_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    server = load_server(monkeypatch)
    monkeypatch.setenv("MCP_AUTH_MODE", "entra_agent_identity")
    monkeypatch.setenv("MCP_ALLOWED_CLIENT_IDS", "allowed-client")
    monkeypatch.setenv("MCP_ALLOWED_OBJECT_IDS", "allowed-object")
    check = server.create_auth_check()
    context = AuthContext(
        token=AccessToken(
            token="opaque",
            client_id="wrong-client",
            scopes=[],
            expires_at=None,
            claims={
                "azp": "wrong-client",
                "oid": "wrong-object",
                "roles": ["Case.ReadWrite.All"],
            },
        ),
        component=None,
    )

    with pytest.raises(AuthorizationError, match="client id"):
        check(context)
