"""The documents this service stores, and the shapes it returns.

Two rules shape every model here.

**Tenancy is a field, not a convention.** Every stored document carries ``tenant_id``,
and the repository layer builds every filter from it. There is no code path that can
express a query without one.

**Nothing secret is a field.** The four-digit passcode is represented only by its hash
and the state of the attempt counter. There is no attribute anywhere that holds the
passcode itself, so no serialiser, log line or response can leak one.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from telecom_middleware.domain.money import Currency

TenantId = Annotated[str, Field(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")]
CxId = Annotated[str, Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")]
Reference = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")]
ShortText = Annotated[str, Field(min_length=1, max_length=200)]
LongText = Annotated[str, Field(min_length=1, max_length=2000)]
Subject = Annotated[str, Field(min_length=1, max_length=128)]

MAX_PAGE_SIZE = 20


class Document(BaseModel):
    """Base for stored documents. Unknown fields are an error, never carried along."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


# --- customers ----------------------------------------------------------------------


class AccountStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSED = "closed"
    PENDING = "pending"


class PasscodeState(Document):
    """Everything about the passcode except the passcode."""

    #: Argon2id hash. Verified in constant time; never returned by any endpoint.
    hash: str
    failed_attempts: int = Field(default=0, ge=0)
    locked_until: datetime | None = None
    updated_at: datetime


class Customer(Document):
    tenant_id: TenantId
    cx_id: CxId
    account_status: AccountStatus
    account_type: Literal["consumer", "business"]
    display_name: ShortText
    customer_since: datetime
    billing_postcode_suffix: str | None = Field(default=None, max_length=8)
    #: Contact details live here and are redacted on the telemetry path, never in a
    #: response to the customer who owns them.
    email: str | None = None
    phone: str | None = None
    passcode: PasscodeState
    created_at: datetime
    updated_at: datetime


# --- services, orders, invoices, network --------------------------------------------


class ServiceKind(StrEnum):
    MOBILE = "mobile"
    BROADBAND = "broadband"
    LANDLINE = "landline"
    TV = "tv"


class Service(Document):
    tenant_id: TenantId
    cx_id: CxId
    service_id: Reference
    kind: ServiceKind
    plan_name: ShortText
    status: Literal["active", "suspended", "pending_activation"]
    monthly_price_minor: int = Field(ge=0)
    currency: Currency
    contract_end_date: datetime | None = None


class OrderState(StrEnum):
    PLACED = "placed"
    PROCESSING = "processing"
    DISPATCHED = "dispatched"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    FAILED = "failed"


class Order(Document):
    tenant_id: TenantId
    cx_id: CxId
    order_id: Reference
    state: OrderState
    placed_at: datetime
    expected_by: datetime | None = None
    summary: ShortText


class InvoiceState(StrEnum):
    PAID = "paid"
    DUE = "due"
    OVERDUE = "overdue"
    DISPUTED = "disputed"
    CANCELLED = "cancelled"


class Invoice(Document):
    tenant_id: TenantId
    cx_id: CxId
    invoice_id: Reference
    state: InvoiceState
    issued_on: datetime
    due_on: datetime
    total_minor: int = Field(ge=0)
    outstanding_minor: int = Field(ge=0)
    currency: Currency


class NetworkState(StrEnum):
    OPERATIONAL = "operational"
    DEGRADED = "degraded"
    OUTAGE = "outage"
    PLANNED_MAINTENANCE = "planned_maintenance"


class NetworkStatus(Document):
    tenant_id: TenantId
    area_ref: Reference
    state: NetworkState
    incident_id: Reference | None = None
    started_at: datetime | None = None
    estimated_resolution: datetime | None = None
    affected_services: list[ServiceKind] = Field(default_factory=list, max_length=8)
    message: ShortText
    updated_at: datetime


# --- who may touch what -------------------------------------------------------------


