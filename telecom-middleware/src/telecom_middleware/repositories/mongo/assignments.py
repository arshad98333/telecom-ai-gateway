from __future__ import annotations

from datetime import datetime
from typing import Any

from telecom_middleware.repositories.mongo._shared import NO_ID, session_kwargs
from telecom_middleware.repositories.schema import AGENT_ASSIGNMENTS


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
            **session_kwargs(),
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
