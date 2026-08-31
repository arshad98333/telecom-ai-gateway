"""A fake middleware API, built from committed fixtures.

This is what lets the whole suite run with the network off, no credentials and no
account, and it is what a developer runs against locally. It is not a mock: it holds
state, enforces tenant isolation the same way the real service must, and can be told
to fail in the shapes the real service fails in, so failure paths are genuinely tested
rather than imagined.

Keeping the fake honest is a maintenance obligation. When the middleware API's real
response for a case is recorded under ``tests/fixtures``, the fake is corrected to
match it, not the other way round.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from importlib import resources
from typing import Any

from telecom_mcp.domain.errors import BackendError, BackendTimeoutError, NotFoundError
from telecom_mcp.domain.ports import Clock, IdGenerator
from telecom_mcp.domain.schemas import (
    ActiveService,
    CreateSupportTicketInput,
    CreateSupportTicketOutput,
    Currency,
    GetActiveServicesInput,
    GetActiveServicesOutput,
    GetCustomerAccountInput,
    GetCustomerAccountOutput,
    GetInvoiceSummaryInput,
    GetInvoiceSummaryOutput,
    GetNetworkStatusInput,
    GetNetworkStatusOutput,
    GetOrderStatusInput,
    GetOrderStatusOutput,
    InvoiceSummary,
    Order,
    RequestRefundApprovalInput,
    RequestRefundApprovalOutput,
    ScheduleCallbackInput,
    ScheduleCallbackOutput,
)

SEED_RESOURCE = "seed.json"

#: How long a low-risk write stays cancellable, matching the service-level policy.
CANCELLATION_WINDOW_S = 900


def load_seed() -> dict[str, Any]:
    """Read the committed seed data. Packaged with the wheel so it always resolves."""
    text = (
        resources.files("telecom_mcp.adapters.fixtures").joinpath(SEED_RESOURCE).read_text("utf-8")
    )
    document: dict[str, Any] = json.loads(text)
    return document


@dataclass
class FailureInjection:
    """How the fake should misbehave, so failure paths get exercised on purpose."""

    #: Raise a timeout on the next N calls, then behave normally.
    timeouts: int = 0
    #: Raise a generic backend failure on the next N calls.
    failures: int = 0
    #: Return a response the schema cannot parse, once.
    malformed_once: bool = False
    #: Fail readiness until cleared.
    unhealthy: bool = False
    calls: list[str] = field(default_factory=list)


class FakeTelecomBackend:
    """In-memory implementation of the telecom backend port."""

    def __init__(
        self,
        *,
        clock: Clock,
        id_generator: IdGenerator,
        seed: dict[str, Any] | None = None,
        failures: FailureInjection | None = None,
    ) -> None:
        self._data = seed if seed is not None else load_seed()
        self._clock = clock
        self._ids = id_generator
        self.failures = failures or FailureInjection()
        self.tickets: dict[str, dict[str, Any]] = {}
        self.callbacks: dict[str, dict[str, Any]] = {}
        self.refund_requests: dict[str, dict[str, Any]] = {}

    # --- reads ----------------------------------------------------------------------

    async def get_customer_account(
        self, tenant_id: str, request: GetCustomerAccountInput
    ) -> GetCustomerAccountOutput:
        record = self._customer(tenant_id, request.cx_id, "get_customer_account")
        return GetCustomerAccountOutput.model_validate(record["account"])

    async def get_active_services(
        self, tenant_id: str, request: GetActiveServicesInput
    ) -> GetActiveServicesOutput:
        record = self._customer(tenant_id, request.cx_id, "get_active_services")
        services = [ActiveService.model_validate(item) for item in record["services"]]
        page = services[: request.limit]
        return GetActiveServicesOutput(
            services=page, total_count=len(services), truncated=len(page) < len(services)
        )

    async def get_order_status(
        self, tenant_id: str, request: GetOrderStatusInput
    ) -> GetOrderStatusOutput:
        record = self._customer(tenant_id, request.cx_id, "get_order_status")
        orders = [Order.model_validate(item) for item in record["orders"]]
        if request.order_id is not None:
            orders = [order for order in orders if order.order_id == request.order_id]
            if not orders:
                raise NotFoundError(operation="get_order_status")
        page = orders[: request.limit]
        return GetOrderStatusOutput(
            orders=page, total_count=len(orders), truncated=len(page) < len(orders)
        )

    async def get_invoice_summary(
        self, tenant_id: str, request: GetInvoiceSummaryInput
    ) -> GetInvoiceSummaryOutput:
        record = self._customer(tenant_id, request.cx_id, "get_invoice_summary")
        invoices = [InvoiceSummary.model_validate(item) for item in record["invoices"]]
        if request.invoice_id is not None:
            invoices = [item for item in invoices if item.invoice_id == request.invoice_id]
            if not invoices:
                raise NotFoundError(operation="get_invoice_summary")
        page = invoices[: request.limit]
        outstanding = sum((item.outstanding for item in invoices), Decimal("0.00"))
        currency = invoices[0].currency if invoices else Currency.GBP
        return GetInvoiceSummaryOutput(
            invoices=page,
            total_outstanding=outstanding,
            currency=currency,
            truncated=len(page) < len(invoices),
        )

    async def get_network_status(
        self, tenant_id: str, request: GetNetworkStatusInput
    ) -> GetNetworkStatusOutput:
        record = self._customer(tenant_id, request.cx_id, "get_network_status")
        return GetNetworkStatusOutput.model_validate(record["network"])

    # --- writes ---------------------------------------------------------------------

    async def create_support_ticket(
        self, tenant_id: str, request: CreateSupportTicketInput
    ) -> CreateSupportTicketOutput:
        self._customer(tenant_id, request.cx_id, "create_support_ticket")
        now = self._clock.now()
        ticket_id = f"TCK-{self._ids.new_id()}"
        self.tickets[ticket_id] = {
            "tenant_id": tenant_id,
            "cx_id": request.cx_id,
            "category": str(request.category),
            "priority": str(request.priority),
            "idempotency_key": request.idempotency_key,
        }
        return CreateSupportTicketOutput(
            ticket_id=ticket_id,
            state="open",
            created_at=now,
            cancellable_until=now + timedelta(seconds=CANCELLATION_WINDOW_S),
        )

    async def schedule_callback(
        self, tenant_id: str, request: ScheduleCallbackInput
    ) -> ScheduleCallbackOutput:
        self._customer(tenant_id, request.cx_id, "schedule_callback")
        callback_id = f"CB-{self._ids.new_id()}"
        self.callbacks[callback_id] = {
            "tenant_id": tenant_id,
            "cx_id": request.cx_id,
            "idempotency_key": request.idempotency_key,
        }
        return ScheduleCallbackOutput(
            callback_id=callback_id,
            scheduled_for=request.preferred_date,
            window=request.window,
            cancellable_until=request.preferred_date,
        )

    async def request_refund_approval(
        self, tenant_id: str, request: RequestRefundApprovalInput
    ) -> RequestRefundApprovalOutput:
        self._customer(tenant_id, request.cx_id, "request_refund_approval")
        approval_id = f"APR-{self._ids.new_id()}"
        self.refund_requests[approval_id] = {
            "tenant_id": tenant_id,
            "cx_id": request.cx_id,
            "amount": str(request.amount),
            "currency": str(request.currency),
            "idempotency_key": request.idempotency_key,
        }
        return RequestRefundApprovalOutput(
            approval_request_id=approval_id,
            state="pending_approval",
            submitted_at=self._clock.now(),
            approver_role="supervisor_approver",
        )

    async def ping(self) -> None:
        if self.failures.unhealthy:
            raise BackendError(operation="ping")

    # --- internals ------------------------------------------------------------------

    def _customer(self, tenant_id: str, cx_id: str, operation: str) -> dict[str, Any]:
        self._maybe_fail(operation)
        tenant = self._data.get("tenants", {}).get(tenant_id)
        if tenant is None:
            # An unknown tenant is indistinguishable from an unknown customer on
            # purpose: neither answer should confirm that the other tenant exists.
            raise NotFoundError(operation=operation)
        record = tenant.get("customers", {}).get(cx_id)
        if record is None:
            raise NotFoundError(operation=operation)
        if self.failures.malformed_once:
            self.failures.malformed_once = False
            return {
                "account": {"unexpected": "shape"},
                "services": [],
                "orders": [],
                "invoices": [],
                "network": {},
            }
        return dict(record)

    def _maybe_fail(self, operation: str) -> None:
        self.failures.calls.append(operation)
        if self.failures.timeouts > 0:
            self.failures.timeouts -= 1
            raise BackendTimeoutError(operation=operation)
        if self.failures.failures > 0:
            self.failures.failures -= 1
            raise BackendError(operation=operation)
