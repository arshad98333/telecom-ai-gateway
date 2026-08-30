"""The HTTP contract: what callers send and what they receive.

Deliberately separate from the stored documents. A response is a projection chosen for
the caller, not a database row with the secrets removed - which is how a field nobody
meant to publish ends up in a public API and then in someone's integration.

Money crosses the wire as an integer count of minor units with its currency, the same
way it is stored, so no layer has to round.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from telecom_middleware.domain.models import (
    AccountStatus,
    CallbackWindow,
    CaseStatus,
    InvoiceState,
    NetworkState,
    OrderState,
    RefundReason,
    ServiceKind,
    TicketCategory,
    TicketPriority,
    TicketState,
)
from telecom_middleware.domain.money import Currency

MAX_PAGE_SIZE = 20

CxIdField = Annotated[str, Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")]
ReferenceField = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")]
ShortTextField = Annotated[str, Field(min_length=1, max_length=200)]
LongTextField = Annotated[str, Field(min_length=1, max_length=2000)]
IdempotencyKeyField = Annotated[
    str, Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
]


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Response(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Page(Response):
    total_count: int = Field(ge=0)
    truncated: bool = False


# --- authentication -----------------------------------------------------------------


class AuthenticateRequest(Request):
    cx_id: CxIdField
    #: Four digits. Never logged, never stored, never echoed back.
    passcode: str = Field(min_length=4, max_length=4)


class AuthenticateResponse(Response):
    authenticated: Literal[True]
    cx_id: str
    account_status: AccountStatus


# --- customer reads -----------------------------------------------------------------


class AccountResponse(Response):
    cx_id: str
    account_status: AccountStatus
    account_type: Literal["consumer", "business"]
    display_name: str
    customer_since: datetime
    billing_postcode_suffix: str | None = None
    open_case_count: int = Field(ge=0)


class ServiceResponse(Response):
    service_id: str
    kind: ServiceKind
    plan_name: str
    status: Literal["active", "suspended", "pending_activation"]
    monthly_price_minor: int
    currency: Currency
    contract_end_date: datetime | None = None


class ServicesResponse(Page):
    services: list[ServiceResponse] = Field(max_length=MAX_PAGE_SIZE)


class OrderResponse(Response):
    order_id: str
    state: OrderState
    placed_at: datetime
    expected_by: datetime | None = None
    summary: str


class OrdersResponse(Page):
    orders: list[OrderResponse] = Field(max_length=MAX_PAGE_SIZE)


class InvoiceResponse(Response):
    invoice_id: str
    state: InvoiceState
    issued_on: datetime
    due_on: datetime
    total_minor: int
    outstanding_minor: int
    currency: Currency


class InvoicesResponse(Page):
    invoices: list[InvoiceResponse] = Field(max_length=MAX_PAGE_SIZE)
    total_outstanding_minor: int
    currency: Currency


class NetworkStatusResponse(Response):
    state: NetworkState
    area_reference: str
    incident_id: str | None = None
    started_at: datetime | None = None
    estimated_resolution: datetime | None = None
    affected_services: list[ServiceKind] = Field(default_factory=list)
    message: str


# --- support writes -----------------------------------------------------------------


class CreateTicketRequest(Request):
    cx_id: CxIdField
    category: TicketCategory
    subject: ShortTextField
    description: LongTextField
    priority: TicketPriority = TicketPriority.NORMAL
    case_id: ReferenceField | None = None


class TicketResponse(Response):
    ticket_id: str
    cx_id: str
    category: TicketCategory
    subject: str
    state: TicketState
    priority: TicketPriority
    created_at: datetime
    cancellable_until: datetime | None = None
    deduplicated: bool = False


class TicketsResponse(Page):
    tickets: list[TicketResponse] = Field(max_length=MAX_PAGE_SIZE)


class ScheduleCallbackRequest(Request):
    cx_id: CxIdField
    preferred_date: datetime
    window: CallbackWindow
    reason: ShortTextField
    case_id: ReferenceField | None = None

    @field_validator("preferred_date")
    @classmethod
    def _must_carry_a_timezone(cls, value: datetime) -> datetime:
        # A naive datetime silently means "whatever zone the server happens to run in",
        # which for a callback is the difference between morning and the middle of the night.
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("preferred_date must include a timezone offset")
        return value


class CallbackResponse(Response):
    callback_id: str
    cx_id: str
    scheduled_for: datetime
    window: CallbackWindow
    cancellable_until: datetime
    deduplicated: bool = False


# --- approvals ----------------------------------------------------------------------


class RequestRefundApprovalRequest(Request):
    cx_id: CxIdField
    invoice_id: ReferenceField
    amount_minor: int = Field(gt=0)
    currency: Currency
    reason: RefundReason
    justification: LongTextField
    case_id: ReferenceField | None = None


class ApprovalDecisionResponse(Response):
    decided_by: str
    decided_by_role: str
    decided_at: datetime
    decision: Literal["approved", "rejected"]
    note: str | None = None


class ApprovalResponse(Response):
    request_id: str
    cx_id: str
    action: str
    amount_minor: int | None = None
    currency: Currency | None = None
    reason: RefundReason | None = None
    justification: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    state: str
    requested_by_role: str
    created_at: datetime
    expires_at: datetime
    decision: ApprovalDecisionResponse | None = None
    #: Stated explicitly on every response: a pending or approved request has still
    #: moved no money. Execution is a separate, later step outside version one.
    money_moved: Literal[False] = False
    deduplicated: bool = False


class ApprovalsResponse(Page):
    approvals: list[ApprovalResponse] = Field(max_length=MAX_PAGE_SIZE)


class DecideApprovalRequest(Request):
    decision: Literal["approved", "rejected"]
    note: ShortTextField | None = None


# --- cases --------------------------------------------------------------------------


class CaseStepRequest(Request):
    intent: ShortTextField | None = None
    tool: ReferenceField | None = None
    outcome: ShortTextField | None = None


class UpsertCaseRequest(Request):
    cx_id: CxIdField
    case_id: ReferenceField
    status: CaseStatus
    step: CaseStepRequest | None = None
    consent_recorded: bool = False
    handover_reason: ShortTextField | None = None


class CaseResponse(Response):
    case_id: str
    cx_id: str
    status: CaseStatus
    started_at: datetime
    updated_at: datetime
    tool_steps_used: int
    steps: list[CaseStepRequest] = Field(default_factory=list)
    handover_reason: str | None = None


# --- administration -----------------------------------------------------------------


class AssignmentRequest(Request):
    agent_sub: str = Field(min_length=1, max_length=128)
    cx_id: CxIdField


class AssignmentsResponse(Response):
    agent_sub: str
    accounts: list[str]


class AuditRecordResponse(Response):
    seq: int
    record_id: str
    at: datetime
    correlation_id: str
    actor_role: str
    cx_ref: str | None = None
    action: str
    resource: str
    decision: str
    outcome: str
    failure_reason: str | None = None
    entry_hash: str


class AuditResponse(Response):
    records: list[AuditRecordResponse]
    #: None when the chain verifies; otherwise the index of the first broken record.
    chain_broken_at: int | None = None
