"""The HTTP adapter is tested by making the outside world fail, not by mocking it away."""

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
