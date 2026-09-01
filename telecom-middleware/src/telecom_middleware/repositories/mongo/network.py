from __future__ import annotations

from typing import Any

from telecom_middleware.domain.models import NetworkStatus
from telecom_middleware.repositories.mongo._shared import NO_ID, document, session_kwargs
from telecom_middleware.repositories.schema import NETWORK_STATUS


class MongoNetworkRepository:
    def __init__(self, database: Any) -> None:
        self._collection = database[NETWORK_STATUS.name]

    async def get_for_area(self, tenant_id: str, area_ref: str) -> NetworkStatus | None:
        found = await self._collection.find_one(
            {"tenant_id": tenant_id, "area_ref": area_ref}, projection=NO_ID
        )
        return NetworkStatus.model_validate(found) if found else None

    async def upsert(self, status: NetworkStatus) -> None:
        await self._collection.replace_one(
            {"tenant_id": status.tenant_id, "area_ref": status.area_ref},
            document(status),
            upsert=True,
            **session_kwargs(),
        )