class AgentAssignment(Document):
    """Which accounts a support agent may act on. The only source of that answer."""

    tenant_id: TenantId
    agent_sub: Subject
    cx_id: CxId
    assigned_at: datetime
    assigned_by: Subject
    expires_at: datetime | None = None


# --- tickets and callbacks ----------------------------------------------------------


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


class TicketState(StrEnum):
    OPEN = "open"
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class Ticket(Document):
    tenant_id: TenantId
    ticket_id: Reference
    cx_id: CxId
    category: TicketCategory
    subject: ShortText
    description: LongText
    priority: TicketPriority
    state: TicketState
    created_at: datetime
    created_by: Subject
    updated_at: datetime
    cancellable_until: datetime | None = None
    case_id: Reference | None = None


class CallbackWindow(StrEnum):
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"


class Callback(Document):
    tenant_id: TenantId
    callback_id: Reference
    cx_id: CxId
    scheduled_for: datetime
    window: CallbackWindow
    reason: ShortText
    state: Literal["scheduled", "completed", "cancelled"]
    created_at: datetime
    created_by: Subject
    cancellable_until: datetime


# --- the approval workflow ----------------------------------------------------------


class ApprovalAction(StrEnum):
    REFUND = "refund"
    SERVICE_CHANGE = "service_change"
    SERVICE_CANCEL = "service_cancel"


class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class RefundReason(StrEnum):
    BILLING_ERROR = "billing_error"
    SERVICE_OUTAGE = "service_outage"
    DUPLICATE_CHARGE = "duplicate_charge"
    GOODWILL = "goodwill"


class ApprovalDecision(Document):
    decided_by: Subject
    decided_by_role: str
    decided_at: datetime
    decision: Literal["approved", "rejected"]
    note: ShortText | None = None


class ApprovalRequest(Document):
    """A restricted action awaiting a human. Nothing has moved while this is pending."""

    tenant_id: TenantId
    request_id: Reference
    cx_id: CxId
    action: ApprovalAction
    amount_minor: int | None = Field(default=None, ge=0)
    currency: Currency | None = None
    reason: RefundReason | None = None
    justification: LongText
    #: What the requester saw when they raised it, so an approver reviews the same facts.
    evidence: dict[str, Any] = Field(default_factory=dict)
    state: ApprovalState
    requested_by: Subject
    requested_by_role: str
    created_at: datetime
    expires_at: datetime
    decision: ApprovalDecision | None = None
    case_id: Reference | None = None


# --- voice cases --------------------------------------------------------------------


class CaseStatus(StrEnum):
    ACTIVE = "active"
    INTERRUPTED = "interrupted"
    HANDED_OVER = "handed_over"
    CLOSED = "closed"


class CaseStep(Document):
    """One turn. Enough to resume, and nothing that could carry a secret."""

    at: datetime
    intent: ShortText | None = None
    tool: Reference | None = None
    outcome: ShortText | None = None


class Case(Document):
    tenant_id: TenantId
    case_id: Reference
    cx_id: CxId
    status: CaseStatus
    started_at: datetime
    updated_at: datetime
    #: Bounded: a case that runs away must not become an unbounded document.
    steps: list[CaseStep] = Field(default_factory=list, max_length=50)
    tool_steps_used: int = Field(default=0, ge=0)
    consent_recorded_at: datetime | None = None
    handover_reason: ShortText | None = None
    closed_at: datetime | None = None


# --- audit and events ---------------------------------------------------------------


class AuditRecord(Document):
    """Append only, hash-chained, one per state change and per refusal."""

    tenant_id: TenantId
    seq: int = Field(ge=1)
    record_id: Reference
    at: datetime
    correlation_id: str
    case_id: Reference | None = None
    actor_sub: Subject
    actor_role: str
    cx_ref: str | None = None
    action: ShortText
    resource: ShortText
    decision: Literal["accepted", "rejected"]
    outcome: ShortText
    failure_reason: ShortText | None = None
    previous_hash: str
    entry_hash: str
    detail: dict[str, Any] = Field(default_factory=dict)
