"""Case state and resume, administration, audit, and the operational endpoints."""

from __future__ import annotations

from typing import Any

import httpx

from telecom_middleware.security.permissions import Role
from tests.integration.conftest import CUSTOMER, OTHER_CUSTOMER, Harness

API = "/api/v1"

CASE: dict[str, Any] = {
    "cx_id": CUSTOMER,
    "case_id": "CASE-0001",
    "status": "active",
    "consent_recorded": True,
    "step": {"intent": "Ask about the bill", "tool": "get_invoice_summary", "outcome": "answered"},
}


# --- cases --------------------------------------------------------------------------


async def test_a_case_is_recorded_and_read_back(client: httpx.AsyncClient, seeded: Harness) -> None:
    agent = seeded.headers(role=Role.SUPPORT_AGENT, subject="auth0|agent-7", cx_id=None)
    await client.post(
        f"{API}/assignments",
        headers=seeded.headers(role=Role.SUPERVISOR_APPROVER, cx_id=None),
        json={"agent_sub": "auth0|agent-7", "cx_id": CUSTOMER},
    )

    written = await client.put(f"{API}/cases", headers=agent, json=CASE)
    read_back = await client.get(f"{API}/cases/CASE-0001", headers=agent)

    assert written.status_code == 200
    assert written.json()["tool_steps_used"] == 1
    assert read_back.json()["steps"][0]["tool"] == "get_invoice_summary"


