"""The HTTP adapter is tested by making the outside world fail, not by mocking it away."""

import json
from typing import Any

import httpx
import pytest
import respx

from telecom_mcp.adapters.http_backend import (
    IDEMPOTENCY_HEADER,
    TENANT_HEADER,
    HttpTelecomBackend,
    build_client,
)
from telecom_mcp.domain.errors import (
    AuthorizationError,
    BackendBadResponseError,
    BackendError,
    BackendTimeoutError,
    NotFoundError,
    RateLimitedError,
)
from telecom_mcp.domain.schemas import CreateSupportTicketInput, GetCustomerAccountInput

BASE = "https://middleware.example.invalid/api/v1"
TENANT = "tenant-eu-1"

ACCOUNT = {
    "cx_id": "CX-1234",
    "account_status": "active",
    "account_type": "consumer",
    "display_name": "J. Okonkwo",
    "customer_since": "2021-03-14T00:00:00Z",
    "billing_postcode_suffix": "4AB",
    "open_case_count": 1,
}


def backend() -> HttpTelecomBackend:
    return HttpTelecomBackend(
        build_client(
            base_url=BASE,
            api_key="dummy-key",
            connect_timeout_s=2.0,
            read_timeout_s=8.0,
            max_connections=10,
        )
    )


@respx.mock
async def test_a_successful_read_is_parsed_into_our_own_shape() -> None:
    respx.get(f"{BASE}/customers/CX-1234").mock(return_value=httpx.Response(200, json=ACCOUNT))

    async with backend() as client:
        account = await client.get_customer_account(
            TENANT, GetCustomerAccountInput(cx_id="CX-1234")
        )

    assert account.display_name == "J. Okonkwo"


@respx.mock
async def test_the_tenant_is_sent_on_every_request_so_filtering_happens_at_the_owner() -> None:
    route = respx.get(f"{BASE}/customers/CX-1234").mock(
        return_value=httpx.Response(200, json=ACCOUNT)
    )

    async with backend() as client:
        await client.get_customer_account(TENANT, GetCustomerAccountInput(cx_id="CX-1234"))

    assert route.calls.last.request.headers[TENANT_HEADER] == TENANT


@respx.mock
async def test_a_write_forwards_the_idempotency_key_as_a_header_not_a_body_field() -> None:
    route = respx.post(f"{BASE}/customers/CX-1234/tickets").mock(
        return_value=httpx.Response(
            201,
            json={
                "ticket_id": "TCK-1",
                "cx_id": "CX-1234",
                "category": "billing",
                "subject": "s",
                "priority": "normal",
                "state": "open",
                "created_at": "2026-08-30T12:00:00Z",
                "cancellable_until": "2026-08-30T12:15:00Z",
                "deduplicated": False,
            },
        )
    )

    async with backend() as client:
        await client.create_support_ticket(
            TENANT,
            CreateSupportTicketInput(
                cx_id="CX-1234",
                category="billing",
                subject="s",
                description="d",
                idempotency_key="idem-0000-0001",
            ),
        )

    request = route.calls.last.request
    assert request.headers[IDEMPOTENCY_HEADER] == "idem-0000-0001"
    assert b"idempotency_key" not in request.content


@respx.mock
async def test_a_timeout_becomes_a_retryable_backend_timeout() -> None:
    respx.get(f"{BASE}/customers/CX-1234").mock(side_effect=httpx.ReadTimeout("too slow"))

    async with backend() as client:
        with pytest.raises(BackendTimeoutError) as caught:
            await client.get_customer_account(TENANT, GetCustomerAccountInput(cx_id="CX-1234"))

    assert caught.value.retryable is True


@respx.mock
async def test_a_refused_connection_becomes_a_backend_error() -> None:
    respx.get(f"{BASE}/customers/CX-1234").mock(side_effect=httpx.ConnectError("refused"))

    async with backend() as client:
        with pytest.raises(BackendError):
            await client.get_customer_account(TENANT, GetCustomerAccountInput(cx_id="CX-1234"))


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, AuthorizationError),
        (403, AuthorizationError),
        (404, NotFoundError),
        (429, RateLimitedError),
        (400, BackendBadResponseError),
        (422, BackendBadResponseError),
        (500, BackendError),
        (503, BackendError),
    ],
)
@respx.mock
async def test_every_error_status_maps_to_one_of_our_own_errors(
    status: int, expected: type[Exception]
) -> None:
    respx.get(f"{BASE}/customers/CX-1234").mock(return_value=httpx.Response(status, json={}))

    async with backend() as client:
        with pytest.raises(expected):
            await client.get_customer_account(TENANT, GetCustomerAccountInput(cx_id="CX-1234"))


