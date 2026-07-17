from __future__ import annotations

from decimal import Decimal
from typing import Literal

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler
from pydantic import BaseModel, Field, field_validator


class InvoiceLineItem(BaseModel):
    description: str
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)

    @field_validator("description")
    @classmethod
    def require_description(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("Invoice line descriptions must not be empty.")
        return clean


class InvoiceProcessingRequest(BaseModel):
    invoice_id: str
    vendor_id: str
    purchase_order_id: str
    currency: str
    line_items: list[InvoiceLineItem] = Field(min_length=1)
    tax_amount: Decimal = Field(ge=0)
    invoice_total: Decimal = Field(gt=0)
    po_remaining_amount: Decimal = Field(gt=0)

    @field_validator("invoice_id", "vendor_id", "purchase_order_id")
    @classmethod
    def require_identifier(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("Invoice identifiers must not be empty.")
        return clean

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        clean = value.strip().upper()
        if len(clean) != 3 or not clean.isalpha():
            raise ValueError("currency must be a three-letter ISO code.")
        return clean


class PreparedInvoice(BaseModel):
    request: InvoiceProcessingRequest
    subtotal: Decimal
    calculated_total: Decimal


class ValidatedInvoice(BaseModel):
    prepared: PreparedInvoice
    arithmetic_variance: Decimal
    checks: dict[str, bool]


class InvoiceProcessingResult(BaseModel):
    invoice_id: str
    status: Literal["accepted", "rejected"]
    route: Literal["auto_post", "finance_review", "rejected"]
    checks: dict[str, bool]
    subtotal: Decimal
    tax_amount: Decimal
    invoice_total: Decimal
    purchase_order_id: str
    posting_payload: dict[str, str] | None = None
    message: str


class PrepareInvoice(Executor):
    def __init__(self) -> None:
        super().__init__(id="prepare_invoice")

    @handler
    async def prepare(
        self,
        request: InvoiceProcessingRequest,
        ctx: WorkflowContext[PreparedInvoice],
    ) -> None:
        subtotal = sum(
            (item.quantity * item.unit_price for item in request.line_items),
            start=Decimal("0"),
        )
        await ctx.send_message(
            PreparedInvoice(
                request=request,
                subtotal=subtotal,
                calculated_total=subtotal + request.tax_amount,
            )
        )


class ValidateInvoice(Executor):
    def __init__(self) -> None:
        super().__init__(id="validate_invoice")

    @handler
    async def validate(
        self,
        prepared: PreparedInvoice,
        ctx: WorkflowContext[ValidatedInvoice],
    ) -> None:
        request = prepared.request
        variance = abs(prepared.calculated_total - request.invoice_total)
        await ctx.send_message(
            ValidatedInvoice(
                prepared=prepared,
                arithmetic_variance=variance,
                checks={
                    "arithmetic_matches": variance <= Decimal("0.01"),
                    "within_purchase_order_balance": (
                        request.invoice_total <= request.po_remaining_amount
                    ),
                },
            )
        )


class RouteInvoice(Executor):
    def __init__(self, auto_post_limit: Decimal) -> None:
        super().__init__(id="route_invoice")
        self.auto_post_limit = auto_post_limit

    @handler
    async def route(
        self,
        validated: ValidatedInvoice,
        ctx: WorkflowContext[None, InvoiceProcessingResult],
    ) -> None:
        request = validated.prepared.request
        checks_pass = all(validated.checks.values())
        if not checks_pass:
            status = "rejected"
            route = "rejected"
            posting_payload = None
            message = "Invoice failed arithmetic or purchase-order controls."
        elif request.invoice_total > self.auto_post_limit:
            status = "accepted"
            route = "finance_review"
            posting_payload = None
            message = "Invoice passed controls and requires finance review due to value."
        else:
            status = "accepted"
            route = "auto_post"
            posting_payload = {
                "invoice_id": request.invoice_id,
                "vendor_id": request.vendor_id,
                "purchase_order_id": request.purchase_order_id,
                "currency": request.currency,
                "amount": str(request.invoice_total),
            }
            message = "Invoice passed controls and is ready for automated posting."
        await ctx.yield_output(
            InvoiceProcessingResult(
                invoice_id=request.invoice_id,
                status=status,
                route=route,
                checks=validated.checks,
                subtotal=validated.prepared.subtotal,
                tax_amount=request.tax_amount,
                invoice_total=request.invoice_total,
                purchase_order_id=request.purchase_order_id,
                posting_payload=posting_payload,
                message=message,
            )
        )


class InvoiceProcessingWorkflowService:
    def __init__(self, auto_post_limit: Decimal = Decimal("10000")) -> None:
        if auto_post_limit <= 0:
            raise ValueError("auto_post_limit must be positive.")
        self.auto_post_limit = auto_post_limit

    def _build(self):
        prepare = PrepareInvoice()
        validate = ValidateInvoice()
        route = RouteInvoice(self.auto_post_limit)
        return (
            WorkflowBuilder(
                start_executor=prepare,
                name="process-invoice-v1",
                output_from=[route],
            )
            .add_edge(prepare, validate)
            .add_edge(validate, route)
            .build()
        )

    async def process(
        self,
        request: InvoiceProcessingRequest,
    ) -> InvoiceProcessingResult:
        events = await self._build().run(request)
        outputs = events.get_outputs()
        if len(outputs) != 1 or not isinstance(outputs[0], InvoiceProcessingResult):
            raise RuntimeError("Invoice workflow did not produce one processing result.")
        return outputs[0]
