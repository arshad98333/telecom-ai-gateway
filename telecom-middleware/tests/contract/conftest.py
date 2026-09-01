"""One store fixture, two implementations, one contract.

The in-memory store runs everywhere. The MongoDB store runs only when
``TELECOM_MW_MONGODB_URI`` names a replica set, and its tests are *deselected* by
default rather than skipped — a deselection shows in the run header, a skip hides.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator
from typing import Any

import pytest

MONGO_URI_VARIABLE = "TELECOM_MW_MONGODB_URI"


def _mongo_uri() -> str | None:
    return os.environ.get(MONGO_URI_VARIABLE)


def _new_client(uri: str, **options: Any) -> Any:
    from motor.motor_asyncio import AsyncIOMotorClient

    return AsyncIOMotorClient(uri, uuidRepresentation="standard", **options)


@pytest.fixture
async def cleanup_client() -> AsyncIterator[Any]:
    """A client for dropping test databases, for one test.

    Function-scoped on purpose. A session-scoped async fixture is created inside the
    first test's event loop, and pytest-asyncio closes that loop when that test ends,
    so every later use of the client raised `RuntimeError: Event loop is closed` - which
    is a teardown error on fifty tests and looks like fifty broken tests.

    A client per test is not free against a hosted cluster, so the pool is capped at one
    connection: the tests run in sequence, the client is closed before the next one opens,
    and a free-tier connection ceiling is never in sight.
    """
    uri = _mongo_uri()
    if not uri:
        yield None
        return
    client = _new_client(uri, maxPoolSize=1)
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
async def store(request: pytest.FixtureRequest) -> AsyncIterator[Any]:
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

    # A database per test, so nothing depends on execution order. Named from a digest of
    # the test id rather than hash(), which is salted per process: the same test would
    # otherwise get a different database on every run, so anything left behind by a run
    # that died mid-teardown could never be recognised, only accumulated.
    digest = hashlib.blake2s(request.node.nodeid.encode(), digest_size=6).hexdigest()
    database_name = f"telecom_contract_{digest}"
    client: Any = _new_client(uri)
    mongo = MongoStore(client, database_name)
    await mongo.start()
    try:
        yield mongo
    finally:
        await mongo.close()
        # A second client for the drop, opened here rather than reusing the store's own.
        # One test closes the store on purpose - twice, to prove that is harmless - and
        # closing a store closes its client, so cleanup that reused it would fail on the
        # one test whose entire point is that it should not. One connection is enough:
        # this runs after the test, and the next test has not opened anything yet.
        cleaner: Any = _new_client(uri, maxPoolSize=1)
        try:
            await cleaner.drop_database(database_name)
        finally:
            cleaner.close()
