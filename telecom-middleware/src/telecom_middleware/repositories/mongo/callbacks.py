from __future__ import annotations

from typing import Any

from telecom_middleware.domain.models import Callback
from telecom_middleware.repositories.mongo._shared import NO_ID, document, session_kwargs
from telecom_middleware.repositories.schema import CALLBACKS


class MongoCallbackRepository:
    def __init__(self, database: Any) -> None:
        self._collection = database[CALLBACKS.name]

    async def get(self, tenant_id: str, callback_id: str) -> Callback | None:
        found = await self._collection.find_one(
            {"tenant_id": tenant_id, "callback_id": callback_id}, projection=NO_ID
        )
        return Callback.model_validate(found) if found else None

    async def insert(self, callback: Callback) -> None:
        await self._collection.insert_one(document(callback), **session_kwargs())