async def test_a_customer_cannot_read_another_customers_case(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    agent = seeded.headers(role=Role.SUPPORT_AGENT, subject="auth0|agent-7", cx_id=None)
    await client.post(
        f"{API}/assignments",
        headers=seeded.headers(role=Role.SUPERVISOR_APPROVER, cx_id=None),
        json={"agent_sub": "auth0|agent-7", "cx_id": OTHER_CUSTOMER},
    )
    await client.put(
        f"{API}/cases", headers=agent, json={**CASE, "cx_id": OTHER_CUSTOMER, "case_id": "CASE-X"}
    )

    response = await client.get(f"{API}/cases/CASE-X", headers=seeded.headers())

    assert response.status_code == 403


async def test_steps_accumulate_and_are_bounded(client: httpx.AsyncClient, seeded: Harness) -> None:
    from telecom_middleware.api.routes.cases import MAX_STEPS

    headers = seeded.headers(permissions=["case:read", "case:write", "account:read"])
    for index in range(MAX_STEPS + 5):
        await client.put(
            f"{API}/cases",
            headers=headers,
            json={**CASE, "step": {"intent": f"turn {index}", "tool": "get_invoice_summary"}},
        )

    response = await client.get(f"{API}/cases/CASE-0001", headers=headers)

    body = response.json()
    assert len(body["steps"]) == MAX_STEPS, "a runaway case must not grow without bound"
    assert body["tool_steps_used"] == MAX_STEPS + 5, "the count is still honest"


async def test_an_interrupted_call_resumes_where_it_stopped(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    headers = seeded.headers(permissions=["case:read", "case:write", "account:read"])
    await client.put(f"{API}/cases", headers=headers, json=CASE)
    await client.put(
        f"{API}/cases", headers=headers, json={**CASE, "status": "interrupted", "step": None}
    )

    resumed = await client.post(f"{API}/customers/{CUSTOMER}/cases/resume", headers=headers)

    assert resumed.status_code == 200
    body = resumed.json()
    assert body["case_id"] == "CASE-0001"
    assert body["status"] == "active"
    assert body["steps"], "the customer does not start again from nothing"


async def test_there_is_nothing_to_resume_when_no_call_was_interrupted(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    headers = seeded.headers(permissions=["case:read", "case:write", "account:read"])
    await client.put(f"{API}/cases", headers=headers, json=CASE)

    response = await client.post(f"{API}/customers/{CUSTOMER}/cases/resume", headers=headers)

    assert response.status_code == 403


async def test_a_handover_records_its_reason(client: httpx.AsyncClient, seeded: Harness) -> None:
    headers = seeded.headers(permissions=["case:read", "case:write", "account:read"])
    await client.put(f"{API}/cases", headers=headers, json=CASE)

    response = await client.put(
        f"{API}/cases",
        headers=headers,
        json={
            **CASE,
            "status": "handed_over",
            "step": None,
            "handover_reason": "Customer disputes the charge",
        },
    )

    assert response.json()["handover_reason"] == "Customer disputes the charge"


async def test_a_case_read_for_an_unknown_case_is_refused(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    headers = seeded.headers(permissions=["case:read", "account:read"])

    assert (await client.get(f"{API}/cases/CASE-NOPE", headers=headers)).status_code == 403


# --- administration -----------------------------------------------------------------


async def test_a_supervisor_assigns_and_revokes_an_account(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    supervisor = seeded.headers(role=Role.SUPERVISOR_APPROVER, cx_id=None)

    assigned = await client.post(
        f"{API}/assignments",
        headers=supervisor,
        json={"agent_sub": "auth0|agent-9", "cx_id": CUSTOMER},
    )
    listed = await client.get(f"{API}/assignments/auth0|agent-9", headers=supervisor)
    revoked = await client.delete(f"{API}/assignments/auth0|agent-9/{CUSTOMER}", headers=supervisor)
    again = await client.delete(f"{API}/assignments/auth0|agent-9/{CUSTOMER}", headers=supervisor)

    assert assigned.status_code == 204
    assert listed.json()["accounts"] == [CUSTOMER]
    assert revoked.status_code == 204
    assert again.status_code == 403, "revoking what is not there is refused, not silently fine"


async def test_an_agent_cannot_assign_accounts_to_themselves(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    agent = seeded.headers(role=Role.SUPPORT_AGENT, subject="auth0|agent-7", cx_id=None)

    response = await client.post(
        f"{API}/assignments", headers=agent, json={"agent_sub": "auth0|agent-7", "cx_id": CUSTOMER}
    )

    assert response.status_code == 403


async def test_security_administration_reads_the_audit_trail_and_its_verdict(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    await client.post(
        f"{API}/customers/{CUSTOMER}/tickets",
        headers={**seeded.headers(), "Idempotency-Key": "idem-audit-0001"},
        json={
            "cx_id": CUSTOMER,
            "category": "billing",
            "subject": "s",
            "description": "d",
        },
    )
    security = seeded.headers(role=Role.ADMIN_SECURITY, subject="auth0|sec-1", cx_id=None)

    response = await client.get(f"{API}/audit", headers=security)

    assert response.status_code == 200
    body = response.json()
    assert body["records"], "the write is in the trail"
    assert body["chain_broken_at"] is None
    # Even here, the customer's identifier is a reference rather than the value.
    assert CUSTOMER not in response.text


async def test_security_administration_cannot_read_a_customer_record(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    security = seeded.headers(role=Role.ADMIN_SECURITY, subject="auth0|sec-1", cx_id=None)

    response = await client.get(f"{API}/customers/{CUSTOMER}", headers=security)

    assert response.status_code == 403


async def test_the_audit_trail_can_be_narrowed_to_one_correlation_identifier(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    await client.get(
        f"{API}/customers/{OTHER_CUSTOMER}",
        headers={**seeded.headers(), "X-Correlation-Id": "trace-abc"},
    )
    security = seeded.headers(role=Role.ADMIN_SECURITY, subject="auth0|sec-1", cx_id=None)

    response = await client.get(f"{API}/audit?correlation_id=trace-abc", headers=security)

    assert [r["correlation_id"] for r in response.json()["records"]] == ["trace-abc"]


async def test_a_customer_cannot_read_the_audit_trail(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    assert (await client.get(f"{API}/audit", headers=seeded.headers())).status_code == 403


# --- operations ---------------------------------------------------------------------


async def test_liveness_answers_without_consulting_the_store(
    client: httpx.AsyncClient, harness: Harness
) -> None:
    harness.store.set_healthy(False)

    response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


async def test_readiness_reports_unhealthy_when_the_store_is_down(
    client: httpx.AsyncClient, harness: Harness
) -> None:
    harness.store.set_healthy(False)

    response = await client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["components"][0]["name"] == "store"
    # The type name is enough to act on and cannot carry a connection string.
    assert "mongodb://" not in response.text


async def test_readiness_passes_when_the_store_answers(client: httpx.AsyncClient) -> None:
    response = await client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
