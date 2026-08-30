"""In-memory store: development, and the offline test suite.

This is not a mock. It holds state, enforces the same tenant isolation, performs the
same conditional updates, and publishes the same events, because it is exercised by the
same contract suite as the MongoDB implementation. When the two disagree, one of them
is wrong and the suite says which.

It is single-process and non-durable, which is exactly why the settings validator
refuses it in production.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator, Sequence
from datetime import datetime, timedelta
from typing import Any, Generic, TypeVar

from telecom_middleware.domain.errors import IdempotencyKeyReusedError
from telecom_middleware.domain.events import DomainEvent
from telecom_middleware.domain.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalState,
    AuditRecord,
    Callback,
    Case,
    CaseStatus,
    Customer,
    Invoice,
    NetworkStatus,
    Order,
    PasscodeState,
    Service,
    Ticket,
)

GENESIS_HASH = "0" * 64

T = TypeVar("T")


class _Collection(Generic[T]):
    """A dictionary keyed by (tenant, id), so no lookup can forget the tenant."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], T] = {}

    def get(self, tenant_id: str, key: str) -> T | None:
        return self._items.get((tenant_id, key))

    def put(self, tenant_id: str, key: str, value: T) -> None:
        self._items[(tenant_id, key)] = value

    def delete(self, tenant_id: str, key: str) -> bool:
        return self._items.pop((tenant_id, key), None) is not None

    def all_for(self, tenant_id: str) -> list[T]:
        return [value for (tenant, _), value in self._items.items() if tenant == tenant_id]


class MemoryCustomerRepository:
    def __init__(self, lock: asyncio.Lock) -> None:
        self._lock = lock
        self.items: _Collection[Customer] = _Collection()

    async def get(self, tenant_id: str, cx_id: str) -> Customer | None:
        return self.items.get(tenant_id, cx_id)

    async def upsert(self, customer: Customer) -> None:
        self.items.put(customer.tenant_id, customer.cx_id, customer)

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
        # Held for the whole read-modify-write: two concurrent guesses must not both
        # see four attempts used and both be allowed a fifth.
        async with self._lock:
            customer = self.items.get(tenant_id, cx_id)
            if customer is None:
                return None
            state = customer.passcode
            if success:
                updated = PasscodeState(
                    hash=state.hash, failed_attempts=0, locked_until=None, updated_at=now
                )
            else:
                attempts = state.failed_attempts + 1
                locked = now + timedelta(seconds=lockout_s) if attempts >= max_attempts else None
                updated = PasscodeState(
                    hash=state.hash,
                    failed_attempts=attempts,
                    locked_until=locked,
                    updated_at=now,
                )
            refreshed = customer.model_copy(update={"passcode": updated, "updated_at": now})
            self.items.put(tenant_id, cx_id, refreshed)
            return refreshed


class MemoryServiceRepository:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], list[Service]] = defaultdict(list)

    async def list_for_customer(
        self, tenant_id: str, cx_id: str, *, limit: int
    ) -> tuple[list[Service], int]:
        found = self.items[(tenant_id, cx_id)]
        return found[:limit], len(found)

    async def upsert(self, service: Service) -> None:
        bucket = self.items[(service.tenant_id, service.cx_id)]
        for index, existing in enumerate(bucket):
            if existing.service_id == service.service_id:
                bucket[index] = service
                return
        bucket.append(service)


class MemoryOrderRepository:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], list[Order]] = defaultdict(list)

    async def list_for_customer(
        self, tenant_id: str, cx_id: str, *, limit: int, order_id: str | None = None
    ) -> tuple[list[Order], int]:
        found = sorted(self.items[(tenant_id, cx_id)], key=lambda o: o.placed_at, reverse=True)
        if order_id is not None:
            found = [order for order in found if order.order_id == order_id]
        return found[:limit], len(found)

    async def upsert(self, order: Order) -> None:
        bucket = self.items[(order.tenant_id, order.cx_id)]
        for index, existing in enumerate(bucket):
            if existing.order_id == order.order_id:
                bucket[index] = order
                return
        bucket.append(order)


class MemoryInvoiceRepository:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], list[Invoice]] = defaultdict(list)

    async def list_for_customer(
        self, tenant_id: str, cx_id: str, *, limit: int, invoice_id: str | None = None
    ) -> tuple[list[Invoice], int]:
        found = sorted(self.items[(tenant_id, cx_id)], key=lambda i: i.issued_on, reverse=True)
        if invoice_id is not None:
            found = [invoice for invoice in found if invoice.invoice_id == invoice_id]
        return found[:limit], len(found)

    async def upsert(self, invoice: Invoice) -> None:
        bucket = self.items[(invoice.tenant_id, invoice.cx_id)]
        for index, existing in enumerate(bucket):
            if existing.invoice_id == invoice.invoice_id:
                bucket[index] = invoice
                return
        bucket.append(invoice)


