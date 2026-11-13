from dataclasses import replace

import pytest

from backend.config import Settings
from tests.test_idp import TEST_AUTH


@pytest.mark.parametrize("test_idp", [False, True])
def test_missing_tenant_configuration_never_selects_an_identity(test_idp, monkeypatch, tmp_path):
    monkeypatch.setattr("backend.config.ROOT", tmp_path)
    for key in ("TSIM_TENANT_ID", "TSIM_CLIENT_ID", "TSIM_SCOPE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TSIM_TEST_IDP", "1" if test_idp else "0")
    with pytest.raises(ValueError, match="TSIM_TENANT_ID"):
        Settings.from_env()


@pytest.mark.parametrize("change,reason", [
    ({"tenant_id": "common"}, "TSIM_TENANT_ID"),
    ({"client_id": "<your-application-id>"}, "TSIM_CLIENT_ID"),
    ({"scope": "https://graph.microsoft.com/User.Read"}, "TSIM_SCOPE"),
    ({"scope": "api://another-api/access_as_user"}, "TSIM_SCOPE"),
    ({"environment": "prod", "test_idp": True}, "ENV"),
    ({"redirect_uri": "http://external.example.test/"}, "HTTPS"),
])
def test_portable_auth_configuration_rejects_invalid_or_ambiguous_values(change, reason):
    with pytest.raises(ValueError, match=reason):
        Settings(**(TEST_AUTH | change))


def test_configured_new_tenant_is_the_only_public_auth_binding(settings, monkeypatch):
    tenant = "33333333-3333-4333-8333-333333333333"
    client = "44444444-4444-4444-8444-444444444444"
    monkeypatch.setenv("TSIM_TENANT_ID", tenant)
    monkeypatch.setenv("TSIM_CLIENT_ID", client)
    monkeypatch.setenv("TSIM_SCOPE", f"api://{client}/access_as_user")
    monkeypatch.delenv("TSIM_TEST_IDP", raising=False)
    configured = Settings.from_env()
    assert configured.issuer == f"https://login.microsoftonline.com/{tenant}/v2.0"
    assert configured.client_id == client
    assert configured.scope == f"api://{client}/access_as_user"
    assert configured.test_idp is False
    with pytest.raises(ValueError, match="HTTPS"):
        replace(configured, environment="production", allowed_origins=("https://simulator.example.test",))
