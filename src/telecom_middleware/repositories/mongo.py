"""MongoDB implementation of the storage ports.

Every filter is built from the ``tenant_id`` argument the method received, so tenant
isolation is enforced in the data path rather than checked somewhere above it. Every
projection excludes ``_id``, because a Mongo object id in an API response is an
implementation detail that becomes a contract the moment a client stores it.

Three operations are conditional updates rather than read-then-write, and each is that
way because the read-then-write version has a specific failure:

* a passcode attempt, or two concurrent guesses each see four attempts used and both
  get a fifth;
* deciding an approval, or two supervisors both decide and the last write wins silently;
* reserving an idempotency key, or two identical requests both believe they are first.

Driver errors are translated here. Nothing above this module imports pymongo.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from telecom_middleware.domain.errors import IdempotencyKeyReusedError, StoreUnavailableError
from telecom_middleware.domain.events import DomainEvent
from telecom_middleware.domain.models import (
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
    Service,
    Ticket,
)
from telecom_middleware.repositories.schema import (
    AGENT_ASSIGNMENTS,
    APPROVAL_REQUESTS,
    AUDIT_RECORDS,
    CALLBACKS,
    CASES,
    CUSTOMERS,
    IDEMPOTENCY_KEYS,
    INVOICES,
    NETWORK_STATUS,
    ORDERS,
    OUTBOX,
    SERVICES,
    STREAM_TOKENS,
    TENANT_SEQUENCES,
    TICKETS,
    apply_schema,
)

GENESIS_HASH = "0" * 64
NO_ID = {"_id": 0}
WATCHER_NAME = "outbox-relay"


def _document(model: Any) -> dict[str, Any]:
    """Serialise a model for storage, keeping datetimes as datetimes for BSON."""
    return dict(model.model_dump(mode="python"))


class MongoCustomerRepository:
    def __init__(self, database: Any) -> None:
        self._collection = database[CUSTOMERS.name]

    async def get(self, tenant_id: str, cx_id: str) -> Customer | None:
        found = await self._collection.find_one(
            {"tenant_id": tenant_id, "cx_id": cx_id}, projection=NO_ID
        )
        return Customer.model_validate(found) if found else None

    async def upsert(self, customer: Customer) -> None:
        await self._collection.replace_one(
            {"tenant_id": customer.tenant_id, "cx_id": customer.cx_id},
            _document(customer),
            upsert=True,
        )

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
        """One atomic update: increment, decide the lockout, return the new state."""
        if success:
            update: list[dict[str, Any]] | dict[str, Any] = {
                "$set": {
                    "passcode.failed_attempts": 0,
                    "passcode.locked_until": None,
                    "passcode.updated_at": now,
                    "updated_at": now,
                }
            }
        else:
            # An aggregation-pipeline update so the new attempt count and the lockout
            # decision are computed inside the same write, not read out and sent back.
            update = [
                {
                    "$set": {
                        "passcode.failed_attempts": {
                            "$add": [{"$ifNull": ["$passcode.failed_attempts", 0]}, 1]
                        },
                        "passcode.updated_at": now,
                        "updated_at": now,
                    }
                },
                {
                    "$set": {
                        "passcode.locked_until": {
                            "$cond": [
                                {"$gte": ["$passcode.failed_attempts", max_attempts]},
                                now + timedelta(seconds=lockout_s),
                                None,
                            ]
                        }
                    }
                },
            ]
        found = await self._collection.find_one_and_update(
            {"tenant_id": tenant_id, "cx_id": cx_id},
            update,
            projection=NO_ID,
            return_document=ReturnDocument.AFTER,
        )
        return Customer.model_validate(found) if found else None


class _CustomerScopedRepository:
    """Shared shape for the per-customer read collections."""

    model: type[Any]
    sort_field: str | None = None
    reference_field: str | None = None

    def __init__(self, database: Any, collection_name: str) -> None:
        self._collection = database[collection_name]

    async def _list(
        self, tenant_id: str, cx_id: str, *, limit: int, reference: str | None
    ) -> tuple[list[Any], int]:
        query: dict[str, Any] = {"tenant_id": tenant_id, "cx_id": cx_id}
        if reference is not None and self.reference_field:
            query[self.reference_field] = reference
        total = await self._collection.count_documents(query)
        cursor = self._collection.find(query, projection=NO_ID)
        if self.sort_field:
            cursor = cursor.sort(self.sort_field, -1)
        found = [self.model.model_validate(row) async for row in cursor.limit(limit)]
        return found, total

    async def upsert(self, model: Any) -> None:
        key = {"tenant_id": model.tenant_id, "cx_id": model.cx_id}
        if self.reference_field:
            key[self.reference_field] = getattr(model, self.reference_field)
        await self._collection.replace_one(key, _document(model), upsert=True)


class MongoServiceRepository(_CustomerScopedRepository):
    model = Service
    reference_field = "service_id"

    def __init__(self, database: Any) -> None:
        super().__init__(database, SERVICES.name)

    async def list_for_customer(
        self, tenant_id: str, cx_id: str, *, limit: int
    ) -> tuple[list[Service], int]:
        return await self._list(tenant_id, cx_id, limit=limit, reference=None)


class MongoOrderRepository(_CustomerScopedRepository):
    model = Order
    sort_field = "placed_at"
    reference_field = "order_id"

    def __init__(self, database: Any) -> None:
        super().__init__(database, ORDERS.name)

    async def list_for_customer(
        self, tenant_id: str, cx_id: str, *, limit: int, order_id: str | None = None
    ) -> tuple[list[Order], int]:
        return await self._list(tenant_id, cx_id, limit=limit, reference=order_id)


class MongoInvoiceRepository(_CustomerScopedRepository):
    model = Invoice
    sort_field = "issued_on"
    reference_field = "invoice_id"

    def __init__(self, database: Any) -> None:
        super().__init__(database, INVOICES.name)

    async def list_for_customer(
        self, tenant_id: str, cx_id: str, *, limit: int, invoice_id: str | None = None
    ) -> tuple[list[Invoice], int]:
        return await self._list(tenant_id, cx_id, limit=limit, reference=invoice_id)


class MongoNetworkRepository:
    def __init__(self, database: Any) -> None:
        self._collection = database[NETWORK_STATUS.name]

    async def get_for_area(self, tenant_id: str, area_ref: str) -> NetworkStatus | None:
        found = await self._collection.find_one(
            {"tenant_id": tenant_id, "area_ref": area_ref}, projection=NO_ID
        )
        return NetworkStatus.model_validate(found) if found else None

    async def upsert(self, status: NetworkStatus) -> None:
        await self._collection.replace_one(
            {"tenant_id": status.tenant_id, "area_ref": status.area_ref},
            _document(status),
            upsert=True,
        )


class MongoAssignmentRepository:
    def __init__(self, database: Any) -> None:
        self._collection = database[AGENT_ASSIGNMENTS.name]

    async def is_assigned(self, tenant_id: str, agent_sub: str, cx_id: str) -> bool:
        found = await self._collection.find_one(
            {"tenant_id": tenant_id, "agent_sub": agent_sub, "cx_id": cx_id}, projection=NO_ID
        )
        return found is not None

    async def assign(
        self, tenant_id: str, agent_sub: str, cx_id: str, *, by: str, now: datetime
    ) -> None:
        await self._collection.update_one(
            {"tenant_id": tenant_id, "agent_sub": agent_sub, "cx_id": cx_id},
            {"$set": {"assigned_by": by, "assigned_at": now}},
            upsert=True,
        )

    async def revoke(self, tenant_id: str, agent_sub: str, cx_id: str) -> bool:
        result = await self._collection.delete_one(
            {"tenant_id": tenant_id, "agent_sub": agent_sub, "cx_id": cx_id}
        )
        return bool(result.deleted_count)

    async def list_for_agent(self, tenant_id: str, agent_sub: str) -> list[str]:
        cursor = self._collection.find(
            {"tenant_id": tenant_id, "agent_sub": agent_sub}, projection={"cx_id": 1, "_id": 0}
        )
        return sorted([row["cx_id"] async for row in cursor])


class MongoTicketRepository:
    def __init__(self, database: Any) -> None:
        self._collection = database[TICKETS.name]

    async def get(self, tenant_id: str, ticket_id: str) -> Ticket | None:
        found = await self._collection.find_one(
            {"tenant_id": tenant_id, "ticket_id": ticket_id}, projection=NO_ID
        )
        return Ticket.model_validate(found) if found else None

    async def insert(self, ticket: Ticket) -> None:
        await self._collection.insert_one(_document(ticket))

    async def list_for_customer(
        self, tenant_id: str, cx_id: str, *, limit: int
    ) -> tuple[list[Ticket], int]:
        query = {"tenant_id": tenant_id, "cx_id": cx_id}
        total = await self._collection.count_documents(query)
        cursor = self._collection.find(query, projection=NO_ID).sort("created_at", -1).limit(limit)
        return [Ticket.model_validate(row) async for row in cursor], total


class MongoCallbackRepository:
    def __init__(self, database: Any) -> None:
        self._collection = database[CALLBACKS.name]

    async def get(self, tenant_id: str, callback_id: str) -> Callback | None:
        found = await self._collection.find_one(
            {"tenant_id": tenant_id, "callback_id": callback_id}, projection=NO_ID
        )
        return Callback.model_validate(found) if found else None

    async def insert(self, callback: Callback) -> None:
        await self._collection.insert_one(_document(callback))


class MongoApprovalRepository:
    def __init__(self, database: Any) -> None:
        self._collection = database[APPROVAL_REQUESTS.name]

    async def get(self, tenant_id: str, request_id: str) -> ApprovalRequest | None:
        found = await self._collection.find_one(
            {"tenant_id": tenant_id, "request_id": request_id}, projection=NO_ID
        )
        return ApprovalRequest.model_validate(found) if found else None

    async def insert(self, request: ApprovalRequest) -> None:
        await self._collection.insert_one(_document(request))

    async def list_pending(
        self, tenant_id: str, *, limit: int
    ) -> tuple[list[ApprovalRequest], int]:
        query = {"tenant_id": tenant_id, "state": ApprovalState.PENDING.value}
        total = await self._collection.count_documents(query)
        cursor = self._collection.find(query, projection=NO_ID).sort("created_at", 1).limit(limit)
        return [ApprovalRequest.model_validate(row) async for row in cursor], total

    async def decide(
        self, tenant_id: str, request_id: str, *, decision: dict[str, Any], state: ApprovalState
    ) -> ApprovalRequest | None:
        # "state: pending" in the filter is what makes this safe: the second of two
        # simultaneous decisions matches nothing and returns None.
        found = await self._collection.find_one_and_update(
            {
                "tenant_id": tenant_id,
                "request_id": request_id,
                "state": ApprovalState.PENDING.value,
            },
            {"$set": {"state": state.value, "decision": decision}},
            projection=NO_ID,
            return_document=ReturnDocument.AFTER,
        )
        return ApprovalRequest.model_validate(found) if found else None


class MongoCaseRepository:
    def __init__(self, database: Any) -> None:
        self._collection = database[CASES.name]

    async def get(self, tenant_id: str, case_id: str) -> Case | None:
        found = await self._collection.find_one(
            {"tenant_id": tenant_id, "case_id": case_id}, projection=NO_ID
        )
        return Case.model_validate(found) if found else None

    async def upsert(self, case: Case) -> None:
        await self._collection.replace_one(
            {"tenant_id": case.tenant_id, "case_id": case.case_id}, _document(case), upsert=True
        )

    async def find_resumable(self, tenant_id: str, cx_id: str) -> Case | None:
        found = await self._collection.find_one(
            {"tenant_id": tenant_id, "cx_id": cx_id, "status": CaseStatus.INTERRUPTED.value},
            projection=NO_ID,
            sort=[("updated_at", -1)],
        )
        return Case.model_validate(found) if found else None


class MongoAuditRepository:
    def __init__(self, database: Any) -> None:
        self._collection = database[AUDIT_RECORDS.name]

    async def append(self, record: AuditRecord) -> None:
        # The unique (tenant_id, seq) index is what makes a gap or a duplicate in the
        # chain a write error rather than a discovery months later.
        await self._collection.insert_one(_document(record))

    async def head(self, tenant_id: str) -> tuple[int, str]:
        found = await self._collection.find_one(
            {"tenant_id": tenant_id}, projection=NO_ID, sort=[("seq", -1)]
        )
        if found is None:
            return 0, GENESIS_HASH
        return int(found["seq"]), str(found["entry_hash"])

    async def list_recent(
        self, tenant_id: str, *, limit: int, correlation_id: str | None = None
    ) -> list[AuditRecord]:
        query: dict[str, Any] = {"tenant_id": tenant_id}
        if correlation_id is not None:
            query["correlation_id"] = correlation_id
        cursor = self._collection.find(query, projection=NO_ID).sort("seq", -1).limit(limit)
        return [AuditRecord.model_validate(row) async for row in cursor]


class MongoOutboxRepository:
    def __init__(self, database: Any) -> None:
        self._collection = database[OUTBOX.name]
        self._sequences = database[TENANT_SEQUENCES.name]

    async def next_sequence(self, tenant_id: str) -> int:
        found = await self._sequences.find_one_and_update(
            {"tenant_id": tenant_id},
            {"$inc": {"value": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int(found["value"])

    async def add(self, event: DomainEvent) -> None:
        await self._collection.insert_one(
            {
                "event": event.model_dump(mode="python"),
                "status": "pending",
                "attempts": 0,
                "created_at": datetime.now(UTC),
                "published_at": None,
            }
        )

    async def fetch_pending(self, *, limit: int) -> list[DomainEvent]:
        cursor = (
            self._collection.find({"status": "pending"}, projection=NO_ID)
            .sort("created_at", 1)
            .limit(limit)
        )
        return [DomainEvent.model_validate(row["event"]) async for row in cursor]

    async def mark_published(self, event_ids: Sequence[str]) -> None:
        if not event_ids:
            return
        await self._collection.update_many(
            {"event.event_id": {"$in": list(event_ids)}},
            {"$set": {"status": "published", "published_at": datetime.now(UTC)}},
        )

    async def replay_since(
        self, tenant_id: str, *, after_sequence: int, limit: int
    ) -> list[DomainEvent]:
        cursor = (
            self._collection.find(
                {"event.tenant_id": tenant_id, "event.sequence": {"$gt": after_sequence}},
                projection=NO_ID,
            )
            .sort("event.sequence", 1)
            .limit(limit)
        )
        return [DomainEvent.model_validate(row["event"]) async for row in cursor]


class MongoIdempotencyRepository:
    def __init__(self, database: Any) -> None:
        self._collection = database[IDEMPOTENCY_KEYS.name]

    async def reserve(
        self, tenant_id: str, scope: str, key: str, request_hash: str, *, now: datetime, ttl_s: int
    ) -> tuple[str, dict[str, Any] | None]:
        try:
            await self._collection.insert_one(
                {
                    "tenant_id": tenant_id,
                    "scope": scope,
                    "key": key,
                    "hash": request_hash,
                    "result": None,
                    "created_at": now,
                    "expires_at": now + timedelta(seconds=ttl_s),
                }
            )
        except DuplicateKeyError:
            pass
        else:
            return "new", None

        found = await self._collection.find_one(
            {"tenant_id": tenant_id, "scope": scope, "key": key}, projection=NO_ID
        )
        if found is None:
            # The TTL swept it between the insert and the read; this attempt is the first.
            return "new", None
        if found["hash"] != request_hash:
            raise IdempotencyKeyReusedError("same key, different request")
        result = found.get("result")
        return ("completed", dict(result)) if result is not None else ("in_progress", None)

    async def complete(self, tenant_id: str, scope: str, key: str, result: dict[str, Any]) -> None:
        await self._collection.update_one(
            {"tenant_id": tenant_id, "scope": scope, "key": key}, {"$set": {"result": result}}
        )

    async def release(self, tenant_id: str, scope: str, key: str) -> None:
        await self._collection.delete_one(
            {"tenant_id": tenant_id, "scope": scope, "key": key, "result": None}
        )


class MongoStore:
    """The MongoDB-backed store. One client per process, created at startup."""

    def __init__(self, client: Any, database_name: str) -> None:
        self._client = client
        self._database = client[database_name]
        self.customers = MongoCustomerRepository(self._database)
        self.services = MongoServiceRepository(self._database)
        self.orders = MongoOrderRepository(self._database)
        self.invoices = MongoInvoiceRepository(self._database)
        self.network = MongoNetworkRepository(self._database)
        self.assignments = MongoAssignmentRepository(self._database)
        self.tickets = MongoTicketRepository(self._database)
        self.callbacks = MongoCallbackRepository(self._database)
        self.approvals = MongoApprovalRepository(self._database)
        self.cases = MongoCaseRepository(self._database)
        self.audit = MongoAuditRepository(self._database)
        self.outbox = MongoOutboxRepository(self._database)
        self.idempotency = MongoIdempotencyRepository(self._database)

    @property
    def database(self) -> Any:
        return self._database

    async def start(self) -> None:
        """Create collections, validators and indexes. Idempotent, so it runs every boot."""
        try:
            await apply_schema(self._database)
        except PyMongoError as exc:
            raise StoreUnavailableError("could not apply the database schema") from exc

    async def close(self) -> None:
        self._client.close()

    async def ping(self) -> None:
        try:
            await self._database.command("ping")
        except PyMongoError as exc:
            raise StoreUnavailableError("database did not answer") from exc

    async def publish(self, event: DomainEvent) -> None:
        """No-op: the change stream is the delivery mechanism, not this method.

        Present so the two stores share one interface and the calling code has no
        branch on which one it is talking to.
        """
        del event

    async def watch(self) -> AsyncIterator[DomainEvent]:
        """Tail the outbox with a resumable change stream.

        The resume token is stored after each event, so a restart continues from the
        last event delivered rather than replaying a day or losing an hour.
        """
        tokens = self._database[STREAM_TOKENS.name]
        stored = await tokens.find_one({"watcher": WATCHER_NAME})
        resume_after = stored.get("token") if stored else None

        pipeline = [{"$match": {"operationType": "insert"}}]
        try:
            async with self._database[OUTBOX.name].watch(
                pipeline, resume_after=resume_after, full_document="updateLookup"
            ) as stream:
                async for change in stream:
                    document = change.get("fullDocument") or {}
                    event_body = document.get("event")
                    if event_body is None:
                        continue
                    yield DomainEvent.model_validate(event_body)
                    await tokens.update_one(
                        {"watcher": WATCHER_NAME},
                        {"$set": {"token": change["_id"], "at": datetime.now(UTC)}},
                        upsert=True,
                    )
        except PyMongoError as exc:
            raise StoreUnavailableError("change stream failed") from exc