class MemoryNetworkRepository:
    def __init__(self) -> None:
        self.items: _Collection[NetworkStatus] = _Collection()

    async def get_for_area(self, tenant_id: str, area_ref: str) -> NetworkStatus | None:
        return self.items.get(tenant_id, area_ref)

    async def upsert(self, status: NetworkStatus) -> None:
        self.items.put(status.tenant_id, status.area_ref, status)


class MemoryAssignmentRepository:
    def __init__(self) -> None:
        self.pairs: set[tuple[str, str, str]] = set()

    async def is_assigned(self, tenant_id: str, agent_sub: str, cx_id: str) -> bool:
        return (tenant_id, agent_sub, cx_id) in self.pairs

    async def assign(
        self, tenant_id: str, agent_sub: str, cx_id: str, *, by: str, now: datetime
    ) -> None:
        del by, now
        self.pairs.add((tenant_id, agent_sub, cx_id))

    async def revoke(self, tenant_id: str, agent_sub: str, cx_id: str) -> bool:
        key = (tenant_id, agent_sub, cx_id)
        if key in self.pairs:
            self.pairs.discard(key)
            return True
        return False

    async def list_for_agent(self, tenant_id: str, agent_sub: str) -> list[str]:
        return sorted(
            cx_id
            for tenant, agent, cx_id in self.pairs
            if tenant == tenant_id and agent == agent_sub
        )


class MemoryTicketRepository:
    def __init__(self) -> None:
        self.items: _Collection[Ticket] = _Collection()

    async def get(self, tenant_id: str, ticket_id: str) -> Ticket | None:
        return self.items.get(tenant_id, ticket_id)

    async def insert(self, ticket: Ticket) -> None:
        self.items.put(ticket.tenant_id, ticket.ticket_id, ticket)

    async def list_for_customer(
        self, tenant_id: str, cx_id: str, *, limit: int
    ) -> tuple[list[Ticket], int]:
        found = sorted(
            (t for t in self.items.all_for(tenant_id) if t.cx_id == cx_id),
            key=lambda t: t.created_at,
            reverse=True,
        )
        return found[:limit], len(found)


class MemoryCallbackRepository:
    def __init__(self) -> None:
        self.items: _Collection[Callback] = _Collection()

    async def get(self, tenant_id: str, callback_id: str) -> Callback | None:
        return self.items.get(tenant_id, callback_id)

    async def insert(self, callback: Callback) -> None:
        self.items.put(callback.tenant_id, callback.callback_id, callback)


class MemoryApprovalRepository:
    def __init__(self, lock: asyncio.Lock) -> None:
        self._lock = lock
        self.items: _Collection[ApprovalRequest] = _Collection()

    async def get(self, tenant_id: str, request_id: str) -> ApprovalRequest | None:
        return self.items.get(tenant_id, request_id)

    async def insert(self, request: ApprovalRequest) -> None:
        self.items.put(request.tenant_id, request.request_id, request)

    async def list_pending(
        self, tenant_id: str, *, limit: int
    ) -> tuple[list[ApprovalRequest], int]:
        found = sorted(
            (r for r in self.items.all_for(tenant_id) if r.state is ApprovalState.PENDING),
            key=lambda r: r.created_at,
        )
        return found[:limit], len(found)

    async def decide(
        self, tenant_id: str, request_id: str, *, decision: dict[str, Any], state: ApprovalState
    ) -> ApprovalRequest | None:
        async with self._lock:
            existing = self.items.get(tenant_id, request_id)
            # The conditional part: only a request still pending can be decided, so two
            # supervisors deciding at the same instant produce one decision.
            if existing is None or existing.state is not ApprovalState.PENDING:
                return None
            decided = existing.model_copy(
                update={"state": state, "decision": ApprovalDecision.model_validate(decision)}
            )
            self.items.put(tenant_id, request_id, decided)
            return decided


class MemoryCaseRepository:
    def __init__(self) -> None:
        self.items: _Collection[Case] = _Collection()

    async def get(self, tenant_id: str, case_id: str) -> Case | None:
        return self.items.get(tenant_id, case_id)

    async def upsert(self, case: Case) -> None:
        self.items.put(case.tenant_id, case.case_id, case)

    async def find_resumable(self, tenant_id: str, cx_id: str) -> Case | None:
        candidates = [
            case
            for case in self.items.all_for(tenant_id)
            if case.cx_id == cx_id and case.status is CaseStatus.INTERRUPTED
        ]
        return max(candidates, key=lambda c: c.updated_at, default=None)


