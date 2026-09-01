"""The parts of the MongoDB adapter only a real replica set can exercise.

The store contract suite proves both implementations agree. These do not: they cover the
code that has no in-memory counterpart at all, and that `coverage-mongo.toml` claims is
"all exercised there" while `repositories/mongo.py` sat at 90%.

The largest gap was `watch()` - the change stream. That is the supervisor's live approval
queue and one of the two reasons this system requires a replica set, and nothing was
running it. The rest are the error translations: a driver failure must arrive above this
module as StoreUnavailableError, because nothing above it imports pymongo.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest

from telecom_middleware.domain.errors import StoreUnavailableError
from telecom_middleware.domain.events import DomainEvent, EventType
from telecom_middleware.repositories.mongo import WATCHER_NAME, MongoStore
from telecom_middleware.repositories.schema import STREAM_TOKENS, missing_indexes
from telecom_middleware.repositories.session import current_session, with_session

pytestmark = pytest.mark.mongo

TENANT = "tenant-eu-1"


def _uri() -> str:
    uri = os.environ.get("TELECOM_MW_MONGODB_URI")
    if not uri:
        pytest.fail("TELECOM_MW_MONGODB_URI is not set; these tests need a real replica set")
    return uri


def _event(sequence: int = 1) -> DomainEvent:
    return DomainEvent(
        event_id=f"EVT-{sequence:04d}",
        type=EventType.TICKET_CREATED,
        tenant_id=TENANT,
        sequence=sequence,
        occurred_at=datetime.now(UTC),
        correlation_id="corr-0001",
        subject="tickets/TCK-0001",
    )


@pytest.fixture
async def mongo_store(request: pytest.FixtureRequest) -> AsyncIterator[MongoStore]:
    """A started store on its own database, dropped afterwards by a separate client."""
    from motor.motor_asyncio import AsyncIOMotorClient

    uri = _uri()
    name = f"telecom_adapter_{abs(hash(request.node.nodeid)) % 10_000_000}"
    client: Any = AsyncIOMotorClient(uri, uuidRepresentation="standard")
    store = MongoStore(client, name)
    await store.start()
    try:
        yield store
    finally:
        await store.close()
        cleaner: Any = AsyncIOMotorClient(uri, uuidRepresentation="standard")
        try:
            await cleaner.drop_database(name)
        finally:
            cleaner.close()


# --- the change stream ----------------------------------------------------------------


async def test_the_watcher_delivers_an_event_written_to_the_outbox(
    mongo_store: MongoStore,
) -> None:
    """An insert reaches a subscriber, without anyone polling for it."""
    watcher = mongo_store.watch()
    received: list[DomainEvent] = []

    async def consume() -> None:
        async for event in watcher:
            received.append(event)
            return

    task = asyncio.create_task(consume())
    # The stream has to be open before the write, or there is nothing to observe. There is
    # no callback that says "watching now", so this waits for the cursor to be established.
    await asyncio.sleep(1.0)

    written = _event()
    await mongo_store.outbox.add(written)

    try:
        await asyncio.wait_for(task, timeout=20)
    finally:
        await watcher.aclose()

    assert [event.event_id for event in received] == [written.event_id]
    assert received[0].type is EventType.TICKET_CREATED


async def test_the_watcher_records_where_it_got_to(mongo_store: MongoStore) -> None:
    """The resume token is stored after each event.

    Without it a restart replays from the beginning of the oplog or from nothing at all,
    and a supervisor either sees yesterday's queue or misses an hour of it.
    """
    watcher = mongo_store.watch()

    async def consume() -> None:
        async for _ in watcher:
            return

    task = asyncio.create_task(consume())
    await asyncio.sleep(1.0)
    await mongo_store.outbox.add(_event(sequence=2))
    try:
        await asyncio.wait_for(task, timeout=20)
    finally:
        await watcher.aclose()

    # The update happens after the yield, so give the generator its next step.
    await asyncio.sleep(0.5)
    stored = await mongo_store.database[STREAM_TOKENS.name].find_one({"watcher": WATCHER_NAME})

    assert stored is not None
    assert stored.get("token") is not None


async def test_a_driver_failure_in_the_stream_is_translated(mongo_store: MongoStore) -> None:
    await mongo_store.close()

    with pytest.raises(StoreUnavailableError, match="change stream failed"):
        async for _ in mongo_store.watch():
            pass


# --- the error translations -------------------------------------------------------------


async def test_a_schema_failure_is_translated(mongo_store: MongoStore) -> None:
    await mongo_store.close()

    with pytest.raises(StoreUnavailableError, match="could not apply the database schema"):
        await mongo_store.start()


async def test_a_ping_failure_is_translated(mongo_store: MongoStore) -> None:
    await mongo_store.close()

    with pytest.raises(StoreUnavailableError, match="database did not answer"):
        await mongo_store.ping()


async def test_a_transaction_failure_is_translated(mongo_store: MongoStore) -> None:
    # A write inside the transaction against a closed client fails at the driver, which
    # is the shape of a primary stepping down mid-commit.
    with pytest.raises(StoreUnavailableError, match="transaction failed"):
        async with mongo_store.transaction():
            await mongo_store.close()
            await mongo_store.outbox.add(_event(sequence=3))


# --- the transaction, and the ambient session it sets ------------------------------------


async def test_a_transaction_commits_its_writes_together(mongo_store: MongoStore) -> None:
    async with mongo_store.transaction():
        assert current_session() is not None
        # Every repository call inside the block picks the session up from here.
        assert "session" in with_session({})
        await mongo_store.outbox.add(_event(sequence=4))

    assert current_session() is None
    pending = await mongo_store.outbox.fetch_pending(limit=10)
    assert [event.sequence for event in pending] == [4]


async def test_publishing_is_a_no_op_because_the_change_stream_delivers(
    mongo_store: MongoStore,
) -> None:
    # Present so callers need no branch on which store they hold.
    await mongo_store.publish(_event(sequence=5))

    assert await mongo_store.outbox.fetch_pending(limit=10) == []


# --- the schema helpers -------------------------------------------------------------------


async def test_applying_the_schema_twice_updates_the_validators_in_place(
    mongo_store: MongoStore,
) -> None:
    # The collections exist after the fixture, so this run takes the collMod branch
    # rather than the create branch. It has to be safe: it runs on every boot.
    await mongo_store.start()

    assert await missing_indexes(mongo_store.database) == {}


async def test_a_database_with_no_collections_reports_every_index_as_missing() -> None:
    from motor.motor_asyncio import AsyncIOMotorClient

    client: Any = AsyncIOMotorClient(_uri(), uuidRepresentation="standard")
    try:
        gaps = await missing_indexes(client["telecom_adapter_absent"])
    finally:
        client.close()

    assert gaps, "an empty database is missing every declared index"
    assert "customers" in gaps
