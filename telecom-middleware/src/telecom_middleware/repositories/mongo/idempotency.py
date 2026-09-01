from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from pymongo.errors import DuplicateKeyError

from telecom_middleware.domain.errors import IdempotencyKeyReusedError
from telecom_middleware.repositories.mongo._shared import NO_ID, session_kwargs
from telecom_middleware.repositories.schema import IDEMPOTENCY_KEYS


class MongoIdempotencyRepository:
    def __init__(self, database: Any) -> None:
        self._collection = database[IDEMPOTENCY_KEYS.name]

    async def reserve(
        self, tenant_id: str, scope: str, key: str, request_hash: str, *, now: datetime, ttl_s: int
    ) -> tuple[str, dict[str, Any] | None]:
        try:
            await self._collection.insert_one(
                {
                    "tenant_id": tenant_id,
                    "scope": scope,
                    "key": key,
                    "hash": request_hash,
                    "result": None,
                    "created_at": now,
                    "expires_at": now + timedelta(seconds=ttl_s),
                },
                **session_kwargs(),
            )
        except DuplicateKeyError:
            pass
        else:
            return "new", None

        found = await self._collection.find_one(
            {"tenant_id": tenant_id, "scope": scope, "key": key}, projection=NO_ID
        )
        if found is None:
            # The TTL swept it between the insert and the read; this attempt is the first.
            return "new", None
        if found["hash"] != request_hash:
            raise IdempotencyKeyReusedError("same key, different request")
        result = found.get("result")
        return ("completed", dict(result)) if result is not None else ("in_progress", None)

    async def complete(self, tenant_id: str, scope: str, key: str, result: dict[str, Any]) -> None:
        await self._collection.update_one(
            {"tenant_id": tenant_id, "scope": scope, "key": key},
            {"$set": {"result": result}},
            **session_kwargs(),
        )

    async def release(self, tenant_id: str, scope: str, key: str) -> None:
        await self._collection.delete_one(
            {"tenant_id": tenant_id, "scope": scope, "key": key, "result": None}
        )
