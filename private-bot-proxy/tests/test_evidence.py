from __future__ import annotations

import jwt

from private_bot_proxy.evidence import build_control_evidence, build_evidence, safe_claims


class Identity:
    claims = {
        "iss": "https://api.botframework.com",
        "aud": "app-id",
        "tid": "tenant-id",
        "secret": "must-not-leak",
    }


def unsigned_token(claims: dict[str, str]) -> str:
    return jwt.encode(claims, key="", algorithm="none")


def test_safe_claims_are_allowlisted() -> None:
    claims = safe_claims(
        unsigned_token({"aud": "api", "tid": "tenant", "scp": "scope", "raw": "secret"}),
        frozenset({"aud", "tid", "scp"}),
    )
    assert claims == {"aud": "api", "scp": "scope", "tid": "tenant"}


def test_evidence_redacts_tokens_and_connector_claims() -> None:
    token_a = unsigned_token(
        {"aud": "api://botid-app", "tid": "tenant", "scp": "defaultScopes", "raw": "secret"}
    )
    token_b = unsigned_token({"aud": "https://graph.microsoft.com", "tid": "tenant"})
    evidence = build_evidence(
        channel_id="msteams",
        connector_identity=Identity(),
        token_a=token_a,
        token_b=token_b,
        graph_me={"id": "user", "displayName": "Person", "mail": "hidden"},
        expected_audience="api://botid-app",
        expected_tenant="tenant",
    )
    rendered = str(evidence)
    assert token_a not in rendered
    assert token_b not in rendered
    assert "secret" not in rendered
    assert evidence["obo"]["tokensDiffer"] is True
    assert evidence["obo"]["graphMe"] == {"id": "user", "displayName": "Person"}


def test_control_evidence_requires_no_user_token() -> None:
    evidence = build_control_evidence(
        channel_id="msteams",
        connector_identity=Identity(),
    )
    assert evidence["channel"] == "msteams"
    assert evidence["connectorValidated"] is True
    assert evidence["connector"]["aud"] == "app-id"
    assert "secret" not in str(evidence)


def test_token_a_guid_client_id_is_valid_audience() -> None:
    client_id = "22222222-2222-2222-2222-222222222222"
    evidence = build_evidence(
        channel_id="msteams",
        connector_identity=Identity(),
        token_a=unsigned_token(
            {
                "aud": client_id,
                "tid": "tenant",
                "scp": "defaultScopes",
            }
        ),
        token_b=unsigned_token({"aud": "https://graph.microsoft.com"}),
        graph_me={"id": "user"},
        expected_audience=f"api://botid-{client_id}",
        expected_tenant="tenant",
    )
    assert evidence["tokenA"]["audienceValid"] is True


def test_token_a_wrong_audience_is_rejected() -> None:
    evidence = build_evidence(
        channel_id="msteams",
        connector_identity=Identity(),
        token_a=unsigned_token(
            {
                "aud": "33333333-3333-3333-3333-333333333333",
                "tid": "tenant",
                "scp": "defaultScopes",
            }
        ),
        token_b=unsigned_token({"aud": "https://graph.microsoft.com"}),
        graph_me={"id": "user"},
        expected_audience="api://botid-22222222-2222-2222-2222-222222222222",
        expected_tenant="tenant",
    )
    assert evidence["tokenA"]["audienceValid"] is False
