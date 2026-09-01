from __future__ import annotations

from typing import Any

from pymongo import ReturnDocument

from telecom_middleware.domain.models import ApprovalRequest, ApprovalState
from telecom_middleware.repositories.mongo._shared import NO_ID, document, session_kwargs
from telecom_middleware.repositories.schema import APPROVAL_REQUESTS


class MongoApprovalRepository:
    def __init__(self, database: Any) -> None:
        self._collection = database[APPROVAL_REQUESTS.name]

    async def get(self, tenant_id: str, request_id: str) -> ApprovalRequest | None:
        found = await self._collection.find_one(
            {"tenant_id": tenant_id, "request_id": request_id}, projection=NO_ID
        )
        return ApprovalRequest.model_validate(found) if found else None

    async def insert(self, request: ApprovalRequest) -> None:
        await self._collection.insert_one(document(request), **session_kwargs())

    async def list_pending(
        self, tenant_id: str, *, limit: int
    ) -> tuple[list[ApprovalRequest], int]:
        query = {"tenant_id": tenant_id, "state": ApprovalState.PENDING.value}
        total = await self._collection.count_documents(query)
        cursor = self._collection.find(query, projection=NO_ID).sort("created_at", 1).limit(limit)
        return [ApprovalRequest.model_validate(row) async for row in cursor], total

    async def decide(
        self, tenant_id: str, request_id: str, *, decision: dict[str, Any], state: ApprovalState
    ) -> ApprovalRequest | None:
        # "state: pending" in the filter is what makes this safe: the second of two
        # simultaneous decisions matches nothing and returns None.
        found = await self._collection.find_one_and_update(
            {
                "tenant_id": tenant_id,
                "request_id": request_id,
                "state": ApprovalState.PENDING.value,
            },
            {"$set": {"state": state.value, "decision": decision}},
            projection=NO_ID,
            return_document=ReturnDocument.AFTER,
            **session_kwargs(),
        )
        return ApprovalRequest.model_validate(found) if found else None
