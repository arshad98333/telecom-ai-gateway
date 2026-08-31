"""Domain events: what happened, published once, consumed by many.

Every state change writes an event in the same transaction as the change itself. That
is what makes the outbox reliable: an event cannot exist for a change that rolled back,
and a change cannot commit without its event.

Events are the substrate for three separate things — the live supervisor feed, replay
after a dropped connection, and downstream consumers such as billing reconciliation and
analytics — which is why they carry a sequence and a subject rather than being fired
and forgotten.

An event body is safe to fan out. It carries references and states, never a customer's
contact details, never the contents of a description a customer dictated, and never
anything from the never-disclosed list.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class EventType(StrEnum):
    """The published contract. Adding a member is compatible; changing one is not."""

    TICKET_CREATED = "ticket.created"
    TICKET_UPDATED = "ticket.updated"
    CALLBACK_SCHEDULED = "callback.scheduled"
    CALLBACK_CANCELLED = "callback.cancelled"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_DECIDED = "approval.decided"
    APPROVAL_EXPIRED = "approval.expired"
    CASE_STARTED = "case.started"
    CASE_INTERRUPTED = "case.interrupted"
    CASE_RESUMED = "case.resumed"
    CASE_HANDED_OVER = "case.handed_over"
    CASE_CLOSED = "case.closed"
    AUTHENTICATION_FAILED = "authentication.failed"
    ACCOUNT_LOCKED = "account.locked"


#: Which scope a subscriber must hold to receive each event type. A subscriber is
#: filtered per event, not only at subscribe time, so a token that loses a permission
#: mid-stream stops receiving what it may no longer see.
EVENT_SCOPES: dict[EventType, str] = {
    EventType.TICKET_CREATED: "ticket:read",
    EventType.TICKET_UPDATED: "ticket:read",
    EventType.CALLBACK_SCHEDULED: "ticket:read",
    EventType.CALLBACK_CANCELLED: "ticket:read",
    EventType.APPROVAL_REQUESTED: "refund:approve",
    EventType.APPROVAL_DECIDED: "refund:approve",
    EventType.APPROVAL_EXPIRED: "refund:approve",
    EventType.CASE_STARTED: "case:read",
    EventType.CASE_INTERRUPTED: "case:read",
    EventType.CASE_RESUMED: "case:read",
    EventType.CASE_HANDED_OVER: "case:read",
    EventType.CASE_CLOSED: "case:read",
    EventType.AUTHENTICATION_FAILED: "audit:read",
    EventType.ACCOUNT_LOCKED: "audit:read",
}


class DomainEvent(BaseModel):
    """One published fact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    type: EventType
    tenant_id: str
    #: Monotonic within a tenant. A subscriber resumes from the last one it saw.
    sequence: int = Field(ge=1)
    occurred_at: datetime
    correlation_id: str
    #: The record this is about, as a reference: "approval_requests/APR-123".
    subject: str
    cx_ref: str | None = None
    case_id: str | None = None
    actor_sub: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    def required_scope(self) -> str:
        return EVENT_SCOPES[self.type]

    def to_sse(self) -> str:
        """Render as a server-sent event, with the sequence as the id for replay."""
        body = self.model_dump_json()
        return f"id: {self.sequence}\nevent: {self.type}\ndata: {body}\n\n"


class OutboxEntry(BaseModel):
    """An event awaiting relay. Written in the same transaction as its change."""

    model_config = ConfigDict(extra="forbid")

    event: DomainEvent
    status: Literal["pending", "published", "failed"] = "pending"
    attempts: int = Field(default=0, ge=0)
    created_at: datetime
    published_at: datetime | None = None
    last_error: str | None = None
