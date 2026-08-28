import os

os.environ.setdefault("PBP_TENANT_ID", "11111111-1111-1111-1111-111111111111")
os.environ.setdefault("PBP_APP_ID", "22222222-2222-2222-2222-222222222222")
os.environ.setdefault("PBP_MANAGED_IDENTITY_CLIENT_ID", "33333333-3333-3333-3333-333333333333")
os.environ.setdefault("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__AUTHTYPE", "FederatedCredentials")
os.environ.setdefault(
    "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__AUTHORITYENDPOINT",
    "https://login.microsoftonline.com/11111111-1111-1111-1111-111111111111",
)
os.environ.setdefault(
    "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID",
    "22222222-2222-2222-2222-222222222222",
)
os.environ.setdefault(
    "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__FEDERATEDCLIENTID",
    "33333333-3333-3333-3333-333333333333",
)
os.environ.setdefault(
    "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID",
    "11111111-1111-1111-1111-111111111111",
)
os.environ.setdefault(
    "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__VALIDATE_ISSUER",
    "true",
)
os.environ.setdefault(
    "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__SCOPES__0",
    "https://api.botframework.com/.default",
)
os.environ.setdefault(
    "AGENTAPPLICATION__USERAUTHORIZATION__HANDLERS__GRAPH__SETTINGS__AZUREBOTOAUTHCONNECTIONNAME",
    "teams-sso",
)

from fastapi.testclient import TestClient

from private_bot_proxy.app import app


def test_healthz() -> None:
    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unsigned_connector_activity_is_rejected() -> None:
    response = TestClient(app).post(
        "/api/messages",
        json={
            "type": "message",
            "id": "unsigned",
            "channelId": "msteams",
            "serviceUrl": "https://smba.trafficmanager.net/emea/",
            "conversation": {"id": "test"},
            "from": {"id": "user"},
            "recipient": {"id": "bot"},
            "text": "unsigned",
        },
    )
    assert response.status_code in {401, 403}


def test_malformed_connector_token_is_rejected() -> None:
    response = TestClient(app).post(
        "/api/messages",
        headers={"Authorization": "Bearer not-a-jwt"},
        json={
            "type": "message",
            "id": "malformed",
            "channelId": "msteams",
            "serviceUrl": "https://smba.trafficmanager.net/emea/",
            "conversation": {"id": "test"},
            "from": {"id": "user"},
            "recipient": {"id": "bot"},
            "text": "malformed",
        },
    )
    assert response.status_code in {401, 403}
