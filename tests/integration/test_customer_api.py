"""Customer reads and passcode authentication, driven over HTTP."""

from __future__ import annotations

import httpx

from telecom_middleware.security.permissions import Role
from tests.integration.conftest import CUSTOMER, OTHER_CUSTOMER, Harness

API = "/api/v1"


async def test_a_customer_reads_their_own_account(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    response = await client.get(f"{API}/customers/{CUSTOMER}", headers=seeded.headers())

    assert response.status_code == 200
    body = response.json()
    assert body["display_name"] == "J. Okonkwo"
    assert body["account_status"] == "active"


async def test_a_customer_cannot_read_another_account(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    response = await client.get(f"{API}/customers/{OTHER_CUSTOMER}", headers=seeded.headers())

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


async def test_a_missing_customer_answers_exactly_like_a_forbidden_one(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    # Otherwise the difference between the two is an oracle for which accounts exist.
    forbidden = await client.get(f"{API}/customers/{OTHER_CUSTOMER}", headers=seeded.headers())
    missing = await client.get(
        f"{API}/customers/CX-0000",
        headers=seeded.headers(cx_id="CX-0000"),
    )

    assert forbidden.status_code == missing.status_code == 403
    assert forbidden.json()["title"] == missing.json()["title"]


async def test_an_agent_reads_only_an_assigned_account(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    agent = seeded.headers(role=Role.SUPPORT_AGENT, subject="auth0|agent-7", cx_id=None)

    assigned = await client.get(f"{API}/customers/{OTHER_CUSTOMER}", headers=agent)
    unassigned = await client.get(f"{API}/customers/{CUSTOMER}", headers=agent)

    assert assigned.status_code == 200
    assert unassigned.status_code == 403


async def test_a_service_credential_alone_cannot_read_customer_data(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    # The MCP server authenticates as a service but must present the customer's token.
    service = seeded.headers(
        role=Role.SERVICE, is_service=True, cx_id=None, permissions=["account:read"]
    )

    response = await client.get(f"{API}/customers/{CUSTOMER}", headers=service)

    assert response.status_code == 403


async def test_a_token_missing_the_scope_is_refused_before_any_lookup(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    response = await client.get(
        f"{API}/customers/{CUSTOMER}/invoices", headers=seeded.headers(permissions=["account:read"])
    )

    assert response.status_code == 403


async def test_services_orders_and_invoices_come_back_projected(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    headers = seeded.headers()

    services = await client.get(f"{API}/customers/{CUSTOMER}/services", headers=headers)
    orders = await client.get(f"{API}/customers/{CUSTOMER}/orders", headers=headers)
    invoices = await client.get(f"{API}/customers/{CUSTOMER}/invoices", headers=headers)

    assert services.json()["total_count"] == 2
    assert orders.json()["orders"][0]["order_id"] == "ORD-9001"
    assert invoices.json()["total_outstanding_minor"] == 6300
    assert invoices.json()["currency"] == "GBP"


async def test_money_crosses_the_wire_as_an_integer_never_a_float(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    body = (
        await client.get(f"{API}/customers/{CUSTOMER}/invoices", headers=seeded.headers())
    ).json()

    assert isinstance(body["invoices"][0]["total_minor"], int)
    assert "." not in str(body["invoices"][0]["total_minor"])


async def test_a_page_size_beyond_the_maximum_is_refused(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    response = await client.get(
        f"{API}/customers/{CUSTOMER}/services?limit=999", headers=seeded.headers()
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_input"


async def test_an_unknown_order_reference_is_refused_indistinguishably(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    response = await client.get(
        f"{API}/customers/{CUSTOMER}/orders?order_id=ORD-0000", headers=seeded.headers()
    )

    assert response.status_code == 403


async def test_the_network_area_comes_from_the_record_not_from_the_caller(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    # A caller-supplied area would let anyone read any area's incident detail.
    response = await client.get(
        f"{API}/customers/{CUSTOMER}/network?service_id=AREA-SOMEWHERE-ELSE",
        headers=seeded.headers(),
    )

    assert response.status_code == 200
    assert response.json()["area_reference"] == "AREA-EDI-04"


# --- authentication -----------------------------------------------------------------


async def test_the_right_passcode_authenticates(client: httpx.AsyncClient, seeded: Harness) -> None:
    response = await client.post(
        f"{API}/customers/{CUSTOMER}/authenticate",
        headers=seeded.headers(),
        json={"cx_id": CUSTOMER, "passcode": "4821"},
    )

    assert response.status_code == 200
    assert response.json()["authenticated"] is True


async def test_a_wrong_passcode_says_only_that_it_failed(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    response = await client.post(
        f"{API}/customers/{CUSTOMER}/authenticate",
        headers=seeded.headers(),
        json={"cx_id": CUSTOMER, "passcode": "0000"},
    )

    assert response.status_code == 401
    body = response.json()
    assert body["title"] == "Authentication failed."
    assert "attempt" not in body["title"].lower()


async def test_an_unknown_customer_answers_exactly_like_a_wrong_passcode(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    wrong = await client.post(
        f"{API}/customers/{CUSTOMER}/authenticate",
        headers=seeded.headers(),
        json={"cx_id": CUSTOMER, "passcode": "0000"},
    )
    unknown = await client.post(
        f"{API}/customers/CX-0000/authenticate",
        headers=seeded.headers(cx_id="CX-0000"),
        json={"cx_id": "CX-0000", "passcode": "4821"},
    )

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["title"] == unknown.json()["title"]


async def test_the_account_locks_after_the_configured_attempts(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    for _ in range(5):
        await client.post(
            f"{API}/customers/{CUSTOMER}/authenticate",
            headers=seeded.headers(),
            json={"cx_id": CUSTOMER, "passcode": "0000"},
        )

    # Even the correct passcode is refused while the lockout is in force.
    locked = await client.post(
        f"{API}/customers/{CUSTOMER}/authenticate",
        headers=seeded.headers(),
        json={"cx_id": CUSTOMER, "passcode": "4821"},
    )

    assert locked.status_code == 423
    assert locked.json()["code"] == "account_locked"


async def test_the_lockout_lifts_when_it_expires(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    for _ in range(5):
        await client.post(
            f"{API}/customers/{CUSTOMER}/authenticate",
            headers=seeded.headers(),
            json={"cx_id": CUSTOMER, "passcode": "0000"},
        )

    seeded.clock.advance(901)

    response = await client.post(
        f"{API}/customers/{CUSTOMER}/authenticate",
        headers=seeded.headers(),
        json={"cx_id": CUSTOMER, "passcode": "4821"},
    )

    assert response.status_code == 200


async def test_the_passcode_never_appears_in_the_audit_trail(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    await client.post(
        f"{API}/customers/{CUSTOMER}/authenticate",
        headers=seeded.headers(),
        json={"cx_id": CUSTOMER, "passcode": "4821"},
    )

    records = seeded.store.audit.records["tenant-eu-1"]
    serialised = "".join(record.model_dump_json() for record in records)
    assert "4821" not in serialised
    assert CUSTOMER not in serialised


async def test_a_passcode_that_is_not_four_digits_is_refused_without_reaching_the_store(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    response = await client.post(
        f"{API}/customers/{CUSTOMER}/authenticate",
        headers=seeded.headers(),
        json={"cx_id": CUSTOMER, "passcode": "48210"},
    )

    assert response.status_code == 422
    # The field name, never the value.
    assert "48210" not in response.text


async def test_every_response_carries_the_correlation_identifier(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    response = await client.get(
        f"{API}/customers/{CUSTOMER}",
        headers={**seeded.headers(), "X-Correlation-Id": "trace-from-the-agent"},
    )

    assert response.headers["X-Correlation-Id"] == "trace-from-the-agent"


async def test_a_hostile_correlation_identifier_is_sanitised(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    response = await client.get(
        f"{API}/customers/{CUSTOMER}",
        headers={**seeded.headers(), "X-Correlation-Id": "trace\r\nInjected: header"},
    )

    # A header value that carries CRLF is a response-splitting attempt; the identifier
    # is reduced to safe characters before it is echoed anywhere.
    echoed = response.headers["X-Correlation-Id"]
    assert "\r" not in echoed and "\n" not in echoed
    assert "Injected" not in str(response.headers.get("Injected", ""))
    assert echoed == "traceInjectedheader"


async def test_protective_headers_are_set_on_every_response(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    response = await client.get(f"{API}/customers/{CUSTOMER}", headers=seeded.headers())

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cache-Control"] == "no-store"
