from private_bot_proxy.config import Settings


def test_settings_accept_identity_configuration() -> None:
    settings = Settings(
        tenant_id="11111111-1111-1111-1111-111111111111",
        app_id="22222222-2222-2222-2222-222222222222",
        managed_identity_client_id="33333333-3333-3333-3333-333333333333",
    )
    assert settings.oauth_connection_name == "teams-sso"
    assert settings.graph_scope == "https://graph.microsoft.com/User.Read"
