from __future__ import annotations

from typing import Any

from telecom_middleware.domain.models import Ticket
from telecom_middleware.repositories.mongo._shared import NO_ID, document, session_kwargs
from telecom_middleware.repositories.schema import TICKETS


class MongoTicketRepository:
    def __init__(self, database: Any) -> None:
        self._collection = database[TICKETS.name]

    async def get(self, tenant_id: str, ticket_id: str) -> Ticket | None:
        found = await self._collection.find_one(
            {"tenant_id": tenant_id, "ticket_id": ticket_id}, projection=NO_ID
        )
        return Ticket.model_validate(found) if found else None

    async def insert(self, ticket: Ticket) -> None:
        await self._collection.insert_one(document(ticket), **session_kwargs())

    async def list_for_customer(
        self, tenant_id: str, cx_id: str, *, limit: int
    ) -> tuple[list[Ticket], int]:
        query = {"tenant_id": tenant_id, "cx_id": cx_id}
        total = await self._collection.count_documents(query)
        cursor = self._collection.find(query, projection=NO_ID).sort("created_at", -1).limit(limit)
        return [Ticket.model_validate(row) async for row in cursor], total
