from __future__ import annotations

import json
import os
import time
from pathlib import Path

from fastmcp import Client
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.auth.providers.jwt import JWTVerifier
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt

from support_policy_mcp import authorize_claims, mcp


def assert_denied(claims: dict[str, object]) -> None:
    try:
        authorize_claims(claims)
    except AuthorizationError:
        return
    raise AssertionError(f"Claims should have been denied: {claims}")


async def main() -> None:
    async with Client(mcp) as client:
        tools = await client.list_tools()
        assert [tool.name for tool in tools] == ["lookup_support_policy"]
        assert all(
            fragment not in tool.name.lower()
            for tool in tools
            for fragment in ("write", "update", "delete", "refund")
        )
        result = await client.call_tool("lookup_support_policy", {"topic": "returns"})
        payload = json.loads(result.content[0].text)
        assert payload["policy"]["policy_id"] == "RET-CANARY-9031"

    v1 = (Path("toolbox/skills/v1/SKILL.md")).read_text()
    v2 = (Path("toolbox/skills/v2/SKILL.md")).read_text()
    assert "ESC-CANARY-7742" in v1
    assert "ESC-CANARY-9921" in v2
    assert "10 business days" not in v1
    assert "10 business days" in v2

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    issuer = "https://login.microsoftonline.com/test-tenant/v2.0"
    audience = "test-api-client"
    verifier = JWTVerifier(
        public_key=public_key,
        issuer=issuer,
        audience=audience,
        algorithm="RS256",
    )
    now = int(time.time())
    valid_claims = {
        "aud": audience,
        "iss": issuer,
        "iat": now,
        "nbf": now - 1,
        "exp": now + 300,
        "azp": "allowed-client",
        "oid": "allowed-object",
        "roles": ["Mcp.Tools.ReadWrite.All"],
    }
    token = jwt.encode(valid_claims, private_key, algorithm="RS256")
    access_token = await verifier.verify_token(token)
    assert access_token is not None

    os.environ["MCP_JWT_AUDIENCE"] = audience
    os.environ["MCP_REQUIRED_ROLE"] = "Mcp.Tools.ReadWrite.All"
    os.environ["MCP_ALLOWED_CLIENT_IDS"] = "allowed-client"
    os.environ["MCP_ALLOWED_OBJECT_IDS"] = "allowed-object"
    assert authorize_claims(access_token.claims) == "allowed-client"

    assert_denied({**valid_claims, "roles": []})
    assert_denied(
        {
            **valid_claims,
            "azp": "wrong-client",
            "oid": "wrong-object",
        }
    )
    assert_denied({**valid_claims, "oid": "wrong-object"})
    assert_denied({**valid_claims, "azp": "wrong-client"})
    assert_denied({**valid_claims, "aud": "wrong-audience"})
    assert_denied({key: value for key, value in valid_claims.items() if key != "exp"})
    assert_denied({key: value for key, value in valid_claims.items() if key != "nbf"})
    assert_denied({**valid_claims, "nbf": now + 3600})

    expired_token = jwt.encode(
        {**valid_claims, "exp": now - 1},
        private_key,
        algorithm="RS256",
    )
    assert await verifier.verify_token(expired_token) is None
    wrong_audience_token = jwt.encode(
        {**valid_claims, "aud": "wrong-audience"},
        private_key,
        algorithm="RS256",
    )
    assert await verifier.verify_token(wrong_audience_token) is None


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
