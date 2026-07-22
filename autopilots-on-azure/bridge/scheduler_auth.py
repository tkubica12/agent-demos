from __future__ import annotations

import os
from functools import cache
from typing import Any

import jwt
from fastapi import HTTPException, Request
from jwt import PyJWKClient


SCHEDULED_LEARNING_ROLE = "ScheduledLearning.Run.All"


def csv_env(name: str) -> set[str]:
    return {
        value.strip().lower()
        for value in os.getenv(name, "").split(",")
        if value.strip()
    }


def scheduler_issuers() -> set[str]:
    configured = {
        value.strip()
        for value in os.getenv("SCHEDULED_LEARNING_ISSUERS", "").split(",")
        if value.strip()
    }
    if configured:
        return configured
    tenant_id = os.getenv("AZURE_TENANT_ID", "").strip()
    if not tenant_id:
        return set()
    return {
        f"https://login.microsoftonline.com/{tenant_id}/v2.0",
        f"https://sts.windows.net/{tenant_id}/",
    }


@cache
def scheduler_jwk_client(jwks_url: str) -> PyJWKClient:
    return PyJWKClient(jwks_url, cache_keys=True)


def validate_scheduler_claims(claims: dict[str, Any]) -> dict[str, Any]:
    client_id = str(claims.get("azp") or claims.get("appid") or "").lower()
    object_id = str(claims.get("oid") or "").lower()
    roles = {str(role) for role in claims.get("roles") or []}
    allowed_client_ids = csv_env("SCHEDULED_LEARNING_ALLOWED_CLIENT_IDS")
    allowed_object_ids = csv_env("SCHEDULED_LEARNING_ALLOWED_OBJECT_IDS")
    if not client_id or client_id not in allowed_client_ids:
        raise HTTPException(status_code=403, detail="Scheduled learning client is not allowed.")
    if allowed_object_ids and object_id not in allowed_object_ids:
        raise HTTPException(status_code=403, detail="Scheduled learning identity object is not allowed.")
    if SCHEDULED_LEARNING_ROLE not in roles:
        raise HTTPException(status_code=403, detail="Scheduled learning role is missing.")
    return claims


def verify_scheduler_token(token: str) -> dict[str, Any]:
    audience = os.getenv("SCHEDULED_LEARNING_AUDIENCE", "").strip()
    jwks_url = os.getenv("SCHEDULED_LEARNING_JWKS_URL", "").strip()
    issuers = scheduler_issuers()
    if not audience or not jwks_url or not issuers:
        raise HTTPException(status_code=503, detail="Scheduled learning identity is not configured.")
    try:
        signing_key = scheduler_jwk_client(jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience.removeprefix("api://"),
            options={"verify_iss": False},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Scheduled learning token is invalid.") from exc
    if claims.get("iss") not in issuers:
        raise HTTPException(status_code=401, detail="Scheduled learning token issuer is invalid.")
    return validate_scheduler_claims(claims)


def require_scheduler_identity(request: Request) -> dict[str, Any]:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Scheduled learning requires a bearer token.")
    return verify_scheduler_token(token.strip())
