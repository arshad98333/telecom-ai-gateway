from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from pymongo import ReturnDocument

from telecom_middleware.domain.models import Customer
from telecom_middleware.repositories.mongo._shared import NO_ID, document, session_kwargs
from telecom_middleware.repositories.schema import CUSTOMERS


class MongoCustomerRepository:
    def __init__(self, database: Any) -> None:
        self._collection = database[CUSTOMERS.name]

    async def get(self, tenant_id: str, cx_id: str) -> Customer | None:
        found = await self._collection.find_one(
            {"tenant_id": tenant_id, "cx_id": cx_id}, projection=NO_ID
        )
        return Customer.model_validate(found) if found else None

    async def upsert(self, customer: Customer) -> None:
        await self._collection.replace_one(
            {"tenant_id": customer.tenant_id, "cx_id": customer.cx_id},
            document(customer),
            upsert=True,
            **session_kwargs(),
        )

    async def record_passcode_attempt(
        self,
        tenant_id: str,
        cx_id: str,
        *,
        success: bool,
        now: datetime,
        max_attempts: int,
        lockout_s: int,
    ) -> Customer | None:
        """One atomic update: increment, decide the lockout, return the new state."""
        if success:
            update: list[dict[str, Any]] | dict[str, Any] = {
                "$set": {
                    "passcode.failed_attempts": 0,
                    "passcode.locked_until": None,
                    "passcode.updated_at": now,
                    "updated_at": now,
                }
            }
        else:
            # An aggregation-pipeline update so the new attempt count and the lockout
            # decision are computed inside the same write, not read out and sent back.
            update = [
                {
                    "$set": {
                        "passcode.failed_attempts": {
                            "$add": [{"$ifNull": ["$passcode.failed_attempts", 0]}, 1]
                        },
                        "passcode.updated_at": now,
                        "updated_at": now,
                    }
                },
                {
                    "$set": {
                        "passcode.locked_until": {
                            "$cond": [
                                {"$gte": ["$passcode.failed_attempts", max_attempts]},
                                now + timedelta(seconds=lockout_s),
                                None,
                            ]
                        }
                    }
                },
            ]
        found = await self._collection.find_one_and_update(
            {"tenant_id": tenant_id, "cx_id": cx_id},
            update,
            projection=NO_ID,
            return_document=ReturnDocument.AFTER,
            **session_kwargs(),
        )
        return Customer.model_validate(found) if found else None
