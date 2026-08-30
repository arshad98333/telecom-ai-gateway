"""HTTP adapter for the telecom middleware API.

The middleware API is the only path to customer data; MongoDB is behind it and this
package never speaks to it directly. Every request carries the tenant explicitly so
the filter is applied by the service that owns the data.

Three things are non-negotiable here and each has a test that makes the outside world
fail. Every call has a connect timeout and a read timeout. Every response is validated
against our own schema before it leaves this module, so a dependency that starts
returning a valid response of an unexpected shape produces a clean, non-retryable
error rather than a confusing failure three layers up. And the client is created once
per process with a bounded connection pool, never per request.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self, TypeVar

import httpx
from pydantic import ValidationError

from telecom_mcp.adapters import translation
from telecom_mcp.adapters.call_context import current_call
from telecom_mcp.domain.errors import (
    AuthorizationError,
    BackendBadResponseError,
    BackendError,
    BackendTimeoutError,
    NotFoundError,
    RateLimitedError,
)
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
    ToolOutput,
)

TENANT_HEADER = "X-Tenant-Id"
IDEMPOTENCY_HEADER = "Idempotency-Key"
CORRELATION_HEADER = "X-Correlation-Id"
SERVICE_CREDENTIAL_HEADER = "X-Service-Authorization"
#: Responses larger than this are refused rather than loaded into memory.
MAX_RESPONSE_BYTES = 1_000_000

M = TypeVar("M", bound=ToolOutput)


def build_client(
    *,
    base_url: str,
    api_key: str,
    connect_timeout_s: float,
    read_timeout_s: float,
    max_connections: int,
) -> httpx.AsyncClient:
    """Create the one client this process will use. Called at startup, never per call."""
    return httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        timeout=httpx.Timeout(
            connect=connect_timeout_s,
            read=read_timeout_s,
            write=connect_timeout_s,
            pool=connect_timeout_s,
        ),
        limits=httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max(1, max_connections // 2),
        ),
        headers={
            # This service's own credential proves *which service* is calling. It is
            # deliberately not the Authorization header: that carries the customer's
            # token, because the middleware authorizes the person, not the robot.
            SERVICE_CREDENTIAL_HEADER: f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "telecom-mcp-tools/1.0",
        },
        follow_redirects=False,  # a redirect off our base URL is not something to follow
    )


class HttpTelecomBackend:
    """Calls the middleware API. Shareable across concurrent requests."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- reads ----------------------------------------------------------------------

    async def get_customer_account(
        self, tenant_id: str, request: GetCustomerAccountInput
    ) -> GetCustomerAccountOutput:
        payload = await self._get(f"/customers/{request.cx_id}", tenant_id, "get_customer_account")
        return self._parse(
            GetCustomerAccountOutput,
            self._translate(translation.account, payload, "get_customer_account"),
            "get_customer_account",
        )

    async def get_active_services(
        self, tenant_id: str, request: GetActiveServicesInput
    ) -> GetActiveServicesOutput:
        payload = await self._get(
            f"/customers/{request.cx_id}/services",
            tenant_id,
            "get_active_services",
            params={"limit": request.limit},
        )
        return self._parse(
            GetActiveServicesOutput,
            self._translate(translation.services, payload, "get_active_services"),
            "get_active_services",
        )

    async def get_order_status(
        self, tenant_id: str, request: GetOrderStatusInput
    ) -> GetOrderStatusOutput:
        params: dict[str, Any] = {"limit": request.limit}
        if request.order_id is not None:
            params["order_id"] = request.order_id
        payload = await self._get(
            f"/customers/{request.cx_id}/orders", tenant_id, "get_order_status", params=params
        )
        return self._parse(
            GetOrderStatusOutput,
            self._translate(translation.orders, payload, "get_order_status"),
            "get_order_status",
        )

    async def get_invoice_summary(
        self, tenant_id: str, request: GetInvoiceSummaryInput
    ) -> GetInvoiceSummaryOutput:
        params: dict[str, Any] = {"limit": request.limit}
        if request.invoice_id is not None:
            params["invoice_id"] = request.invoice_id
        payload = await self._get(
            f"/customers/{request.cx_id}/invoices",
            tenant_id,
            "get_invoice_summary",
            params=params,
        )
        return self._parse(
            GetInvoiceSummaryOutput,
            self._translate(translation.invoices, payload, "get_invoice_summary"),
            "get_invoice_summary",
        )

    async def get_network_status(
        self, tenant_id: str, request: GetNetworkStatusInput
    ) -> GetNetworkStatusOutput:
        params: dict[str, Any] = {}
        if request.service_id is not None:
            params["service_id"] = request.service_id
        payload = await self._get(
            f"/customers/{request.cx_id}/network", tenant_id, "get_network_status", params=params
        )
        return self._parse(
            GetNetworkStatusOutput,
            self._translate(translation.network, payload, "get_network_status"),
            "get_network_status",
        )

    # --- writes ---------------------------------------------------------------------

    async def create_support_ticket(
        self, tenant_id: str, request: CreateSupportTicketInput
    ) -> CreateSupportTicketOutput:
        payload = await self._post(
            f"/customers/{request.cx_id}/tickets",
            tenant_id,
            "create_support_ticket",
            body=request.model_dump(mode="json", exclude={"idempotency_key"}),
            idempotency_key=request.idempotency_key,
        )
        return self._parse(
            CreateSupportTicketOutput,
            self._translate(translation.ticket, payload, "create_support_ticket"),
            "create_support_ticket",
        )

    async def schedule_callback(
        self, tenant_id: str, request: ScheduleCallbackInput
    ) -> ScheduleCallbackOutput:
        payload = await self._post(
            f"/customers/{request.cx_id}/callbacks",
            tenant_id,
            "schedule_callback",
            body=request.model_dump(mode="json", exclude={"idempotency_key"}),
            idempotency_key=request.idempotency_key,
        )
        return self._parse(
            ScheduleCallbackOutput,
            self._translate(translation.callback, payload, "schedule_callback"),
            "schedule_callback",
        )

    async def request_refund_approval(
        self, tenant_id: str, request: RequestRefundApprovalInput
    ) -> RequestRefundApprovalOutput:
        payload = await self._post(
            f"/customers/{request.cx_id}/refund-approvals",
            tenant_id,
            "request_refund_approval",
            body=self._translate(
                translation.refund_request_body,
                request.model_dump(mode="json", exclude={"idempotency_key"}),
                "request_refund_approval",
            ),
            idempotency_key=request.idempotency_key,
        )
        return self._parse(
            RequestRefundApprovalOutput,
            self._translate(translation.refund_approval, payload, "request_refund_approval"),
            "request_refund_approval",
        )

    async def ping(self) -> None:
        try:
            response = await self._client.get("/health")
        except httpx.HTTPError as exc:
            raise BackendError(operation="ping") from exc
        if response.status_code >= 500:
            raise BackendError(operation="ping")

    # --- internals ------------------------------------------------------------------

    async def _get(
        self,
        path: str,
        tenant_id: str,
        operation: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        return await self._send("GET", path, tenant_id, operation, params=params)

    async def _post(
        self,
        path: str,
        tenant_id: str,
        operation: str,
        *,
        body: dict[str, Any],
        idempotency_key: str,
    ) -> Any:
        return await self._send(
            "POST",
            path,
            tenant_id,
            operation,
            json=body,
            extra_headers={IDEMPOTENCY_HEADER: idempotency_key},
        )

    async def _send(
        self,
        method: str,
        path: str,
        tenant_id: str,
        operation: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        call = current_call()
        headers = {TENANT_HEADER: tenant_id, **(extra_headers or {})}
        if call is not None:
            # The customer's own token. Without it the middleware refuses the call,
            # which is the property that makes a compromised service credential useless.
            headers["Authorization"] = f"Bearer {call.token}"
            headers[CORRELATION_HEADER] = call.correlation_id
        try:
            response = await self._client.request(
                method, path, params=params, json=json, headers=headers
            )
        except httpx.TimeoutException as exc:
            raise BackendTimeoutError(operation=operation) from exc
        except httpx.HTTPError as exc:
            # Connection refused, DNS failure, protocol error: all the same to a caller.
            raise BackendError(operation=operation) from exc

        self._raise_for_status(response, operation)
        return self._decode(response, operation)

    @staticmethod
    def _raise_for_status(response: httpx.Response, operation: str) -> None:
        status = response.status_code
        if status < 400:
            return
        if status in (401, 403):
            # The middleware refused us, not the customer. Never surfaced as a customer
            # permission error, which would be a misleading answer.
            raise AuthorizationError(operation=operation)
        if status == 404:
            raise NotFoundError(operation=operation)
        if status == 429:
            raise RateLimitedError(operation=operation)
        if status < 500:
            raise BackendBadResponseError(operation=operation)
        raise BackendError(operation=operation)

    @staticmethod
    def _decode(response: httpx.Response, operation: str) -> Any:
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise BackendBadResponseError(operation=operation)
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type:
            # A status code that says success and a body that is an HTML error page is
            # a real thing that happens behind proxies.
            raise BackendBadResponseError(operation=operation)
        try:
            return response.json()
        except ValueError as exc:
            raise BackendBadResponseError(operation=operation) from exc

    @staticmethod
    def _parse(model: type[M], payload: Any, operation: str) -> M:
        try:
            return model.model_validate(payload)
        except ValidationError as exc:
            raise BackendBadResponseError(operation=operation) from exc

    @staticmethod
    def _translate(translate: Any, payload: Any, operation: str) -> Any:
        """Run a translation, turning any surprise in the payload into our own error."""
        try:
            return translate(payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise BackendBadResponseError(operation=operation) from exc
