from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

import jwt
import pytest
from fastmcp.server.auth.providers.jwt import JWTVerifier

os.environ.setdefault("MCP_AUTH_MODE", "none")

import private_incidents_mcp.server as server_module
from private_incidents_mcp.server import create_auth_provider


def test_http_app_disables_host_origin_protection_for_aca_ingress() -> None:
    assert server_module.app is not None
    source = Path(server_module.__file__).read_text(encoding="utf-8")
    assert source.count("host_origin_protection=False") == 2
    assert source.count('allowed_hosts=["*"]') == 2


@pytest.mark.asyncio
async def test_entra_agent_identity_uses_fastmcp_jwt_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_AUTH_MODE", "entra_agent_identity")
    monkeypatch.setenv("MCP_JWT_SECRET", "test-secret")
    monkeypatch.setenv("MCP_JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("MCP_JWT_ISSUER", "https://sts.windows.net/demo/")
    monkeypatch.setenv("MCP_JWT_AUDIENCE", "api://private-incidents-mcp")
    provider = create_auth_provider()
    token = jwt.encode(
        {
            "aud": "api://private-incidents-mcp",
            "iss": "https://sts.windows.net/demo/",
            "azp": "agent-identity-client-id",
            "oid": "agent-identity-object-id",
            "roles": ["Incidents.Read.All"],
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        "test-secret",
        algorithm="HS256",
    )

    assert isinstance(provider, JWTVerifier)
    access_token = await provider.verify_token(token)
    assert access_token is not None
    assert access_token.claims["oid"] == "agent-identity-object-id"


@pytest.mark.asyncio
async def test_jwt_user_obo_uses_fastmcp_jwt_verifier_with_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_AUTH_MODE", "jwt_user_obo")
    monkeypatch.setenv("MCP_JWT_SECRET", "test-secret")
    monkeypatch.setenv("MCP_JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("MCP_JWT_ISSUER", "https://login.microsoftonline.com/demo/v2.0")
    monkeypatch.setenv("MCP_JWT_AUDIENCE", "api://private-incidents-mcp")
    monkeypatch.setenv("MCP_REQUIRED_SCOPES", "Incidents.Read")
    provider = create_auth_provider()
    token = jwt.encode(
        {
            "aud": "api://private-incidents-mcp",
            "iss": "https://login.microsoftonline.com/demo/v2.0",
            "oid": "user-object-id",
            "preferred_username": "analyst@example.test",
            "scp": "Incidents.Read",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        "test-secret",
        algorithm="HS256",
    )

    assert isinstance(provider, JWTVerifier)
    access_token = await provider.verify_token(token)
    assert access_token is not None
    assert access_token.scopes == ["Incidents.Read"]
