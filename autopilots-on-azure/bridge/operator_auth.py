from __future__ import annotations

import asyncio
import os
import uuid
from functools import lru_cache

import jwt
from fastapi import HTTPException, Request

from bridge.channel_auth import tenant_id_from_environment


OPERATOR_SCOPE = "CollectiveLearning.Review"
AZURE_CLI_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"


@lru_cache(maxsize=4)
def _signing_keys(tenant_id: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(
        f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys",
        timeout=10,
    )


def _operator_principal(token: str) -> str:
    try:
        tenant_id = str(uuid.UUID(tenant_id_from_environment()))
        audience = str(uuid.UUID(os.environ["COLLECTIVE_LEARNING_OPERATOR_CLIENT_ID"]))
        owner_id = str(uuid.UUID(os.environ["WORKER_OWNER_ID"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="Entra Collective Learning operator authorization is not configured.") from exc
    try:
        key = _signing_keys(tenant_id).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            audience=audience,
            issuer=f"https://login.microsoftonline.com/{tenant_id}/v2.0",
            options={"require": ["exp", "iat", "nbf", "iss", "aud", "tid", "oid", "scp", "azp"]},
        )
    except jwt.PyJWKClientConnectionError as exc:
        raise HTTPException(status_code=503, detail="Entra signing keys are unavailable.") from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="A valid Entra operator access token is required.") from exc
    if (
        claims.get("tid") != tenant_id
        or claims.get("oid") != owner_id
        or claims.get("azp") != AZURE_CLI_CLIENT_ID
        or claims.get("ver") != "2.0"
        or claims.get("idtyp") == "app"
        or not isinstance(claims.get("scp"), str)
        or OPERATOR_SCOPE not in claims["scp"].split()
    ):
        raise HTTPException(status_code=403, detail="Only the authorized Worker owner may operate Collective Learning.")
    return f"entra:{tenant_id}:{owner_id}"


async def require_collective_learning_operator(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="An Entra operator bearer token is required.")
    return await asyncio.to_thread(_operator_principal, token.strip())
