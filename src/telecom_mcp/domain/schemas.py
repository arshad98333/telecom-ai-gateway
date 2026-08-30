"""Frozen v1 input and output contracts for every tool.

Two rules shape everything here.

**Inputs are validated once, at the edge, into a typed structure.** After that
boundary the code trusts its own data. Every field is bounded: strings have maximum
lengths, numbers have ranges, enumerations replace free text wherever a fixed set
exists. A caller cannot ask for an unbounded amount of anything.

**Outputs are projections, not pass-throughs.** The response shape is the small set
of fields the agent needs to answer the customer, not whatever the middleware
returned. This is the single largest cost lever in the system: every field in a tool
result is spent again on every subsequent model turn in that conversation.

Money is ``Decimal`` serialised as a string. Floating point loses precision, and a
telecom refund that is out by a cent is a support case of its own.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --- Shared field types -------------------------------------------------------------

#: Customer reference. Bounded and pattern-checked so it cannot smuggle a query.
CxId = Annotated[str, Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")]
IdempotencyKey = Annotated[str, Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]
ShortText = Annotated[str, Field(min_length=1, max_length=200)]
LongText = Annotated[str, Field(min_length=1, max_length=2000)]
Reference = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")]

#: Page sizes are capped so one request cannot exhaust the machine or the token budget.
MAX_PAGE_SIZE = 20
PageSize = Annotated[int, Field(default=5, ge=1, le=MAX_PAGE_SIZE)]

#: The SOP caps an autonomous transaction at five units of currency.
MAX_AUTONOMOUS_AMOUNT = Decimal("5.00")


class Currency(StrEnum):
    GBP = "GBP"
    EUR = "EUR"
    USD = "USD"


class ToolInput(BaseModel):
    """Base for every tool input. Unknown fields are an error, never ignored."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ToolOutput(BaseModel):
    """Base for every tool output."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# --- get_customer_account -----------------------------------------------------------


class GetCustomerAccountInput(ToolInput):
    cx_id: CxId = Field(description="The authenticated customer's account reference.")


class AccountStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSED = "closed"
    PENDING = "pending"


class GetCustomerAccountOutput(ToolOutput):
    cx_id: str
    account_status: AccountStatus
    account_type: Literal["consumer", "business"]
    display_name: str
    customer_since: datetime
    #: Present so the agent can say "the bill goes to the address ending 4AB" without
    #: reciting the address. The full address is never projected into a tool result.
    billing_postcode_suffix: str | None = None
    open_case_count: int = Field(ge=0)


# --- get_active_services ------------------------------------------------------------


class GetActiveServicesInput(ToolInput):
    cx_id: CxId
    limit: PageSize = Field(description="Maximum services to return, at most 20.")


class ServiceKind(StrEnum):
    MOBILE = "mobile"
    BROADBAND = "broadband"
    LANDLINE = "landline"
    TV = "tv"


class ActiveService(ToolOutput):
    service_id: str
    kind: ServiceKind
    plan_name: str
    status: Literal["active", "suspended", "pending_activation"]
    monthly_price: Decimal
    currency: Currency
    contract_end_date: datetime | None = None


class GetActiveServicesOutput(ToolOutput):
    services: list[ActiveService] = Field(max_length=MAX_PAGE_SIZE)
    total_count: int = Field(ge=0)
    truncated: bool = False


# --- get_order_status ---------------------------------------------------------------


class GetOrderStatusInput(ToolInput):
    cx_id: CxId
    order_id: Reference | None = Field(
        default=None, description="A specific order. Omit for the most recent orders."
    )
    limit: PageSize


class OrderState(StrEnum):
    PLACED = "placed"
    PROCESSING = "processing"
    DISPATCHED = "dispatched"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    FAILED = "failed"


class Order(ToolOutput):
    order_id: str
    state: OrderState
    placed_at: datetime
    expected_by: datetime | None = None
    summary: str


class GetOrderStatusOutput(ToolOutput):
    orders: list[Order] = Field(max_length=MAX_PAGE_SIZE)
    total_count: int = Field(ge=0)
    truncated: bool = False


# --- get_invoice_summary ------------------------------------------------------------


class GetInvoiceSummaryInput(ToolInput):
    cx_id: CxId
    invoice_id: Reference | None = None
    limit: PageSize


class InvoiceState(StrEnum):
    PAID = "paid"
    DUE = "due"
    OVERDUE = "overdue"
    DISPUTED = "disputed"
    CANCELLED = "cancelled"


class InvoiceSummary(ToolOutput):
    invoice_id: str
    state: InvoiceState
    issued_on: datetime
    due_on: datetime
    total: Decimal
    outstanding: Decimal
    currency: Currency


class GetInvoiceSummaryOutput(ToolOutput):
    invoices: list[InvoiceSummary] = Field(max_length=MAX_PAGE_SIZE)
    total_outstanding: Decimal
    currency: Currency
    truncated: bool = False


# --- get_network_status -------------------------------------------------------------


class GetNetworkStatusInput(ToolInput):
    cx_id: CxId
    service_id: Reference | None = Field(
        default=None, description="Check one service. Omit to check the whole account area."
    )


class NetworkState(StrEnum):
    OPERATIONAL = "operational"
    DEGRADED = "degraded"
    OUTAGE = "outage"
    PLANNED_MAINTENANCE = "planned_maintenance"


class GetNetworkStatusOutput(ToolOutput):
    state: NetworkState
    area_reference: str
    incident_id: str | None = None
    started_at: datetime | None = None
    estimated_resolution: datetime | None = None
    affected_services: list[ServiceKind] = Field(default_factory=list, max_length=8)
    message: str


# --- create_support_ticket ----------------------------------------------------------


class TicketCategory(StrEnum):
    BILLING = "billing"
    NETWORK = "network"
    DEVICE = "device"
    ACCOUNT = "account"
    ORDER = "order"
    OTHER = "other"


class TicketPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class CreateSupportTicketInput(ToolInput):
    cx_id: CxId
    category: TicketCategory
    subject: ShortText
    description: LongText
    priority: TicketPriority = TicketPriority.NORMAL
    idempotency_key: IdempotencyKey = Field(
        description="Required. The same key returns the original ticket rather than a new one."
    )


class CreateSupportTicketOutput(ToolOutput):
    ticket_id: str
    state: Literal["open", "queued"]
    created_at: datetime
    cancellable_until: datetime | None = None
    #: True when this call replayed an earlier identical request.
    deduplicated: bool = False


# --- schedule_callback --------------------------------------------------------------


class CallbackWindow(StrEnum):
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"


class ScheduleCallbackInput(ToolInput):
    cx_id: CxId
    preferred_date: datetime = Field(description="Date of the callback, with an explicit zone.")
    window: CallbackWindow
    reason: ShortText
    idempotency_key: IdempotencyKey

    @field_validator("preferred_date")
    @classmethod
    def _must_carry_a_timezone(cls, value: datetime) -> datetime:
        # A naive datetime silently means "whatever zone the server happens to run in".
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("preferred_date must include a timezone offset")
        return value


class ScheduleCallbackOutput(ToolOutput):
    callback_id: str
    scheduled_for: datetime
    window: CallbackWindow
    cancellable_until: datetime
    deduplicated: bool = False


# --- request_refund_approval --------------------------------------------------------


class RefundReason(StrEnum):
    BILLING_ERROR = "billing_error"
    SERVICE_OUTAGE = "service_outage"
    DUPLICATE_CHARGE = "duplicate_charge"
    GOODWILL = "goodwill"


class RequestRefundApprovalInput(ToolInput):
    cx_id: CxId
    invoice_id: Reference
    amount: Decimal = Field(gt=Decimal("0"), le=MAX_AUTONOMOUS_AMOUNT, decimal_places=2)
    currency: Currency
    reason: RefundReason
    justification: LongText
    idempotency_key: IdempotencyKey


class RequestRefundApprovalOutput(ToolOutput):
    approval_request_id: str
    state: Literal["pending_approval"]
    submitted_at: datetime
    approver_role: Literal["supervisor_approver"]
    #: Nothing has moved. The agent must say so to the customer.
    money_moved: Literal[False] = False
    deduplicated: bool = False
