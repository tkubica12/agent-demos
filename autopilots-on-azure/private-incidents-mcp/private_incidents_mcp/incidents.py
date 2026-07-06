from __future__ import annotations

from enum import Enum
from typing import Any


class ServiceName(str, Enum):
    CORE_BANKING = "core_banking"
    CARD_PAYMENTS = "card_payments"
    DIGITAL_ONBOARDING = "digital_onboarding"
    FRAUD_DETECTION = "fraud_detection"
    WEALTH_PORTFOLIO = "wealth_portfolio"


INCIDENTS: dict[ServiceName, list[dict[str, Any]]] = {
    ServiceName.CORE_BANKING: [
        {
            "id": "FSI-CORE-1042",
            "service": ServiceName.CORE_BANKING.value,
            "title": "Intermittent account ledger posting delay",
            "severity": "high",
            "status": "investigating",
            "startedAt": "2026-06-23T17:18:00Z",
            "affectedRegion": "europe-west",
            "customerImpact": "Batch ledger updates are delayed for premium current accounts.",
            "businessKpi": {"name": "postingLagMinutes", "value": 19, "threshold": 5},
            "suspectedCause": "Mainframe connector queue depth is above the private runbook threshold.",
            "runbookHint": "Scale ledger-bridge workers and drain queue fsi-ledger-west-01.",
            "privateEndpoint": "ledger-bridge.internal.fsi.local:8443",
            "tags": ["ledger", "mainframe", "queue"],
        }
    ],
    ServiceName.CARD_PAYMENTS: [
        {
            "id": "FSI-CARD-2207",
            "service": ServiceName.CARD_PAYMENTS.value,
            "title": "Authorization latency above SLO",
            "severity": "critical",
            "status": "active",
            "startedAt": "2026-06-23T18:41:00Z",
            "affectedRegion": "global",
            "customerImpact": "Contactless card authorization p95 exceeds issuer SLO.",
            "businessKpi": {"name": "authP95Ms", "value": 1480, "threshold": 650},
            "suspectedCause": "Issuer risk scoring dependency is saturating connection pools.",
            "runbookHint": "Enable low-risk fallback scoring and scale card-auth-api replicas.",
            "privateEndpoint": "risk-score.internal.fsi.local:9443",
            "tags": ["cards", "authorization", "risk"],
        },
        {
            "id": "FSI-CARD-2213",
            "service": ServiceName.CARD_PAYMENTS.value,
            "title": "Settlement file reconciliation warning",
            "severity": "low",
            "status": "monitoring",
            "startedAt": "2026-06-23T10:30:00Z",
            "affectedRegion": "europe-west",
            "customerImpact": "No customer-facing impact; finance ops sees delayed settlement preview.",
            "businessKpi": {"name": "unmatchedSettlementRows", "value": 37, "threshold": 10},
            "suspectedCause": "Acquirer SFTP import completed with partial retry.",
            "runbookHint": "Re-run settlement parser with replay window 2026-06-23T09:00Z/10:00Z.",
            "privateEndpoint": "settlement-import.internal.fsi.local:2222",
            "tags": ["settlement", "reconciliation", "sftp"],
        },
    ],
    ServiceName.DIGITAL_ONBOARDING: [
        {
            "id": "FSI-ONB-3105",
            "service": ServiceName.DIGITAL_ONBOARDING.value,
            "title": "KYC document extraction backlog",
            "severity": "high",
            "status": "active",
            "startedAt": "2026-06-23T16:55:00Z",
            "affectedRegion": "europe-central",
            "customerImpact": "New account opening waits for ID document extraction.",
            "businessKpi": {"name": "kycBacklogItems", "value": 428, "threshold": 75},
            "suspectedCause": "Document AI private endpoint throttling after campaign traffic spike.",
            "runbookHint": "Move low-priority cases to async lane and increase extractor concurrency.",
            "privateEndpoint": "doc-ai.privatelink.fsi.local:443",
            "tags": ["kyc", "documents", "onboarding"],
        }
    ],
    ServiceName.FRAUD_DETECTION: [
        {
            "id": "FSI-FRAUD-4071",
            "service": ServiceName.FRAUD_DETECTION.value,
            "title": "Anomaly model feature drift alert",
            "severity": "medium",
            "status": "investigating",
            "startedAt": "2026-06-23T14:12:00Z",
            "affectedRegion": "global",
            "customerImpact": "Fraud review queue has elevated false positives.",
            "businessKpi": {"name": "falsePositiveRatePercent", "value": 8.7, "threshold": 4.0},
            "suspectedCause": "Merchant category enrichment feed is stale.",
            "runbookHint": "Refresh merchant features and compare model shadow score.",
            "privateEndpoint": "fraud-features.internal.fsi.local:9000",
            "tags": ["fraud", "ml", "features"],
        }
    ],
    ServiceName.WEALTH_PORTFOLIO: [
        {
            "id": "FSI-WEALTH-5099",
            "service": ServiceName.WEALTH_PORTFOLIO.value,
            "title": "Portfolio valuation snapshot delayed",
            "severity": "medium",
            "status": "monitoring",
            "startedAt": "2026-06-23T06:45:00Z",
            "affectedRegion": "europe-west",
            "customerImpact": "Advisors see stale valuation timestamps for model portfolios.",
            "businessKpi": {"name": "valuationFreshnessMinutes", "value": 46, "threshold": 15},
            "suspectedCause": "Market data normalization job waited on private pricing feed.",
            "runbookHint": "Switch to secondary pricing feed and replay valuation window.",
            "privateEndpoint": "pricing-feed.privatelink.fsi.local:443",
            "tags": ["wealth", "portfolio", "market-data"],
        }
    ],
}


def service_values() -> list[str]:
    return [service.value for service in ServiceName]


def get_incidents_for_service(service: ServiceName | str) -> list[dict[str, Any]]:
    try:
        service_name = ServiceName(service)
    except ValueError as exc:
        valid = ", ".join(service_values())
        raise ValueError(f"Unknown service '{service}'. Valid services: {valid}") from exc
    return [dict(incident) for incident in INCIDENTS[service_name]]
