from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Literal

from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.auth.auth import AuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.middleware.authorization import AuthContext, AuthMiddleware
from starlette.responses import JSONResponse


SERVICE_NAME = "step-10-support-policy-mcp"
DEFAULT_POLICY_PATH = Path(__file__).parent / "policies" / "support-policies.json"
logger = logging.getLogger(SERVICE_NAME)


def split_csv(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip() for item in value.split(",") if item.strip()}


def auth_mode() -> str:
    return os.getenv("MCP_AUTH_MODE", "disabled").strip().lower()


def create_auth_provider() -> AuthProvider | None:
    mode = auth_mode()
    if mode in {"none", "disabled"}:
        return None
    if mode != "entra":
        raise RuntimeError(f"Unsupported MCP_AUTH_MODE: {mode}")
    return JWTVerifier(
        public_key=os.getenv("MCP_JWT_PUBLIC_KEY") or None,
        jwks_uri=os.getenv("MCP_JWKS_URL") or None,
        issuer=os.getenv("MCP_JWT_ISSUER") or None,
        audience=os.getenv("MCP_JWT_AUDIENCE") or None,
        algorithm="RS256",
        ssrf_safe=bool(os.getenv("MCP_JWKS_URL")),
    )


def authorize_claims(claims: dict[str, object]) -> str:
    now = int(time.time())
    expires_at = claims.get("exp")
    not_before = claims.get("nbf")
    if not isinstance(expires_at, int) or expires_at <= now:
        raise AuthorizationError("Token expiration is missing or invalid.")
    if not isinstance(not_before, int) or not_before > now:
        raise AuthorizationError("Token not-before time is missing or invalid.")

    required_role = os.getenv("MCP_REQUIRED_ROLE", "Mcp.Tools.ReadWrite.All")
    roles_claim = claims.get("roles") or []
    roles = {str(roles_claim)} if isinstance(roles_claim, str) else {str(role) for role in roles_claim}
    if required_role not in roles:
        raise AuthorizationError(f"Required application role is missing: {required_role}")

    client_id = str(claims.get("azp") or claims.get("appid") or "")
    object_id = str(claims.get("oid") or "")
    allowed_client_ids = split_csv(os.getenv("MCP_ALLOWED_CLIENT_IDS"))
    allowed_object_ids = split_csv(os.getenv("MCP_ALLOWED_OBJECT_IDS"))
    if not allowed_client_ids or client_id not in allowed_client_ids:
        raise AuthorizationError("Calling agent client ID is not allowed.")
    if not allowed_object_ids or object_id not in allowed_object_ids:
        raise AuthorizationError("Calling agent object ID is not allowed.")

    expected_audience = os.getenv("MCP_JWT_AUDIENCE")
    audience_claim = claims.get("aud")
    audiences = {str(audience_claim)} if isinstance(audience_claim, str) else {
        str(audience) for audience in (audience_claim or [])
    }
    if expected_audience and expected_audience not in audiences:
        raise AuthorizationError("Token audience is not allowed.")

    caller = client_id or object_id
    logger.info(
        json.dumps(
            {
                "event": "mcp.caller_authorized",
                "caller": caller,
                "objectId": object_id,
                "roles": sorted(roles),
            },
            separators=(",", ":"),
        )
    )
    return caller


def require_agent_identity(context: AuthContext) -> bool:
    if context.token is None:
        return False
    authorize_claims(context.token.claims)
    return True


auth_provider = create_auth_provider()

mcp = FastMCP(
    name=SERVICE_NAME,
    instructions=(
        "Read-only Contoso Outdoors support-policy capabilities. "
        "No customer, order, refund, inventory, or shipment mutations are available."
    ),
    auth=auth_provider,
    middleware=[AuthMiddleware(require_agent_identity)] if auth_provider else None,
)


@mcp.tool
def lookup_support_policy(
    topic: Literal["returns", "shipping", "escalation"],
) -> dict[str, object]:
    """Read the authoritative Contoso Outdoors support policy for one topic."""
    policy_path = Path(os.getenv("SUPPORT_POLICY_PATH", str(DEFAULT_POLICY_PATH))).resolve()
    if not policy_path.exists():
        raise RuntimeError(f"SUPPORT_POLICY_PATH does not exist: {policy_path}")
    policies = json.loads(policy_path.read_text(encoding="utf-8"))
    policy = policies.get(topic)
    if not isinstance(policy, dict):
        raise RuntimeError(f"Support policy topic is unavailable: {topic}")
    return {"topic": topic, "policy": policy}


@mcp.custom_route("/health", methods=["GET"])
async def health(_request) -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "service": SERVICE_NAME,
            "transport": "streamable-http",
            "businessTools": ["lookup_support_policy"],
        }
    )


app = mcp.http_app(
    path="/mcp",
    transport="streamable-http",
    stateless_http=True,
    host_origin_protection=True,
)


def main() -> None:
    mcp.run(
        transport="streamable-http",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        path="/mcp",
    )


if __name__ == "__main__":
    main()
