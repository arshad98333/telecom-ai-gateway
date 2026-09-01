from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from pymongo import ReturnDocument

from telecom_middleware.domain.events import DomainEvent
from telecom_middleware.repositories.mongo._shared import NO_ID, session_kwargs
from telecom_middleware.repositories.schema import OUTBOX, TENANT_SEQUENCES


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
            **session_kwargs(),
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
            },
            **session_kwargs(),
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
