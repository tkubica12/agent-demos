from __future__ import annotations

import os
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.auth.auth import AuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.middleware.authorization import AuthContext, AuthMiddleware
from starlette.responses import JSONResponse

from .shipments import TrackingId, shipment_ids, shipment_status


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def create_auth_provider() -> AuthProvider | None:
    mode = os.getenv("MCP_AUTH_MODE", "entra").strip().lower()
    if mode in {"none", "disabled"}:
        return None
    if mode != "entra":
        raise ValueError(f"Unsupported MCP_AUTH_MODE '{mode}'.")
    jwks_uri = os.getenv("MCP_JWKS_URL")
    if not jwks_uri:
        raise ValueError("entra authentication requires MCP_JWKS_URL.")
    return JWTVerifier(
        jwks_uri=jwks_uri,
        issuer=os.getenv("MCP_JWT_ISSUER") or None,
        audience=os.getenv("MCP_JWT_AUDIENCE", "api://public-shipments-mcp"),
        algorithm="RS256",
    )


def authorize_shipments(ctx: AuthContext) -> bool:
    if ctx.token is None:
        return False
    claims = ctx.token.claims
    scopes = set(str(claims.get("scp") or claims.get("scope") or "").split())
    roles_claim = claims.get("roles") or []
    roles = {str(roles_claim)} if isinstance(roles_claim, str) else {str(role) for role in roles_claim}
    allowed_scopes = set(split_csv(os.getenv("MCP_ALLOWED_SCOPES", "Shipments.Read")))
    allowed_roles = set(split_csv(os.getenv("MCP_ALLOWED_ROLES", "Shipments.Read.All")))
    if scopes.intersection(allowed_scopes) or roles.intersection(allowed_roles):
        return True
    raise AuthorizationError("Token is missing an approved shipment scope or application role.")


mcp = FastMCP(
    name="public-shipments",
    instructions="Public mock shipment tracking tools for Agent 365 BYO MCP governance demonstrations.",
    auth=create_auth_provider(),
    middleware=[AuthMiddleware(authorize_shipments)],
)


@mcp.tool
def list_demo_shipments() -> list[str]:
    """List the mock tracking IDs accepted by get_shipment_status."""
    return shipment_ids()


@mcp.tool
def get_shipment_status(tracking_id: TrackingId) -> dict[str, Any]:
    """Return the current status of one mock public shipment."""
    return shipment_status(tracking_id)


@mcp.custom_route("/health", methods=["GET"])
async def health(request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "public-shipments"})


app = mcp.http_app(
    path="/mcp",
    transport="streamable-http",
    stateless_http=True,
    host_origin_protection=False,
    allowed_hosts=["*"],
)


def main() -> None:
    mcp.run(
        transport="streamable-http",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8765")),
        path="/mcp",
        host_origin_protection=False,
        allowed_hosts=["*"],
    )


if __name__ == "__main__":
    main()
