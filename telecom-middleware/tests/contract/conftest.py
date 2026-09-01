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

    from motor.motor_asyncio import AsyncIOMotorClient

    from telecom_middleware.repositories.mongo import MongoStore

    # A database per test worker and test, so nothing depends on execution order.
    database_name = f"telecom_contract_{abs(hash(request.node.nodeid)) % 10_000_000}"
    client: Any = AsyncIOMotorClient(uri, uuidRepresentation="standard")
    mongo = MongoStore(client, database_name)
    await mongo.start()
    try:
        yield mongo
    finally:
        await mongo.close()
        # A second client for the drop. One test closes the store on purpose - twice, to
        # prove that is harmless - and closing a store closes its client, so cleanup that
        # reuses it fails on the one test whose whole point is that it should not.
        cleaner: Any = AsyncIOMotorClient(uri, uuidRepresentation="standard")
        try:
            await cleaner.drop_database(database_name)
        finally:
            cleaner.close()