@respx.mock
async def test_a_success_status_with_an_html_body_is_refused() -> None:
    # A proxy returning its own error page with a 200 is a real thing that happens.
    respx.get(f"{BASE}/customers/CX-1234").mock(
        return_value=httpx.Response(200, text="<html>gateway</html>")
    )

    async with backend() as client:
        with pytest.raises(BackendBadResponseError):
            await client.get_customer_account(TENANT, GetCustomerAccountInput(cx_id="CX-1234"))


@respx.mock
async def test_a_valid_response_of_an_unexpected_shape_is_refused_not_guessed() -> None:
    respx.get(f"{BASE}/customers/CX-1234").mock(
        return_value=httpx.Response(200, json={"cx_id": "CX-1234", "surprise": True})
    )

    async with backend() as client:
        with pytest.raises(BackendBadResponseError) as caught:
            await client.get_customer_account(TENANT, GetCustomerAccountInput(cx_id="CX-1234"))

    assert caught.value.retryable is False


@respx.mock
async def test_an_enormous_response_is_refused_rather_than_loaded() -> None:
    respx.get(f"{BASE}/customers/CX-1234").mock(
        return_value=httpx.Response(200, json={"padding": "x" * 1_200_000})
    )

    async with backend() as client:
        with pytest.raises(BackendBadResponseError):
            await client.get_customer_account(TENANT, GetCustomerAccountInput(cx_id="CX-1234"))


def test_every_call_carries_a_connect_and_a_read_timeout() -> None:
    client = build_client(
        base_url=BASE,
        api_key="k",
        connect_timeout_s=2.0,
        read_timeout_s=8.0,
        max_connections=10,
    )

    assert client.timeout.connect == 2.0
    assert client.timeout.read == 8.0
    assert client.timeout.pool == 2.0


def test_the_client_does_not_follow_a_redirect_off_our_base_url() -> None:
    client = build_client(
        base_url=BASE, api_key="k", connect_timeout_s=1.0, read_timeout_s=1.0, max_connections=1
    )

    assert client.follow_redirects is False


@respx.mock
async def test_readiness_fails_when_the_middleware_is_unhealthy() -> None:
    respx.get(f"{BASE}/health").mock(return_value=httpx.Response(503))

    async with backend() as client:
        with pytest.raises(BackendError):
            await client.ping()


@respx.mock
async def test_readiness_passes_when_the_middleware_answers() -> None:
    respx.get(f"{BASE}/health").mock(return_value=httpx.Response(200, json={"ok": True}))

    async with backend() as client:
        await client.ping()


# --- the remaining endpoints, so every request path has a test ----------------------

SERVICES = {
    "services": [
        {
            "service_id": "SVC-001",
            "kind": "mobile",
            "plan_name": "Unlimited 5G",
            "status": "active",
            "monthly_price_minor": 2400,
            "currency": "GBP",
            "contract_end_date": None,
        }
    ],
    "total_count": 1,
    "truncated": False,
}
ORDERS = {
    "orders": [
        {
            "order_id": "ORD-9001",
            "state": "dispatched",
            "placed_at": "2026-08-20T09:15:00Z",
            "expected_by": "2026-09-02T00:00:00Z",
            "summary": "Replacement router",
        }
    ],
    "total_count": 1,
    "truncated": False,
}
INVOICES = {
    "invoices": [
        {
            "invoice_id": "INV-2026-08",
            "state": "due",
            "issued_on": "2026-08-01T00:00:00Z",
            "due_on": "2026-09-01T00:00:00Z",
            "total_minor": 6300,
            "outstanding_minor": 6300,
            "currency": "GBP",
        }
    ],
    "total_outstanding_minor": 6300,
    "currency": "GBP",
    "truncated": False,
}
NETWORK: dict[str, Any] = {
    "state": "operational",
    "area_reference": "AREA-EDI-04",
    "incident_id": None,
    "started_at": None,
    "estimated_resolution": None,
    "affected_services": [],
    "message": "No known issues in this area.",
}


