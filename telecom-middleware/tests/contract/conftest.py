"""One store fixture, two implementations, one contract.

The in-memory store runs everywhere. The MongoDB store runs only when
``TELECOM_MW_MONGODB_URI`` names a replica set, and its tests are *deselected* by
default rather than skipped — a deselection shows in the run header, a skip hides.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import pytest

MONGO_URI_VARIABLE = "TELECOM_MW_MONGODB_URI"


def _mongo_uri() -> str | None:
    return os.environ.get(MONGO_URI_VARIABLE)


def _new_client(uri: str) -> Any:
    from motor.motor_asyncio import AsyncIOMotorClient

    return AsyncIOMotorClient(uri, uuidRepresentation="standard")


@pytest.fixture(scope="session")
async def cleanup_client() -> AsyncIterator[Any]:
    """One client for dropping test databases, for the whole session.

    Opening a fresh connection per test is barely noticeable against a replica set on
    the same machine and expensive against a hosted one: every handshake crosses the
    internet, and a free-tier cluster has a connection ceiling that a client per test
    walks straight into.
    """
    uri = _mongo_uri()
    if not uri:
        yield None
        return
    client = _new_client(uri)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture(
    params=[
        pytest.param("memory", id="memory"),
        pytest.param("mongodb", id="mongodb", marks=pytest.mark.mongo),
    ]
)
async def store(request: pytest.FixtureRequest, cleanup_client: Any) -> AsyncIterator[Any]:
    """A started, empty store. Each test gets its own database or its own process state."""
    if request.param == "memory":
        from telecom_middleware.repositories.memory import MemoryStore

        memory = MemoryStore()
        await memory.start()
        yield memory
        await memory.close()
        return

    uri = _mongo_uri()
    if not uri:
        pytest.fail(
            f"the mongodb contract tests were selected but {MONGO_URI_VARIABLE} is not set; "
            "run them with a real replica set or leave them deselected"
        )

    from telecom_middleware.repositories.mongo import MongoStore

    # A database per test, so nothing depends on execution order.
    database_name = f"telecom_contract_{abs(hash(request.node.nodeid)) % 10_000_000}"
    client: Any = _new_client(uri)
    mongo = MongoStore(client, database_name)
    await mongo.start()
    try:
        yield mongo
    finally:
        await mongo.close()
        # The drop uses the shared cleanup client, not the store's own. One test closes
        # the store on purpose - twice, to prove that is harmless - and closing a store
        # closes its client, so cleanup that reused it would fail on the one test whose
        # entire point is that it should not.
        await cleanup_client.drop_database(database_name)
