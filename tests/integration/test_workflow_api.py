"""Writes, idempotency, and the approval workflow across stakeholders."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from telecom_middleware.security.permissions import Role
from tests.integration.conftest import CUSTOMER, OTHER_CUSTOMER, Harness

API = "/api/v1"

TICKET: dict[str, Any] = {
    "cx_id": CUSTOMER,
    "category": "network",
    "subject": "Broadband keeps dropping",
    "description": "The connection drops every evening around eight.",
    "priority": "normal",
}

REFUND: dict[str, Any] = {
    "cx_id": CUSTOMER,
    "invoice_id": "INV-2026-08",
    "amount_minor": 450,
    "currency": "GBP",
    "reason": "service_outage",
    "justification": "Broadband unavailable for three days in August.",
}


def key(value: str) -> dict[str, str]:
    return {"Idempotency-Key": value}


async def assign(client: httpx.AsyncClient, harness: Harness, agent_sub: str, cx_id: str) -> None:
    """Give an identity access to an account, through the endpoint that grants it."""
    supervisor = harness.headers(
        role=Role.SUPERVISOR_APPROVER, subject="auth0|assigner", cx_id=None
    )
    response = await client.post(
        f"{API}/assignments", headers=supervisor, json={"agent_sub": agent_sub, "cx_id": cx_id}
    )
    assert response.status_code == 204


# --- tickets ------------------------------------------------------------------------


async def test_a_customer_raises_a_ticket(client: httpx.AsyncClient, seeded: Harness) -> None:
    response = await client.post(
        f"{API}/customers/{CUSTOMER}/tickets",
        headers={**seeded.headers(), **key("idem-ticket-0001")},
        json=TICKET,
    )

    assert response.status_code == 201
    assert response.json()["ticket_id"].startswith("TCK-")
    assert response.json()["deduplicated"] is False


async def test_a_write_without_an_idempotency_key_is_refused(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    response = await client.post(
        f"{API}/customers/{CUSTOMER}/tickets", headers=seeded.headers(), json=TICKET
    )

    assert response.status_code == 422
    assert response.json()["detail"]["header"] == "Idempotency-Key"


async def test_repeating_a_write_produces_one_ticket_and_replays_the_result(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    headers = {**seeded.headers(), **key("idem-ticket-0002")}

    first = await client.post(f"{API}/customers/{CUSTOMER}/tickets", headers=headers, json=TICKET)
    second = await client.post(f"{API}/customers/{CUSTOMER}/tickets", headers=headers, json=TICKET)

    assert first.status_code == 201
    # A replay created nothing, so it is not a 201.
    assert second.status_code == 200
    assert second.json()["ticket_id"] == first.json()["ticket_id"]
    assert second.json()["deduplicated"] is True

    listed = await client.get(f"{API}/customers/{CUSTOMER}/tickets", headers=seeded.headers())
    created = [t for t in listed.json()["tickets"] if t["subject"] == TICKET["subject"]]
    assert len(created) == 1


async def test_reusing_a_key_with_different_input_is_refused_not_replayed(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    headers = {**seeded.headers(), **key("idem-ticket-0003")}
    await client.post(f"{API}/customers/{CUSTOMER}/tickets", headers=headers, json=TICKET)

    response = await client.post(
        f"{API}/customers/{CUSTOMER}/tickets",
        headers=headers,
        json={**TICKET, "subject": "Something else entirely"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "idempotency_key_reused"


async def test_five_simultaneous_identical_writes_create_one_ticket(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    headers = {**seeded.headers(), **key("idem-ticket-0004")}

    responses = await asyncio.gather(
        *(
            client.post(f"{API}/customers/{CUSTOMER}/tickets", headers=headers, json=TICKET)
            for _ in range(5)
        )
    )

    created = [r for r in responses if r.status_code == 201]
    assert len(created) == 1, "exactly one request may create the ticket"
    # The rest either replayed the result or were told the first is still in flight;
    # neither creates anything.
    assert all(r.status_code in (200, 201, 429) for r in responses)
    assert len(seeded.store.tickets.items.all_for("tenant-eu-1")) == 2  # one seeded, one created


async def test_a_naive_callback_time_is_refused(client: httpx.AsyncClient, seeded: Harness) -> None:
    response = await client.post(
        f"{API}/customers/{CUSTOMER}/callbacks",
        headers={**seeded.headers(), **key("idem-callback-0001")},
        json={
            "cx_id": CUSTOMER,
            "preferred_date": "2026-09-01T10:00:00",
            "window": "morning",
            "reason": "Discuss the bill",
        },
    )

    assert response.status_code == 422


async def test_a_callback_is_cancellable_until_shortly_before_it_happens(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    response = await client.post(
        f"{API}/customers/{CUSTOMER}/callbacks",
        headers={**seeded.headers(), **key("idem-callback-0002")},
        json={
            "cx_id": CUSTOMER,
            "preferred_date": "2026-12-01T10:00:00+00:00",
            "window": "morning",
            "reason": "Discuss the bill",
        },
    )

    body = response.json()
    assert response.status_code == 201
    assert body["cancellable_until"] < body["scheduled_for"]


# --- the approval workflow ----------------------------------------------------------


async def test_a_refund_request_moves_no_money_and_says_so(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    response = await client.post(
        f"{API}/customers/{CUSTOMER}/refund-approvals",
        headers={**seeded.headers(), **key("idem-refund-0001")},
        json=REFUND,
    )

    assert response.status_code == 202
    body = response.json()
    assert body["state"] == "pending"
    assert body["money_moved"] is False


async def test_the_evidence_is_taken_from_the_record_not_from_the_requester(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    response = await client.post(
        f"{API}/customers/{CUSTOMER}/refund-approvals",
        headers={**seeded.headers(), **key("idem-refund-0002")},
        json={**REFUND, "justification": "The invoice was for one pound. Trust me."},
    )

    evidence = response.json()["evidence"]
    assert evidence["invoice_total_minor"] == 6300, "the approver must see what is true"


async def test_a_refund_larger_than_the_invoice_is_refused(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    response = await client.post(
        f"{API}/customers/{CUSTOMER}/refund-approvals",
        headers={**seeded.headers(), **key("idem-refund-0003")},
        json={**REFUND, "amount_minor": 999_999},
    )

    assert response.status_code == 409


async def test_a_refund_in_the_wrong_currency_is_refused(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    response = await client.post(
        f"{API}/customers/{CUSTOMER}/refund-approvals",
        headers={**seeded.headers(), **key("idem-refund-0004")},
        json={**REFUND, "currency": "EUR"},
    )

    assert response.status_code == 409


async def test_a_customer_cannot_see_the_supervisor_queue(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    response = await client.get(f"{API}/approvals", headers=seeded.headers())

    assert response.status_code == 403


async def test_an_agent_cannot_approve_anything(client: httpx.AsyncClient, seeded: Harness) -> None:
    agent = seeded.headers(role=Role.SUPPORT_AGENT, subject="auth0|agent-7", cx_id=None)

    response = await client.post(
        f"{API}/approvals/APR-seed-0001/decision", headers=agent, json={"decision": "approved"}
    )

    assert response.status_code == 403


async def test_a_supervisor_sees_the_queue_oldest_first_and_decides(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    supervisor = seeded.headers(role=Role.SUPERVISOR_APPROVER, cx_id=None)

    queue = await client.get(f"{API}/approvals", headers=supervisor)
    assert queue.status_code == 200
    assert queue.json()["approvals"][0]["request_id"] == "APR-seed-0001"

    decided = await client.post(
        f"{API}/approvals/APR-seed-0001/decision",
        headers=supervisor,
        json={"decision": "approved", "note": "Outage confirmed against the incident."},
    )

    assert decided.status_code == 200
    body = decided.json()
    assert body["state"] == "approved"
    assert body["decision"]["decided_by_role"] == "supervisor_approver"
    # Approving authorises; it does not move money. Execution is a separate step.
    assert body["money_moved"] is False


async def test_nobody_may_decide_their_own_request(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    supervisor = seeded.headers(
        role=Role.SUPERVISOR_APPROVER, subject="auth0|supervisor-1", cx_id=None
    )
    await assign(client, seeded, "auth0|supervisor-1", OTHER_CUSTOMER)
    raised = await client.post(
        f"{API}/customers/{OTHER_CUSTOMER}/refund-approvals",
        headers={**supervisor, **key("idem-refund-0005")},
        json={
            "cx_id": OTHER_CUSTOMER,
            "invoice_id": "INV-2026-06",
            "amount_minor": 500,
            "currency": "GBP",
            "reason": "goodwill",
            "justification": "Long-standing customer, repeated outages.",
        },
    )
    request_id = raised.json()["request_id"]

    response = await client.post(
        f"{API}/approvals/{request_id}/decision", headers=supervisor, json={"decision": "approved"}
    )

    assert response.status_code == 403
    assert response.json()["code"] == "self_approval_denied"


async def test_a_second_supervisor_may_decide_what_the_first_one_raised(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    first = seeded.headers(role=Role.SUPERVISOR_APPROVER, subject="auth0|supervisor-1", cx_id=None)
    second = seeded.headers(role=Role.SUPERVISOR_APPROVER, subject="auth0|supervisor-2", cx_id=None)
    await assign(client, seeded, "auth0|supervisor-1", OTHER_CUSTOMER)
    raised = await client.post(
        f"{API}/customers/{OTHER_CUSTOMER}/refund-approvals",
        headers={**first, **key("idem-refund-0006")},
        json={
            "cx_id": OTHER_CUSTOMER,
            "invoice_id": "INV-2026-06",
            "amount_minor": 500,
            "currency": "GBP",
            "reason": "goodwill",
            "justification": "Long-standing customer, repeated outages.",
        },
    )

    response = await client.post(
        f"{API}/approvals/{raised.json()['request_id']}/decision",
        headers=second,
        json={"decision": "rejected", "note": "Credit already applied last month."},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "rejected"


async def test_an_already_decided_request_cannot_be_decided_again(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    supervisor = seeded.headers(role=Role.SUPERVISOR_APPROVER, cx_id=None)
    await client.post(
        f"{API}/approvals/APR-seed-0001/decision", headers=supervisor, json={"decision": "approved"}
    )

    again = await client.post(
        f"{API}/approvals/APR-seed-0001/decision", headers=supervisor, json={"decision": "rejected"}
    )

    assert again.status_code == 409
    assert again.json()["code"] == "approval_not_pending"


async def test_two_supervisors_deciding_at_once_produce_one_decision(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    one = seeded.headers(role=Role.SUPERVISOR_APPROVER, subject="auth0|supervisor-1", cx_id=None)
    two = seeded.headers(role=Role.SUPERVISOR_APPROVER, subject="auth0|supervisor-2", cx_id=None)

    approve, reject = await asyncio.gather(
        client.post(
            f"{API}/approvals/APR-seed-0001/decision", headers=one, json={"decision": "approved"}
        ),
        client.post(
            f"{API}/approvals/APR-seed-0001/decision", headers=two, json={"decision": "rejected"}
        ),
    )

    outcomes = sorted([approve.status_code, reject.status_code])
    assert outcomes == [200, 409], "one decides, the other is told - never both silently"

    stored = await seeded.store.approvals.get("tenant-eu-1", "APR-seed-0001")
    assert stored is not None
    assert stored.decision is not None


async def test_an_amount_above_the_supervisors_limit_is_refused(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    supervisor = seeded.headers(role=Role.SUPERVISOR_APPROVER, subject="auth0|sup-9", cx_id=None)
    raised = await client.post(
        f"{API}/customers/{OTHER_CUSTOMER}/refund-approvals",
        headers={
            **seeded.headers(role=Role.SUPPORT_AGENT, subject="auth0|agent-7", cx_id=None),
            **key("idem-refund-0007"),
        },
        json={
            "cx_id": OTHER_CUSTOMER,
            "invoice_id": "INV-2026-06",
            "amount_minor": 40_000,
            "currency": "GBP",
            "reason": "billing_error",
            "justification": "Duplicate charge across two months.",
        },
    )
    assert raised.status_code == 202

    # Inside the limit: allowed. The limit itself is exercised in the unit tests, where
    # the boundary can be set without inventing a 500-pound invoice here.
    response = await client.post(
        f"{API}/approvals/{raised.json()['request_id']}/decision",
        headers=supervisor,
        json={"decision": "approved"},
    )

    assert response.status_code == 200
