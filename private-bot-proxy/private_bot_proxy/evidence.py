from __future__ import annotations

import hashlib
from typing import Any

import jwt

CONNECTOR_CLAIM_ALLOWLIST = frozenset({"iss", "aud", "appid", "azp", "tid", "serviceurl", "ver"})
USER_CLAIM_ALLOWLIST = frozenset({"aud", "tid", "scp", "oid", "preferred_username", "ver"})


def safe_claims(token: str, allowlist: frozenset[str]) -> dict[str, Any]:
    claims = jwt.decode(token, options={"verify_signature": False, "verify_aud": False})
    return {name: claims[name] for name in sorted(allowlist) if name in claims}


def connector_claims(identity: Any) -> dict[str, Any]:
    if identity is None:
        return {}
    claims = getattr(identity, "claims", None)
    if not isinstance(claims, dict):
        return {}
    return {name: claims[name] for name in sorted(CONNECTOR_CLAIM_ALLOWLIST) if name in claims}


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def build_evidence(
    *,
    channel_id: str,
    connector_identity: Any,
    token_a: str,
    token_b: str,
    graph_me: dict[str, Any],
    expected_audience: str,
    expected_tenant: str,
) -> dict[str, Any]:
    token_a_claims = safe_claims(token_a, USER_CLAIM_ALLOWLIST)
    expected_client_id = expected_audience.removeprefix("api://botid-")
    allowed_audiences = frozenset(
        {
            expected_client_id,
            f"api://botid-{expected_client_id}",
        }
    )
    return {
        "channel": channel_id,
        "connector": connector_claims(connector_identity),
        "tokenA": {
            "claims": token_a_claims,
            "audienceValid": token_a_claims.get("aud") in allowed_audiences,
            "tenantValid": token_a_claims.get("tid") == expected_tenant,
            "scopePresent": "defaultScopes" in str(token_a_claims.get("scp", "")),
        },
        "obo": {
            "exchangeMethod": "AGENT_APP.auth.exchange_token",
            "tokensDiffer": token_fingerprint(token_a) != token_fingerprint(token_b),
            "graphMe": {
                key: graph_me[key]
                for key in ("id", "displayName", "userPrincipalName")
                if key in graph_me
            },
        },
    }


def build_control_evidence(
    *,
    channel_id: str,
    connector_identity: Any,
) -> dict[str, Any]:
    return {
        "channel": channel_id,
        "connector": connector_claims(connector_identity),
        "connectorValidated": True,
        "sso": "Send a message beginning with SSO- to exercise token A, OBO, and Graph /me.",
    }