@respx.mock
async def test_services_orders_invoices_and_network_are_all_fetched_and_parsed() -> None:
    from telecom_mcp.domain.schemas import (
        GetActiveServicesInput,
        GetInvoiceSummaryInput,
        GetNetworkStatusInput,
        GetOrderStatusInput,
    )

    respx.get(f"{BASE}/customers/CX-1234/services").mock(
        return_value=httpx.Response(200, json=SERVICES)
    )
    respx.get(f"{BASE}/customers/CX-1234/orders").mock(
        return_value=httpx.Response(200, json=ORDERS)
    )
    respx.get(f"{BASE}/customers/CX-1234/invoices").mock(
        return_value=httpx.Response(200, json=INVOICES)
    )
    respx.get(f"{BASE}/customers/CX-1234/network").mock(
        return_value=httpx.Response(200, json=NETWORK)
    )

    async with backend() as client:
        services = await client.get_active_services(
            TENANT, GetActiveServicesInput(cx_id="CX-1234", limit=5)
        )
        orders = await client.get_order_status(
            TENANT, GetOrderStatusInput(cx_id="CX-1234", order_id="ORD-9001", limit=5)
        )
        invoices = await client.get_invoice_summary(
            TENANT, GetInvoiceSummaryInput(cx_id="CX-1234", invoice_id="INV-2026-08", limit=5)
        )
        network = await client.get_network_status(
            TENANT, GetNetworkStatusInput(cx_id="CX-1234", service_id="SVC-001")
        )

    assert services.services[0].plan_name == "Unlimited 5G"
    assert orders.orders[0].order_id == "ORD-9001"
    assert invoices.invoices[0].invoice_id == "INV-2026-08"
    assert network.state == "operational"


@respx.mock
async def test_an_optional_filter_is_sent_as_a_query_parameter_only_when_present() -> None:
    from telecom_mcp.domain.schemas import GetOrderStatusInput

    route = respx.get(f"{BASE}/customers/CX-1234/orders").mock(
        return_value=httpx.Response(200, json=ORDERS)
    )

    async with backend() as client:
        await client.get_order_status(TENANT, GetOrderStatusInput(cx_id="CX-1234", limit=5))

    assert "order_id" not in str(route.calls.last.request.url)


@respx.mock
async def test_a_callback_and_a_refund_request_are_posted_with_their_keys() -> None:
    from telecom_mcp.domain.schemas import RequestRefundApprovalInput, ScheduleCallbackInput

    callback_route = respx.post(f"{BASE}/customers/CX-1234/callbacks").mock(
        return_value=httpx.Response(
            201,
            json={
                "callback_id": "CB-1",
                "cx_id": "CX-1234",
                "scheduled_for": "2026-09-01T10:00:00Z",
                "window": "morning",
                "cancellable_until": "2026-09-01T09:00:00Z",
                "deduplicated": False,
            },
        )
    )
    refund_route = respx.post(f"{BASE}/customers/CX-1234/refund-approvals").mock(
        return_value=httpx.Response(
            202,
            json={
                "request_id": "APR-1",
                "cx_id": "CX-1234",
                "action": "refund",
                "amount_minor": 450,
                "currency": "GBP",
                "reason": "duplicate_charge",
                "justification": "Charged twice.",
                "evidence": {"invoice_id": "INV-2026-08"},
                "state": "pending",
                "requested_by_role": "customer",
                "created_at": "2026-08-30T12:00:00Z",
                "expires_at": "2026-09-01T12:00:00Z",
                "money_moved": False,
                "deduplicated": False,
            },
        )
    )

    async with backend() as client:
        callback = await client.schedule_callback(
            TENANT,
            ScheduleCallbackInput(
                cx_id="CX-1234",
                preferred_date="2026-09-01T10:00:00Z",
                window="morning",
                reason="Discuss the bill",
                idempotency_key="idem-0000-0004",
            ),
        )
        refund = await client.request_refund_approval(
            TENANT,
            RequestRefundApprovalInput(
                cx_id="CX-1234",
                invoice_id="INV-2026-08",
                amount="4.50",
                currency="GBP",
                reason="duplicate_charge",
                justification="Charged twice.",
                idempotency_key="idem-0000-0005",
            ),
        )

    assert callback.callback_id == "CB-1"
    assert refund.money_moved is False
    assert refund.approval_request_id == "APR-1"
    assert callback_route.calls.last.request.headers[IDEMPOTENCY_HEADER] == "idem-0000-0004"
    assert refund_route.calls.last.request.headers[IDEMPOTENCY_HEADER] == "idem-0000-0005"


