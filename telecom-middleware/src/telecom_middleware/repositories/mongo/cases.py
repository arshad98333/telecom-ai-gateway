from __future__ import annotations

from typing import Any

from telecom_middleware.domain.models import Case, CaseStatus
from telecom_middleware.repositories.mongo._shared import NO_ID, document, session_kwargs
from telecom_middleware.repositories.schema import CASES


class MongoCaseRepository:
    def __init__(self, database: Any) -> None:
        self._collection = database[CASES.name]

    async def get(self, tenant_id: str, case_id: str) -> Case | None:
        found = await self._collection.find_one(
            {"tenant_id": tenant_id, "case_id": case_id}, projection=NO_ID
        )
        return Case.model_validate(found) if found else None

    async def upsert(self, case: Case) -> None:
        await self._collection.replace_one(
            {"tenant_id": case.tenant_id, "case_id": case.case_id},
            document(case),
            upsert=True,
            **session_kwargs(),
        )

    async def find_resumable(self, tenant_id: str, cx_id: str) -> Case | None:
        found = await self._collection.find_one(
            {"tenant_id": tenant_id, "cx_id": cx_id, "status": CaseStatus.INTERRUPTED.value},
            projection=NO_ID,
            sort=[("updated_at", -1)],
        )
        return Case.model_validate(found) if found else None