class MemoryAuditRepository:
    def __init__(self, lock: asyncio.Lock) -> None:
        self._lock = lock
        self.records: dict[str, list[AuditRecord]] = defaultdict(list)

    async def append(self, record: AuditRecord) -> None:
        async with self._lock:
            self.records[record.tenant_id].append(record)

    async def head(self, tenant_id: str) -> tuple[int, str]:
        records = self.records[tenant_id]
        if not records:
            return 0, GENESIS_HASH
        last = records[-1]
        return last.seq, last.entry_hash

    async def list_recent(
        self, tenant_id: str, *, limit: int, correlation_id: str | None = None
    ) -> list[AuditRecord]:
        found = self.records[tenant_id]
        if correlation_id is not None:
            found = [r for r in found if r.correlation_id == correlation_id]
        return list(reversed(found[-limit:]))


class MemoryOutboxRepository:
    def __init__(self, lock: asyncio.Lock) -> None:
        self._lock = lock
        self.events: list[DomainEvent] = []
        self.published: set[str] = set()
        self._sequences: dict[str, int] = defaultdict(int)

    async def next_sequence(self, tenant_id: str) -> int:
        async with self._lock:
            self._sequences[tenant_id] += 1
            return self._sequences[tenant_id]

    async def add(self, event: DomainEvent) -> None:
        async with self._lock:
            self.events.append(event)

    async def fetch_pending(self, *, limit: int) -> list[DomainEvent]:
        return [e for e in self.events if e.event_id not in self.published][:limit]

    async def mark_published(self, event_ids: Sequence[str]) -> None:
        self.published.update(event_ids)

    async def replay_since(
        self, tenant_id: str, *, after_sequence: int, limit: int
    ) -> list[DomainEvent]:
        return [
            event
            for event in self.events
            if event.tenant_id == tenant_id and event.sequence > after_sequence
        ][:limit]


class MemoryIdempotencyRepository:
    def __init__(self, lock: asyncio.Lock) -> None:
        self._lock = lock
        self.entries: dict[tuple[str, str, str], dict[str, Any]] = {}

    async def reserve(
        self, tenant_id: str, scope: str, key: str, request_hash: str, *, now: datetime, ttl_s: int
    ) -> tuple[str, dict[str, Any] | None]:
        async with self._lock:
            self._evict(now)
            entry_key = (tenant_id, scope, key)
            entry = self.entries.get(entry_key)
            if entry is None:
                self.entries[entry_key] = {
                    "hash": request_hash,
                    "result": None,
                    "expires_at": now + timedelta(seconds=ttl_s),
                }
                return "new", None
            if entry["hash"] != request_hash:
                raise IdempotencyKeyReusedError("same key, different request")
            if entry["result"] is None:
                return "in_progress", None
            return "completed", dict(entry["result"])

    async def complete(self, tenant_id: str, scope: str, key: str, result: dict[str, Any]) -> None:
        async with self._lock:
            entry = self.entries.get((tenant_id, scope, key))
            if entry is not None:
                entry["result"] = result

    async def release(self, tenant_id: str, scope: str, key: str) -> None:
        async with self._lock:
            entry = self.entries.get((tenant_id, scope, key))
            if entry is not None and entry["result"] is None:
                del self.entries[(tenant_id, scope, key)]

    def _evict(self, now: datetime) -> None:
        expired = [key for key, entry in self.entries.items() if entry["expires_at"] <= now]
        for key in expired:
            del self.entries[key]


class MemoryStore:
    """The whole store, in one process."""

    def __init__(self) -> None:
        lock = asyncio.Lock()
        self.customers = MemoryCustomerRepository(lock)
        self.services = MemoryServiceRepository()
        self.orders = MemoryOrderRepository()
        self.invoices = MemoryInvoiceRepository()
        self.network = MemoryNetworkRepository()
        self.assignments = MemoryAssignmentRepository()
        self.tickets = MemoryTicketRepository()
        self.callbacks = MemoryCallbackRepository()
        self.approvals = MemoryApprovalRepository(lock)
        self.cases = MemoryCaseRepository()
        self.audit = MemoryAuditRepository(lock)
        self.outbox = MemoryOutboxRepository(lock)
        self.idempotency = MemoryIdempotencyRepository(lock)
        self._listeners: list[asyncio.Queue[DomainEvent]] = []
        self._healthy = True

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        self._listeners.clear()

    async def ping(self) -> None:
        if not self._healthy:
            raise ConnectionError("memory store marked unhealthy")

    def set_healthy(self, healthy: bool) -> None:
        """Test and development hook, so readiness can be exercised."""
        self._healthy = healthy

    async def publish(self, event: DomainEvent) -> None:
        """Deliver an event to live watchers, in addition to the outbox."""
        for queue in list(self._listeners):
            queue.put_nowait(event)

    async def watch(self) -> AsyncIterator[DomainEvent]:
        queue: asyncio.Queue[DomainEvent] = asyncio.Queue(maxsize=1000)
        self._listeners.append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            if queue in self._listeners:
                self._listeners.remove(queue)
