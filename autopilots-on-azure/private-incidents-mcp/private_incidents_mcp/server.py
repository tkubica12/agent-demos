from __future__ import annotations

import os
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.auth.auth import AuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier, StaticTokenVerifier
from fastmcp.server.middleware.authorization import AuthContext, AuthMiddleware
from starlette.responses import JSONResponse

from .incidents import ServiceName, get_incidents_for_service, service_values


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def auth_mode() -> str:
    return os.getenv("MCP_AUTH_MODE", "static_key").strip().lower()


def create_auth_provider() -> AuthProvider | None:
    mode = auth_mode()
    if mode in {"none", "disabled"}:
        return None
    if mode == "static_key":
        token = os.getenv("MCP_STATIC_KEY", "demo-static-key")
        required_scopes = split_csv(os.getenv("MCP_REQUIRED_SCOPES"))
        return StaticTokenVerifier(
            tokens={
                token: {
                    "client_id": os.getenv("MCP_STATIC_CLIENT_ID", "local-demo-client"),
                    "scopes": required_scopes or ["Incidents.Read"],
                }
            },
            required_scopes=required_scopes or None,
        )
    if mode in {"jwt_managed_identity", "jwt_user_obo"}:
        public_key = os.getenv("MCP_JWT_PUBLIC_KEY") or os.getenv("MCP_JWT_SECRET")
        jwks_uri = os.getenv("MCP_JWKS_URL")
        if not public_key and not jwks_uri:
            raise ValueError(f"{mode} requires MCP_JWT_PUBLIC_KEY, MCP_JWT_SECRET, or MCP_JWKS_URL.")
        return JWTVerifier(
            public_key=public_key,
            jwks_uri=jwks_uri,
            issuer=os.getenv("MCP_JWT_ISSUER") or None,
            audience=os.getenv("MCP_JWT_AUDIENCE", "api://private-incidents-mcp"),
            algorithm=os.getenv("MCP_JWT_ALGORITHM", "RS256"),
            required_scopes=split_csv(os.getenv("MCP_REQUIRED_SCOPES")) or None,
        )
    raise ValueError(f"Unsupported MCP_AUTH_MODE '{mode}'.")


def create_auth_checks():
    mode = auth_mode()
    if mode == "jwt_managed_identity":
        allowed_client_ids = set(split_csv(os.getenv("MCP_ALLOWED_CLIENT_IDS", os.getenv("MCP_ALLOWED_CLIENT_ID", ""))))
        allowed_object_ids = set(split_csv(os.getenv("MCP_ALLOWED_OBJECT_IDS", os.getenv("MCP_ALLOWED_OBJECT_ID", ""))))

        def require_managed_identity(ctx: AuthContext) -> bool:
            if ctx.token is None:
                return False
            claims = ctx.token.claims
            client_id = str(claims.get("appid") or claims.get("azp") or claims.get("client_id") or "")
            object_id = str(claims.get("oid") or "")
            if allowed_client_ids and client_id not in allowed_client_ids:
                raise AuthorizationError("Managed identity client id is not allowed.")
            if allowed_object_ids and object_id not in allowed_object_ids:
                raise AuthorizationError("Managed identity object id is not allowed.")
            return bool(client_id or object_id)

        return require_managed_identity
    if mode == "jwt_user_obo":
        def require_user_token(ctx: AuthContext) -> bool:
            if ctx.token is None:
                return False
            claims = ctx.token.claims
            return any(claims.get(name) for name in ("oid", "sub", "preferred_username", "upn"))

        return require_user_token
    return None


auth_checks = create_auth_checks()
mcp = FastMCP(
    name="private-fsi-incidents",
    instructions="Mock private FSI incident data reachable only from the OpenClaw VNet demo.",
    auth=create_auth_provider(),
    middleware=[AuthMiddleware(auth_checks)] if auth_checks else None,
)


@mcp.tool
def get_incidents(service: ServiceName) -> list[dict[str, Any]]:
    """Return mocked private operational incidents for one FSI service."""
    return get_incidents_for_service(service)


@mcp.tool
def list_services() -> list[str]:
    """Return the valid service names accepted by get_incidents."""
    return service_values()


@mcp.custom_route("/health", methods=["GET"])
async def health(request) -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "server": "private-fsi-incidents",
            "authMode": auth_mode(),
            "services": service_values(),
        }
    )


app = mcp.http_app(path="/mcp", transport="streamable-http", stateless_http=True)


def main() -> None:
    mcp.run(
        transport="streamable-http",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8765")),
        path="/mcp",
    )


if __name__ == "__main__":
    main()
