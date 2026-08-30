"""The storage interfaces, in our shapes rather than the driver's.

Two implementations exist and both must satisfy the same behavioural contract, which is
written once in ``tests/contract`` and run against each: an in-memory store for
development and the offline suite, and MongoDB for everything real. Running one suite
against both is what stops the fast implementation drifting away from the real one.

Every method takes ``tenant_id`` as a required argument. The filter is built here, from
that argument, so there is no query in this service that can omit it - tenant isolation
is a property of the type signature, not of remembering.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from typing import Any, Protocol

from telecom_middleware.domain.events import DomainEvent
from telecom_middleware.domain.models import (
    ApprovalRequest,
    ApprovalState,
    AuditRecord,
    Callback,
    Case,
    Customer,
    Invoice,
    NetworkStatus,
    Order,
    Service,
    Ticket,
)


class CustomerRepository(Protocol):
    async def get(self, tenant_id: str, cx_id: str) -> Customer | None: ...

    async def upsert(self, customer: Customer) -> None: ...

    async def record_passcode_attempt(
        self,
        tenant_id: str,
        cx_id: str,
        *,
        success: bool,
        now: datetime,
        max_attempts: int,
        lockout_s: int,
    ) -> Customer | None:
        """Apply one authentication attempt atomically and return the updated customer.

        Atomic because two concurrent guesses must not each see four attempts used and
        both be allowed a fifth.
        """
        ...


class ServiceRepository(Protocol):
    async def list_for_customer(
        self, tenant_id: str, cx_id: str, *, limit: int
    ) -> tuple[list[Service], int]: ...

    async def upsert(self, service: Service) -> None: ...


class OrderRepository(Protocol):
    async def list_for_customer(
        self, tenant_id: str, cx_id: str, *, limit: int, order_id: str | None = None
    ) -> tuple[list[Order], int]: ...

    async def upsert(self, order: Order) -> None: ...


class InvoiceRepository(Protocol):
    async def list_for_customer(
        self, tenant_id: str, cx_id: str, *, limit: int, invoice_id: str | None = None
    ) -> tuple[list[Invoice], int]: ...

    async def upsert(self, invoice: Invoice) -> None: ...


class NetworkRepository(Protocol):
    async def get_for_area(self, tenant_id: str, area_ref: str) -> NetworkStatus | None: ...

    async def upsert(self, status: NetworkStatus) -> None: ...


class AssignmentRepository(Protocol):
    async def is_assigned(self, tenant_id: str, agent_sub: str, cx_id: str) -> bool: ...

    async def assign(
        self, tenant_id: str, agent_sub: str, cx_id: str, *, by: str, now: datetime
    ) -> None: ...

    async def revoke(self, tenant_id: str, agent_sub: str, cx_id: str) -> bool: ...

    async def list_for_agent(self, tenant_id: str, agent_sub: str) -> list[str]: ...


class TicketRepository(Protocol):
    async def get(self, tenant_id: str, ticket_id: str) -> Ticket | None: ...

    async def insert(self, ticket: Ticket) -> None: ...

    async def list_for_customer(
        self, tenant_id: str, cx_id: str, *, limit: int
    ) -> tuple[list[Ticket], int]: ...


class CallbackRepository(Protocol):
    async def get(self, tenant_id: str, callback_id: str) -> Callback | None: ...

    async def insert(self, callback: Callback) -> None: ...


class ApprovalRepository(Protocol):
    async def get(self, tenant_id: str, request_id: str) -> ApprovalRequest | None: ...

    async def insert(self, request: ApprovalRequest) -> None: ...

    async def list_pending(
        self, tenant_id: str, *, limit: int
    ) -> tuple[list[ApprovalRequest], int]: ...

    async def decide(
        self, tenant_id: str, request_id: str, *, decision: dict[str, Any], state: ApprovalState
    ) -> ApprovalRequest | None:
        """Move a request out of pending, or return None if it already moved.

        The conditional update is the whole point: two supervisors deciding at the same
        instant must produce one decision, not a race won by whoever wrote last.
        """
        ...


class CaseRepository(Protocol):
    async def get(self, tenant_id: str, case_id: str) -> Case | None: ...

    async def upsert(self, case: Case) -> None: ...

    async def find_resumable(self, tenant_id: str, cx_id: str) -> Case | None: ...


class AuditRepository(Protocol):
    async def append(self, record: AuditRecord) -> None: ...

    async def head(self, tenant_id: str) -> tuple[int, str]:
        """The last sequence number and entry hash for a tenant, for chaining."""
        ...

    async def list_recent(
        self, tenant_id: str, *, limit: int, correlation_id: str | None = None
    ) -> list[AuditRecord]: ...


class OutboxRepository(Protocol):
    async def next_sequence(self, tenant_id: str) -> int: ...

    async def add(self, event: DomainEvent) -> None: ...

    async def fetch_pending(self, *, limit: int) -> list[DomainEvent]: ...

    async def mark_published(self, event_ids: Sequence[str]) -> None: ...

    async def replay_since(
        self, tenant_id: str, *, after_sequence: int, limit: int
    ) -> list[DomainEvent]: ...


class IdempotencyRepository(Protocol):
    async def reserve(
        self, tenant_id: str, scope: str, key: str, request_hash: str, *, now: datetime, ttl_s: int
    ) -> tuple[str, dict[str, Any] | None]:
        """Return ``("new"|"in_progress"|"completed", stored_result)``."""
        ...

    async def complete(
        self, tenant_id: str, scope: str, key: str, result: dict[str, Any]
    ) -> None: ...

    async def release(self, tenant_id: str, scope: str, key: str) -> None: ...


class Store(Protocol):
    """Everything the API needs from storage, plus lifecycle and change notification."""

    customers: CustomerRepository
    services: ServiceRepository
    orders: OrderRepository
    invoices: InvoiceRepository
    network: NetworkRepository
    assignments: AssignmentRepository
    tickets: TicketRepository
    callbacks: CallbackRepository
    approvals: ApprovalRepository
    cases: CaseRepository
    audit: AuditRepository
    outbox: OutboxRepository
    idempotency: IdempotencyRepository

    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def ping(self) -> None:
        """Raise when the store cannot serve. Used by readiness."""
        ...

    def watch(self) -> AsyncIterator[DomainEvent]:
        """Live events, in order, resuming where the last watcher stopped."""
        ...
