from __future__ import annotations

from .models import SupportCase


SAMPLE_CASES = [
    SupportCase(
        case_id="CASE-1001",
        title="Intermittent workforce sign-in failures",
        customer="Contoso Retail",
        status="open",
        priority="high",
        owner="Avery",
        summary="Users in two stores receive intermittent sign-in failures after a policy rollout.",
        tags=["identity", "production", "policy"],
    ),
    SupportCase(
        case_id="CASE-1002",
        title="Invoice export missing regional tax lines",
        customer="Fabrikam Services",
        status="pending_customer",
        priority="medium",
        owner="Morgan",
        summary="The customer supplied one affected invoice and is gathering two control samples.",
        tags=["billing", "export"],
    ),
    SupportCase(
        case_id="CASE-1003",
        title="Privileged access review overdue",
        customer="Northwind Health",
        status="escalated",
        priority="critical",
        owner="Riley",
        summary="A quarterly privileged access review missed its deadline and requires compliance review.",
        tags=["security", "compliance", "escalation"],
    ),
    SupportCase(
        case_id="CASE-1004",
        title="Knowledge article localization request",
        customer="Adventure Works",
        status="resolved",
        priority="low",
        owner="Jordan",
        summary="The German localization was published and validated by the customer.",
        tags=["documentation", "localization"],
        resolution_note="Published revision 7 and received customer acceptance.",
    ),
]
