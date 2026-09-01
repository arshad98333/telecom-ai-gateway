"""The three repositories a customer's own records are read from: services, orders,
invoices. They share one shape — filter by ``tenant_id`` and ``cx_id``, optionally by a
reference field, sorted and limited — so the shape lives once here and each repository
below only names its collection, its model and its sort/reference fields.
"""

from __future__ import annotations

from typing import Any

from telecom_middleware.domain.models import Invoice, Order, Service
from telecom_middleware.repositories.mongo._shared import NO_ID, document, session_kwargs
from telecom_middleware.repositories.schema import INVOICES, ORDERS, SERVICES


class _CustomerScopedRepository:
    """Shared shape for the per-customer read collections."""

    model: type[Any]
    sort_field: str | None = None
    reference_field: str | None = None

    def __init__(self, database: Any, collection_name: str) -> None:
        self._collection = database[collection_name]

    async def _list(
        self, tenant_id: str, cx_id: str, *, limit: int, reference: str | None
    ) -> tuple[list[Any], int]:
        query: dict[str, Any] = {"tenant_id": tenant_id, "cx_id": cx_id}
        if reference is not None and self.reference_field:
            query[self.reference_field] = reference
        total = await self._collection.count_documents(query)
        cursor = self._collection.find(query, projection=NO_ID)
        if self.sort_field:
            cursor = cursor.sort(self.sort_field, -1)
        found = [self.model.model_validate(row) async for row in cursor.limit(limit)]
        return found, total

    async def upsert(self, model: Any) -> None:
        key = {"tenant_id": model.tenant_id, "cx_id": model.cx_id}
        if self.reference_field:
            key[self.reference_field] = getattr(model, self.reference_field)
        await self._collection.replace_one(key, document(model), upsert=True, **session_kwargs())


class MongoServiceRepository(_CustomerScopedRepository):
    model = Service
    reference_field = "service_id"

    def __init__(self, database: Any) -> None:
        super().__init__(database, SERVICES.name)

    async def list_for_customer(
        self, tenant_id: str, cx_id: str, *, limit: int
    ) -> tuple[list[Service], int]:
        return await self._list(tenant_id, cx_id, limit=limit, reference=None)


class MongoOrderRepository(_CustomerScopedRepository):
    model = Order
    sort_field = "placed_at"
    reference_field = "order_id"

    def __init__(self, database: Any) -> None:
        super().__init__(database, ORDERS.name)

    async def list_for_customer(
        self, tenant_id: str, cx_id: str, *, limit: int, order_id: str | None = None
    ) -> tuple[list[Order], int]:
        return await self._list(tenant_id, cx_id, limit=limit, reference=order_id)


class MongoInvoiceRepository(_CustomerScopedRepository):
    model = Invoice
    sort_field = "issued_on"
    reference_field = "invoice_id"

    def __init__(self, database: Any) -> None:
        super().__init__(database, INVOICES.name)

    async def list_for_customer(
        self, tenant_id: str, cx_id: str, *, limit: int, invoice_id: str | None = None
    ) -> tuple[list[Invoice], int]:
        return await self._list(tenant_id, cx_id, limit=limit, reference=invoice_id)
