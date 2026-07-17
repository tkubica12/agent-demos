from decimal import Decimal

import pytest

from invoice_workflow import (
    InvoiceLineItem,
    InvoiceProcessingRequest,
    InvoiceProcessingWorkflowService,
)


def invoice(total: str, remaining: str = "20000") -> InvoiceProcessingRequest:
    return InvoiceProcessingRequest(
        invoice_id="INV-2048",
        vendor_id="VENDOR-42",
        purchase_order_id="PO-9001",
        currency="usd",
        line_items=[
            InvoiceLineItem(
                description="Managed support service",
                quantity=Decimal("2"),
                unit_price=Decimal("4500"),
            )
        ],
        tax_amount=Decimal("900"),
        invoice_total=Decimal(total),
        po_remaining_amount=Decimal(remaining),
    )


@pytest.mark.asyncio
async def test_valid_invoice_routes_to_automatic_posting() -> None:
    result = await InvoiceProcessingWorkflowService().process(invoice("9900"))

    assert result.status == "accepted"
    assert result.route == "auto_post"
    assert result.checks == {
        "arithmetic_matches": True,
        "within_purchase_order_balance": True,
    }
    assert result.posting_payload is not None
    assert result.posting_payload["currency"] == "USD"


@pytest.mark.asyncio
async def test_high_value_invoice_routes_to_finance_review() -> None:
    request = invoice("9900")
    request.line_items[0].unit_price = Decimal("5500")
    request.tax_amount = Decimal("1100")
    request.invoice_total = Decimal("12100")

    result = await InvoiceProcessingWorkflowService().process(request)

    assert result.status == "accepted"
    assert result.route == "finance_review"
    assert result.posting_payload is None


@pytest.mark.asyncio
async def test_invoice_with_invalid_total_is_rejected() -> None:
    result = await InvoiceProcessingWorkflowService().process(invoice("9800"))

    assert result.status == "rejected"
    assert result.route == "rejected"
    assert result.checks["arithmetic_matches"] is False
    assert result.posting_payload is None
