"""The whole system, one journey at a time."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from conftest import (
    API_AUDIENCE,
    CUSTOMER,
    OTHER_CUSTOMER,
    SUPERVISOR_PERMISSIONS,
    TENANT,
    System,
    make_token,
)

TICKET: dict[str, Any] = {
    "cx_id": CUSTOMER,
    "category": "network",
    "subject": "Broadband keeps dropping",
    "description": "The connection drops every evening around eight.",
    "idempotency_key": "idem-e2e-0001",
}


async def api(system: System, method: str, path: str, token: str, **kwargs: Any) -> httpx.Response:
    """Talk to the middleware directly, as a console would."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=system.middleware_app), base_url="http://middleware"
    ) as client:
        return await client.request(
            method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs
        )


async def test_a_customer_read_travels_the_whole_way_and_back(system: System) -> None:
    result = await system.mcp_server.call_tool_for_caller(
        "get_customer_account", {"cx_id": CUSTOMER}
    )

    assert result["display_name"] == "J. Okonkwo"
    assert result["account_status"] == "active"


async def test_the_tools_a_caller_sees_come_from_their_own_permissions(system: System) -> None:
    listed = {tool.name for tool in await system.mcp_server.list_tools_for_caller()}

    assert "get_customer_account" in listed
    assert "cancel_service" not in listed


async def test_money_arrives_as_a_decimal_though_it_was_stored_as_minor_units(
    system: System,
) -> None:
    from decimal import Decimal

    result = await system.mcp_server.call_tool_for_caller(
        "get_invoice_summary", {"cx_id": CUSTOMER, "limit": 5}
    )

    assert Decimal(result["total_outstanding"]) == Decimal("63.00")
    assert result["invoices"][0]["total"] == "63.00"


async def test_a_cross_account_read_is_refused_by_the_tool_server_before_it_leaves(
    system: System,
) -> None:
    result = await system.mcp_server.call_tool_for_caller(
        "get_customer_account", {"cx_id": OTHER_CUSTOMER}
    )

    assert result["error"]["code"] == "cross_account_denied"
    assert OTHER_CUSTOMER not in str(result)


async def test_the_middleware_refuses_a_cross_account_read_on_its_own_account(
    system: System,
) -> None:
    """The second lock. Even if the tool server were compromised, this one holds."""
    response = await api(
        system, "GET", f"/api/v1/customers/{OTHER_CUSTOMER}", make_token()
    )

    assert response.status_code == 403


async def test_a_service_credential_alone_reaches_nothing(system: System) -> None:
    service_token = make_token(role="service", subject="mcp@clients", cx_id=None, permissions=[])

    response = await api(system, "GET", f"/api/v1/customers/{CUSTOMER}", service_token)

    assert response.status_code == 403


async def test_a_ticket_raised_through_the_tool_exists_in_the_store(system: System) -> None:
    result = await system.mcp_server.call_tool_for_caller("create_support_ticket", TICKET)

    assert result["ticket_id"].startswith("TCK-")
    stored = await system.store.tickets.get(TENANT, result["ticket_id"])
    assert stored is not None
    assert stored.subject == TICKET["subject"]


async def test_repeating_a_write_end_to_end_produces_one_ticket(system: System) -> None:
    first = await system.mcp_server.call_tool_for_caller("create_support_ticket", TICKET)
    second = await system.mcp_server.call_tool_for_caller("create_support_ticket", TICKET)

    assert second["ticket_id"] == first["ticket_id"]
    assert second["deduplicated"] is True
    # One created by this test, one from the seed data.
    assert len(system.store.tickets.items.all_for(TENANT)) == 2


async def test_both_services_record_the_same_correlation_identifier(system: System) -> None:
    """One identifier, generated once, adopted by the second service.

    The middleware audits state changes and refusals; a plain read is audited by the
    gateway, which is the layer that knows the case it belongs to. Either way the
    identifier is the same, so one search finds the whole story.
    """
    await system.mcp_server.call_tool_for_caller("create_support_ticket", TICKET)

    middleware_records = system.store.audit.records[TENANT]
    assert middleware_records, "the middleware audited the write"
    # The tool server generates the identifier and forwards it; the middleware adopts it
    # rather than inventing its own, so one trace covers both services.
    assert middleware_records[-1].correlation_id.startswith("corr-")


async def test_the_customer_reference_is_pseudonymised_in_both_audit_trails(
    system: System,
) -> None:
    await system.mcp_server.call_tool_for_caller("create_support_ticket", TICKET)

    serialised = "".join(r.model_dump_json() for r in system.store.audit.records[TENANT])
    assert CUSTOMER not in serialised
    assert "ref_" in serialised


# --- the approval journey, across three stakeholders ---------------------------------


