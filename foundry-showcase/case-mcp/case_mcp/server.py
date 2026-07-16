from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.auth.auth import AuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.middleware.authorization import AuthContext, AuthMiddleware
from starlette.responses import JSONResponse

from .repository import CaseRepository, create_repository


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def auth_mode() -> str:
    return os.getenv("MCP_AUTH_MODE", "entra_agent_identity").strip().lower()


def create_auth_provider() -> AuthProvider | None:
    mode = auth_mode()
    if mode in {"none", "disabled"}:
        return None
    if mode != "entra_agent_identity":
        raise ValueError(f"Unsupported MCP_AUTH_MODE '{mode}'.")
    public_key = os.getenv("MCP_JWT_PUBLIC_KEY") or os.getenv("MCP_JWT_SECRET")
    jwks_uri = os.getenv("MCP_JWKS_URL")
    if not public_key and not jwks_uri:
        raise ValueError(
            "entra_agent_identity requires MCP_JWT_PUBLIC_KEY, MCP_JWT_SECRET, or MCP_JWKS_URL."
        )
    return JWTVerifier(
        public_key=public_key,
        jwks_uri=jwks_uri,
        issuer=os.getenv("MCP_JWT_ISSUER") or None,
        audience=os.getenv("MCP_JWT_AUDIENCE") or None,
        algorithm=os.getenv("MCP_JWT_ALGORITHM", "RS256"),
    )


def create_auth_check():
    if auth_mode() in {"none", "disabled"}:
        return None
    allowed_client_ids = set(split_csv(os.getenv("MCP_ALLOWED_CLIENT_IDS")))
    allowed_object_ids = set(split_csv(os.getenv("MCP_ALLOWED_OBJECT_IDS")))
    required_roles = set(split_csv(os.getenv("MCP_REQUIRED_ROLES", "Case.ReadWrite.All")))

    def require_agent_identity(ctx: AuthContext) -> bool:
        if ctx.token is None:
            return False
        claims = ctx.token.claims
        client_id = str(claims.get("appid") or claims.get("azp") or claims.get("client_id") or "")
        object_id = str(claims.get("oid") or "")
        roles_claim = claims.get("roles") or []
        roles = {str(roles_claim)} if isinstance(roles_claim, str) else {str(role) for role in roles_claim}
        if allowed_client_ids and client_id not in allowed_client_ids:
            raise AuthorizationError("Agent identity client id is not allowed.")
        if allowed_object_ids and object_id not in allowed_object_ids:
            raise AuthorizationError("Agent identity object id is not allowed.")
        if required_roles and not required_roles.issubset(roles):
            raise AuthorizationError("Agent identity token is missing the required application role.")
        return bool(client_id or object_id)

    return require_agent_identity


@lru_cache(maxsize=1)
def repository() -> CaseRepository:
    return create_repository()


auth_check = create_auth_check()
mcp = FastMCP(
    name="foundry-showcase-cases",
    instructions=(
        "Governed support-case tools. Read tools inspect synthetic hosted cases. "
        "Updates must be proposed before the separately approved apply tool is called."
    ),
    auth=create_auth_provider(),
    middleware=[AuthMiddleware(auth_check)] if auth_check else None,
)


@mcp.tool
def search_cases(
    query: str = "",
    status: str | None = None,
    priority: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search support cases by free text, status, or priority."""
    return [
        {
            "case_id": case.case_id,
            "title": case.title,
            "customer": case.customer,
            "status": case.status,
            "priority": case.priority,
            "owner": case.owner,
            "summary": case.summary,
            "updated_at": case.updated_at,
        }
        for case in repository().search(query, status, priority, limit)
    ]


@mcp.tool
def get_case(case_id: str) -> dict[str, Any]:
    """Get the complete current record for one support case."""
    case = repository().get(case_id)
    if case is None:
        raise KeyError(f"Case not found: {case_id.strip().upper()}")
    return case.as_dict()


@mcp.tool
def propose_case_update(
    case_id: str,
    reason: str,
    status: str | None = None,
    owner: str | None = None,
    priority: str | None = None,
    resolution_note: str | None = None,
) -> dict[str, Any]:
    """Create a noncommitted case update proposal for user review."""
    proposal = repository().propose(
        case_id,
        {
            "status": status,
            "owner": owner,
            "priority": priority,
            "resolution_note": resolution_note,
        },
        reason,
    )
    return proposal.as_dict()


@mcp.tool
def apply_case_update(proposal_id: str, confirmation_id: str) -> dict[str, Any]:
    """Apply one previously proposed update after explicit user confirmation."""
    case, proposal = repository().apply(proposal_id, confirmation_id)
    return {
        "case": case.as_dict(),
        "proposal": proposal.as_dict(),
        "audit": {
            "action": "case.update_applied",
            "confirmation_id": confirmation_id,
        },
    }


@mcp.custom_route("/health", methods=["GET"])
async def health(request) -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "service": "foundry-showcase-cases",
            "authMode": auth_mode(),
            "storeBackend": os.getenv("CASE_STORE_BACKEND", "table"),
        }
    )


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
        port=int(os.getenv("PORT", "8000")),
        path="/mcp",
        host_origin_protection=False,
        allowed_hosts=["*"],
    )


if __name__ == "__main__":
    main()
