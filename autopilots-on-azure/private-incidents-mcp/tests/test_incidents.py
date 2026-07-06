from __future__ import annotations

from private_incidents_mcp.incidents import ServiceName, get_incidents_for_service, service_values


def test_all_services_have_mock_incidents() -> None:
    assert service_values() == [
        "core_banking",
        "card_payments",
        "digital_onboarding",
        "fraud_detection",
        "wealth_portfolio",
    ]
    for service in service_values():
        incidents = get_incidents_for_service(service)
        assert incidents
        assert all(incident["service"] == service for incident in incidents)
        assert all("businessKpi" in incident for incident in incidents)
        assert all("privateEndpoint" in incident for incident in incidents)


def test_service_enum_is_accepted() -> None:
    incidents = get_incidents_for_service(ServiceName.CARD_PAYMENTS)

    assert incidents[0]["id"].startswith("FSI-CARD-")


def test_unknown_service_lists_valid_values() -> None:
    try:
        get_incidents_for_service("mortgage_servicing")
    except ValueError as exc:
        assert "core_banking" in str(exc)
        assert "wealth_portfolio" in str(exc)
    else:
        raise AssertionError("Expected unknown service to fail.")