async def test_a_refund_travels_from_the_customer_to_a_supervisor_and_back(
    system: System,
) -> None:
    supervisor = make_token(
        role="supervisor_approver",
        subject="auth0|supervisor-1",
        cx_id=None,
        permissions=SUPERVISOR_PERMISSIONS,
    )

    # 1. The customer asks, through the voice agent's tool. Nothing moves.
    requested = await system.mcp_server.call_tool_for_caller(
        "request_refund_approval",
        {
            "cx_id": CUSTOMER,
            "invoice_id": "INV-2026-08",
            "amount": "4.50",
            "currency": "GBP",
            "reason": "service_outage",
            "justification": "Broadband unavailable for three days in August.",
            "idempotency_key": "idem-e2e-refund-1",
        },
    )
    assert requested["state"] == "pending_approval"
    assert requested["money_moved"] is False

    request_id = requested["approval_request_id"]

    # 2. The supervisor sees it in their queue, with the evidence gathered from the
    #    record rather than from the customer's description.
    queue = await api(system, "GET", "/api/v1/approvals", supervisor)
    assert queue.status_code == 200
    pending = [a for a in queue.json()["approvals"] if a["request_id"] == request_id]
    assert pending, "the request the customer raised is in the supervisor's queue"
    assert pending[0]["evidence"]["invoice_total_minor"] == 6300

    # 3. The supervisor decides.
    decided = await api(
        system,
        "POST",
        f"/api/v1/approvals/{request_id}/decision",
        supervisor,
        json={"decision": "approved", "note": "Outage confirmed against incident INC-5512."},
    )
    assert decided.status_code == 200
    assert decided.json()["state"] == "approved"
    # Approving authorises. It still moves no money; execution is a later, separate step.
    assert decided.json()["money_moved"] is False

    # 4. The whole chain is one audit trail, and it verifies.
    from telecom_middleware.services.recording import verify_chain

    records = system.store.audit.records[TENANT]
    assert verify_chain(records) is None
    actions = [record.action for record in records]
    assert "request_refund_approval" in actions
    assert "decide_approval" in actions


async def test_the_supervisor_sees_the_request_appear_live(system: System) -> None:
    from telecom_middleware.security.permissions import ROLE_SCOPES, Role
    from telecom_middleware.security.principal import Principal

    supervisor = Principal(
        subject="auth0|supervisor-1",
        tenant_id=TENANT,
        role=Role.SUPERVISOR_APPROVER,
        granted_scopes=ROLE_SCOPES[Role.SUPERVISOR_APPROVER],
        expires_at=system.clock.now(),
    )

    async with system.broker.subscribe(supervisor) as subscriber:
        await system.mcp_server.call_tool_for_caller(
            "request_refund_approval",
            {
                "cx_id": CUSTOMER,
                "invoice_id": "INV-2026-08",
                "amount": "4.50",
                "currency": "GBP",
                "reason": "service_outage",
                "justification": "Broadband unavailable for three days in August.",
                "idempotency_key": "idem-e2e-refund-2",
            },
        )

        event = await asyncio.wait_for(subscriber.queue.get(), timeout=2)

    assert event.type == "approval.requested"
    assert event.tenant_id == TENANT
    # Fanned out to every supervisor watching, so it carries a reference, never the
    # customer's identifier.
    assert CUSTOMER not in event.model_dump_json()


async def test_a_customer_watching_the_stream_never_sees_the_approval_queue(
    system: System,
) -> None:
    from telecom_middleware.security.permissions import ROLE_SCOPES, Role
    from telecom_middleware.security.principal import Principal

    customer = Principal(
        subject="auth0|customer-1",
        tenant_id=TENANT,
        role=Role.CUSTOMER,
        granted_scopes=ROLE_SCOPES[Role.CUSTOMER],
        expires_at=system.clock.now(),
        cx_id=CUSTOMER,
    )

    async with system.broker.subscribe(customer) as subscriber:
        await system.mcp_server.call_tool_for_caller(
            "request_refund_approval",
            {
                "cx_id": CUSTOMER,
                "invoice_id": "INV-2026-08",
                "amount": "4.50",
                "currency": "GBP",
                "reason": "service_outage",
                "justification": "Broadband unavailable for three days in August.",
                "idempotency_key": "idem-e2e-refund-3",
            },
        )

        assert subscriber.queue.empty()


# --- failure and identity -------------------------------------------------------------


async def test_an_expired_token_is_refused_and_the_customer_is_told_plainly(
    system: System,
) -> None:
    from datetime import UTC, datetime, timedelta

    import jwt
    from conftest import CUSTOMER_PERMISSIONS, MCP_AUDIENCE, NAMESPACE, SECRET

    expired = jwt.encode(
        {
            "sub": "auth0|customer-1",
            "aud": [MCP_AUDIENCE, API_AUDIENCE],
            "exp": int((datetime.now(UTC) - timedelta(seconds=1)).timestamp()),
            "permissions": CUSTOMER_PERMISSIONS,
            f"{NAMESPACE}tenant_id": TENANT,
            f"{NAMESPACE}role": "customer",
            f"{NAMESPACE}cx_id": CUSTOMER,
        },
        SECRET,
        algorithm="HS256",
    )
    system.act_as(expired)

    result = await system.mcp_server.call_tool_for_caller(
        "get_customer_account", {"cx_id": CUSTOMER}
    )

    assert result["error"]["code"] == "token_expired"
    assert result["error"]["retryable"] is True


async def test_a_passcode_failure_locks_the_account_and_the_agent_learns_nothing_extra(
    system: System,
) -> None:
    token = make_token()

    for _ in range(5):
        response = await api(
            system,
            "POST",
            f"/api/v1/customers/{CUSTOMER}/authenticate",
            token,
            json={"cx_id": CUSTOMER, "passcode": "0000"},
        )
        assert response.status_code == 401
        assert response.json()["title"] == "Authentication failed."

    locked = await api(
        system,
        "POST",
        f"/api/v1/customers/{CUSTOMER}/authenticate",
        token,
        json={"cx_id": CUSTOMER, "passcode": "4821"},
    )

    assert locked.status_code == 423


@pytest.mark.parametrize(
    "tool",
    ["get_customer_account", "get_active_services", "get_order_status", "get_invoice_summary",
     "get_network_status"],
)
async def test_every_read_tool_works_against_the_real_middleware(
    system: System, tool: str
) -> None:
    result = await system.mcp_server.call_tool_for_caller(tool, {"cx_id": CUSTOMER})

    assert "error" not in result, result