# --- identity propagation and translation -------------------------------------------


@respx.mock
async def test_the_customers_token_is_sent_and_our_credential_is_a_separate_header() -> None:
    """The middleware authorizes the person, not this service.

    Sending our own credential as Authorization would mean a compromised service
    credential could read any customer's record, which is the property this arrangement
    exists to remove.
    """
    from telecom_mcp.adapters.call_context import CallContext, reset_call, set_call
    from telecom_mcp.adapters.http_backend import CORRELATION_HEADER, SERVICE_CREDENTIAL_HEADER

    route = respx.get(f"{BASE}/customers/CX-1234").mock(
        return_value=httpx.Response(200, json=ACCOUNT)
    )
    call = set_call(CallContext(token="customer-token", correlation_id="corr-99"))
    try:
        async with backend() as client:
            await client.get_customer_account(TENANT, GetCustomerAccountInput(cx_id="CX-1234"))
    finally:
        reset_call(call)

    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer customer-token"
    assert request.headers[SERVICE_CREDENTIAL_HEADER] == "Bearer dummy-key"
    assert request.headers[CORRELATION_HEADER] == "corr-99"


@respx.mock
async def test_minor_units_become_the_decimal_amount_the_tool_contract_exposes() -> None:
    from decimal import Decimal

    from telecom_mcp.domain.schemas import GetInvoiceSummaryInput

    respx.get(f"{BASE}/customers/CX-1234/invoices").mock(
        return_value=httpx.Response(200, json=INVOICES)
    )

    async with backend() as client:
        invoices = await client.get_invoice_summary(
            TENANT, GetInvoiceSummaryInput(cx_id="CX-1234", limit=5)
        )

    assert invoices.invoices[0].total == Decimal("63.00")
    assert invoices.total_outstanding == Decimal("63.00")


@respx.mock
async def test_a_decimal_refund_amount_becomes_integer_minor_units_on_the_wire() -> None:
    from telecom_mcp.domain.schemas import RequestRefundApprovalInput

    route = respx.post(f"{BASE}/customers/CX-1234/refund-approvals").mock(
        return_value=httpx.Response(
            202,
            json={
                "request_id": "APR-1",
                "cx_id": "CX-1234",
                "action": "refund",
                "amount_minor": 499,
                "currency": "GBP",
                "reason": "billing_error",
                "justification": "j",
                "evidence": {},
                "state": "pending",
                "requested_by_role": "customer",
                "created_at": "2026-08-30T12:00:00Z",
                "expires_at": "2026-09-01T12:00:00Z",
                "money_moved": False,
                "deduplicated": False,
            },
        )
    )

    async with backend() as client:
        await client.request_refund_approval(
            TENANT,
            RequestRefundApprovalInput(
                cx_id="CX-1234",
                invoice_id="INV-1",
                amount="4.99",
                currency="GBP",
                reason="billing_error",
                justification="j",
                idempotency_key="idem-0000-0009",
            ),
        )

    body = json.loads(route.calls.last.request.content)
    assert body["amount_minor"] == 499
    assert "amount" not in body, "the decimal form must not also be sent"


@respx.mock
async def test_a_middleware_response_missing_a_field_is_a_clean_backend_error() -> None:
    # Not a KeyError three layers up: the adapter owns the translation and owns its
    # failure.
    from telecom_mcp.domain.schemas import GetActiveServicesInput

    respx.get(f"{BASE}/customers/CX-1234/services").mock(
        return_value=httpx.Response(
            200, json={"services": [{"service_id": "SVC-1"}], "total_count": 1, "truncated": False}
        )
    )

    async with backend() as client:
        with pytest.raises(BackendBadResponseError):
            await client.get_active_services(
                TENANT, GetActiveServicesInput(cx_id="CX-1234", limit=5)
            )


def test_a_float_price_from_the_middleware_is_refused_rather_than_rounded() -> None:
    from telecom_mcp.adapters.translation import minor_to_decimal

    with pytest.raises(ValueError, match="expected integer minor units"):
        minor_to_decimal(24.0)


def test_an_amount_with_too_much_precision_cannot_be_sent() -> None:
    from telecom_mcp.adapters.translation import decimal_to_minor

    with pytest.raises(ValueError, match="more precision"):
        decimal_to_minor("4.999")
    assert decimal_to_minor("4.99") == 499
    assert decimal_to_minor("0.01") == 1
