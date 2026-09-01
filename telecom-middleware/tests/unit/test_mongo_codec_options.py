"""The MongoDB store must read back the timestamps it wrote, zone and all.

These need no server. They pin the codec options on the database handle, which is what
decides whether a stored `datetime` comes back aware or naive, because the version that
needs a replica set only runs in CI and this is the kind of mistake that reaches
production between two runs of it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import bson
from bson.binary import UuidRepresentation

from telecom_middleware.repositories.mongo import MongoStore

URI = "mongodb://127.0.0.1:27017/?replicaSet=rs0"


def _store() -> tuple[Any, MongoStore]:
    from motor.motor_asyncio import AsyncIOMotorClient

    # Motor connects lazily, so constructing this reaches no server.
    client: Any = AsyncIOMotorClient(URI, uuidRepresentation="standard")
    return client, MongoStore(client, "telecom_codec_probe")


def test_the_database_handle_returns_aware_timestamps() -> None:
    client, store = _store()
    try:
        options = store.database.codec_options
        assert options.tz_aware is True
        assert options.tzinfo is UTC
    finally:
        client.close()


def test_a_stored_timestamp_survives_the_round_trip_with_its_zone() -> None:
    # What the driver actually does either side of the wire. Without these options the
    # value comes back naive and every comparison against an aware `now` raises.
    client, store = _store()
    try:
        written = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
        encoded = bson.encode({"created_at": written})

        assert bson.decode(encoded)["created_at"].tzinfo is None
        read_back = bson.decode(encoded, codec_options=store.database.codec_options)["created_at"]

        assert read_back.tzinfo is not None
        assert read_back == written
    finally:
        client.close()


def test_the_uuid_representation_is_not_lost_to_the_codec_options() -> None:
    # CodecOptions replaces the client's settings rather than extending them, so the
    # representation has to be restated or UUIDs silently change encoding.
    client, store = _store()
    try:
        assert store.database.codec_options.uuid_representation is UuidRepresentation.STANDARD
    finally:
        client.close()
