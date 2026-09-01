from __future__ import annotations

from typing import Any

from telecom_middleware.domain.models import AuditRecord
from telecom_middleware.repositories.mongo._shared import NO_ID, document, session_kwargs
from telecom_middleware.repositories.schema import AUDIT_RECORDS

GENESIS_HASH = "0" * 64


class MongoAuditRepository:
    def __init__(self, database: Any) -> None:
        self._collection = database[AUDIT_RECORDS.name]

    async def append(self, record: AuditRecord) -> None:
        # The unique (tenant_id, seq) index is what makes a gap or a duplicate in the
        # chain a write error rather than a discovery months later.
        await self._collection.insert_one(document(record), **session_kwargs())

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
