"""The telecom backend port: our interface, in our shapes, not the vendor's.

Everything the package needs from the outside world for customer data goes through
this one protocol. Two implementations exist: a fixture-driven fake that needs no
network and no account, and an HTTP adapter that calls the middleware API.

The package never connects to MongoDB. Keeping database access behind the service
layer is what centralises validation, authorization and auditing; a second client
reading the same collections would quietly become a second, weaker access path.

Every method takes ``tenant_id`` explicitly. Tenant filtering is applied in the data
path, not checked in a conditional somewhere above it, so a missing filter is a type
error rather than a silent leak.
"""

from __future__ import annotations

from typing import Protocol

from telecom_mcp.domain.schemas import (
    CreateSupportTicketInput,
    CreateSupportTicketOutput,
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
    RequestRefundApprovalInput,
    RequestRefundApprovalOutput,
    ScheduleCallbackInput,
    ScheduleCallbackOutput,
)


class TelecomBackend(Protocol):
    """What the middleware API can do for us. Implementations must be safe to share."""

    async def get_customer_account(
        self, tenant_id: str, request: GetCustomerAccountInput
    ) -> GetCustomerAccountOutput: ...

    async def get_active_services(
        self, tenant_id: str, request: GetActiveServicesInput
    ) -> GetActiveServicesOutput: ...

    async def get_order_status(
        self, tenant_id: str, request: GetOrderStatusInput
    ) -> GetOrderStatusOutput: ...

    async def get_invoice_summary(
        self, tenant_id: str, request: GetInvoiceSummaryInput
    ) -> GetInvoiceSummaryOutput: ...

    async def get_network_status(
        self, tenant_id: str, request: GetNetworkStatusInput
    ) -> GetNetworkStatusOutput: ...

    async def create_support_ticket(
        self, tenant_id: str, request: CreateSupportTicketInput
    ) -> CreateSupportTicketOutput: ...

    async def schedule_callback(
        self, tenant_id: str, request: ScheduleCallbackInput
    ) -> ScheduleCallbackOutput: ...

    async def request_refund_approval(
        self, tenant_id: str, request: RequestRefundApprovalInput
    ) -> RequestRefundApprovalOutput: ...

    async def ping(self) -> None:
        """Readiness probe. Raises when the backend cannot serve."""
        ...
