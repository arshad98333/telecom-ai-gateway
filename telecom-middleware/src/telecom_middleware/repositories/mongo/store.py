"""``MongoStore``: one client per process, wiring together every per-collection
repository in this package, plus the transaction and change-stream machinery that spans
collections.

Driver errors are translated here. Nothing above this package imports pymongo.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from bson.binary import UuidRepresentation
from bson.codec_options import CodecOptions
from pymongo.errors import PyMongoError

from telecom_middleware.domain.errors import StoreUnavailableError
from telecom_middleware.domain.events import DomainEvent
from telecom_middleware.repositories.mongo.approvals import MongoApprovalRepository
from telecom_middleware.repositories.mongo.assignments import MongoAssignmentRepository
from telecom_middleware.repositories.mongo.audit import MongoAuditRepository
from telecom_middleware.repositories.mongo.callbacks import MongoCallbackRepository
from telecom_middleware.repositories.mongo.cases import MongoCaseRepository
from telecom_middleware.repositories.mongo.customer_scoped import (
    MongoInvoiceRepository,
    MongoOrderRepository,
    MongoServiceRepository,
)
from telecom_middleware.repositories.mongo.customers import MongoCustomerRepository
from telecom_middleware.repositories.mongo.idempotency import MongoIdempotencyRepository
from telecom_middleware.repositories.mongo.network import MongoNetworkRepository
from telecom_middleware.repositories.mongo.outbox import MongoOutboxRepository
from telecom_middleware.repositories.mongo.tickets import MongoTicketRepository
from telecom_middleware.repositories.schema import OUTBOX, STREAM_TOKENS, apply_schema
from telecom_middleware.repositories.session import reset_session, set_session

WATCHER_NAME = "outbox-relay"


class MongoStore:
    """The MongoDB-backed store. One client per process, created at startup."""

    #: BSON dates carry no zone, so pymongo returns naive datetimes unless it is told
    #: otherwise. Every timestamp this system stores is UTC and every comparison it makes
    #: is against an aware one, so a naive value read back does not merely look wrong: it
    #: raises `can't compare offset-naive and offset-aware datetimes` the first time a
    #: lockout is checked. Setting it here rather than on the client means no caller can
    #: construct a store that reads naive timestamps.
    #:
    #: The uuid representation is restated because CodecOptions replaces the client's
    #: rather than extending it, and dropping it would change how UUIDs are encoded.
    _CODEC_OPTIONS: CodecOptions[dict[str, Any]] = CodecOptions(
        tz_aware=True,
        tzinfo=UTC,
        uuid_representation=UuidRepresentation.STANDARD,
    )

    def __init__(self, client: Any, database_name: str) -> None:
        self._client = client
        self._database = client.get_database(database_name, codec_options=self._CODEC_OPTIONS)
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

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """Run a block so its writes commit together, or not at all.

        This is what makes the outbox trustworthy: an event cannot exist for a change
        that rolled back, and a change cannot commit without its event. It needs a
        replica set, which is also what makes change streams available - the two
        requirements are the same requirement.
        """
        async with await self._client.start_session() as session:
            token = set_session(session)
            try:
                async with session.start_transaction():
                    yield
            except PyMongoError as exc:
                raise StoreUnavailableError("transaction failed") from exc
            finally:
                reset_session(token)

    async def publish(self, event: DomainEvent) -> None:
        """No-op: the change stream is the delivery mechanism, not this method.

        Present so the two stores share one interface and the calling code has no
        branch on which one it is talking to.
        """
        del event

    async def watch(self) -> AsyncGenerator[DomainEvent, None]:
        """Tail the outbox with a resumable change stream.

        The resume token is stored after each event, so a restart continues from the
        last event delivered rather than replaying a day or losing an hour.
        """
        tokens = self._database[STREAM_TOKENS.name]
        pipeline = [{"$match": {"operationType": "insert"}}]
        try:
            # Reading the resume token is a database call like any other, so it belongs
            # inside the translation. Left outside it, a driver failure here escaped as a
            # raw pymongo error - and nothing above this package imports pymongo.
            stored = await tokens.find_one({"watcher": WATCHER_NAME})
            resume_after = stored.get("token") if stored else None

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
