"""The behaviour both stores must have. Run against each; a disagreement fails here.

This is the suite that stops the fast in-memory implementation drifting away from the
real one, which is the usual way an offline test suite becomes a comfortable lie.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from telecom_middleware.domain.errors import IdempotencyKeyReusedError
from telecom_middleware.domain.events import DomainEvent, EventType
from telecom_middleware.domain.models import ApprovalState, CaseStatus
from tests import builders
from tests.builders import CUSTOMER, NOW, OTHER_TENANT, TENANT

#: When a reservation is made, not merely a fixed point in time.
#:
#: Reservations expire, and MongoDB enforces that with a TTL index on expires_at rather
#: than a comparison at read time. builders.NOW is a fixed date, so once the wall clock
#: passed it every reservation these tests made was born already expired, and the TTL
#: monitor - which sweeps roughly once a minute - deleted one partway through the run.
#: The test that reserves the same key twice then saw its own repeat as the first
#: reservation. Nothing here is asserting anything about the date, so anchoring to the
#: real clock removes the dependency instead of pushing the constant forward and waiting
#: for the same failure again.
RESERVED_AT = datetime.now(UTC)

# --- tenancy ------------------------------------------------------------------------


async def test_a_record_is_invisible_from_another_tenant(store: Any) -> None:
    await store.customers.upsert(builders.customer())

    assert await store.customers.get(TENANT, CUSTOMER) is not None
    assert await store.customers.get(OTHER_TENANT, CUSTOMER) is None


async def test_the_same_reference_in_two_tenants_is_two_different_records(store: Any) -> None:
    await store.customers.upsert(builders.customer(display_name="Tenant one"))
    await store.customers.upsert(
        builders.customer(tenant_id=OTHER_TENANT, display_name="Tenant two")
    )

    here = await store.customers.get(TENANT, CUSTOMER)
    there = await store.customers.get(OTHER_TENANT, CUSTOMER)

    assert here is not None and there is not None
    assert here.display_name != there.display_name


async def test_listing_never_crosses_a_tenant_boundary(store: Any) -> None:
    await store.tickets.insert(builders.ticket())
    await store.tickets.insert(builders.ticket(tenant_id=OTHER_TENANT, ticket_id="TCK-9999"))

    found, total = await store.tickets.list_for_customer(TENANT, CUSTOMER, limit=10)

    assert total == 1
    assert [t.ticket_id for t in found] == ["TCK-0001"]


# --- reads --------------------------------------------------------------------------


async def test_an_absent_record_is_none_rather_than_an_error(store: Any) -> None:
    assert await store.customers.get(TENANT, "CX-0000") is None
    assert await store.tickets.get(TENANT, "TCK-0000") is None
    assert await store.approvals.get(TENANT, "APR-0000") is None


async def test_the_empty_case_returns_an_empty_page_and_a_zero_total(store: Any) -> None:
    found, total = await store.services.list_for_customer(TENANT, CUSTOMER, limit=5)

    assert found == []
    assert total == 0


async def test_a_page_is_capped_and_the_total_still_reports_everything(store: Any) -> None:
    for index in range(7):
        await store.services.upsert(builders.service(service_id=f"SVC-{index:03d}"))

    found, total = await store.services.list_for_customer(TENANT, CUSTOMER, limit=3)

    assert len(found) == 3
    assert total == 7


async def test_orders_and_invoices_come_back_newest_first(store: Any) -> None:
    for days in (30, 1, 10):
        await store.orders.upsert(
            builders.order(order_id=f"ORD-{days}", placed_at=NOW - timedelta(days=days))
        )
        await store.invoices.upsert(
            builders.invoice(invoice_id=f"INV-{days}", issued_on=NOW - timedelta(days=days))
        )

    orders, _ = await store.orders.list_for_customer(TENANT, CUSTOMER, limit=10)
    invoices, _ = await store.invoices.list_for_customer(TENANT, CUSTOMER, limit=10)

    assert [o.order_id for o in orders] == ["ORD-1", "ORD-10", "ORD-30"]
    assert [i.invoice_id for i in invoices] == ["INV-1", "INV-10", "INV-30"]


async def test_a_specific_reference_filters_to_one_record(store: Any) -> None:
    await store.orders.upsert(builders.order(order_id="ORD-1"))
    await store.orders.upsert(builders.order(order_id="ORD-2"))

    found, total = await store.orders.list_for_customer(
        TENANT, CUSTOMER, limit=10, order_id="ORD-2"
    )

    assert [o.order_id for o in found] == ["ORD-2"]
    assert total == 1


async def test_an_upsert_replaces_rather_than_duplicating(store: Any) -> None:
    await store.services.upsert(builders.service(plan_name="Old plan"))
    await store.services.upsert(builders.service(plan_name="New plan"))

    found, total = await store.services.list_for_customer(TENANT, CUSTOMER, limit=10)

    assert total == 1
    assert found[0].plan_name == "New plan"


async def test_money_survives_storage_as_an_exact_integer(store: Any) -> None:
    await store.invoices.upsert(builders.invoice(total_minor=6300, outstanding_minor=1))

    found, _ = await store.invoices.list_for_customer(TENANT, CUSTOMER, limit=1)

    assert found[0].total_minor == 6300
    assert found[0].outstanding_minor == 1
    assert isinstance(found[0].outstanding_minor, int)


async def test_a_timestamp_comes_back_with_its_timezone(store: Any) -> None:
    await store.tickets.insert(builders.ticket())

    found = await store.tickets.get(TENANT, "TCK-0001")

    assert found is not None
    assert found.created_at.tzinfo is not None
    assert found.created_at == NOW


# --- passcode attempts --------------------------------------------------------------


async def test_a_failed_attempt_increments_and_a_success_resets(store: Any) -> None:
    await store.customers.upsert(builders.customer())

    after_failure = await store.customers.record_passcode_attempt(
        TENANT, CUSTOMER, success=False, now=NOW, max_attempts=5, lockout_s=900
    )
    assert after_failure is not None
    assert after_failure.passcode.failed_attempts == 1
    assert after_failure.passcode.locked_until is None

    after_success = await store.customers.record_passcode_attempt(
        TENANT, CUSTOMER, success=True, now=NOW, max_attempts=5, lockout_s=900
    )
    assert after_success is not None
    assert after_success.passcode.failed_attempts == 0


async def test_the_account_locks_exactly_at_the_attempt_limit(store: Any) -> None:
    await store.customers.upsert(builders.customer())

    for attempt in range(1, 6):
        updated = await store.customers.record_passcode_attempt(
            TENANT, CUSTOMER, success=False, now=NOW, max_attempts=5, lockout_s=900
        )
        assert updated is not None
        assert updated.passcode.failed_attempts == attempt
        if attempt < 5:
            assert updated.passcode.locked_until is None

    assert updated.passcode.locked_until == NOW + timedelta(seconds=900)


async def test_recording_an_attempt_for_an_unknown_customer_returns_none(store: Any) -> None:
    result = await store.customers.record_passcode_attempt(
        TENANT, "CX-0000", success=False, now=NOW, max_attempts=5, lockout_s=900
    )

    assert result is None


async def test_the_stored_passcode_hash_is_never_replaced_by_an_attempt(store: Any) -> None:
    await store.customers.upsert(builders.customer())
    original = (await store.customers.get(TENANT, CUSTOMER)).passcode.hash

    await store.customers.record_passcode_attempt(
        TENANT, CUSTOMER, success=False, now=NOW, max_attempts=5, lockout_s=900
    )

    assert (await store.customers.get(TENANT, CUSTOMER)).passcode.hash == original


# --- assignments --------------------------------------------------------------------


async def test_an_agent_is_assigned_only_to_what_was_assigned(store: Any) -> None:
    await store.assignments.assign(TENANT, "auth0|agent-7", "CX-5555", by="auth0|sup-1", now=NOW)

    assert await store.assignments.is_assigned(TENANT, "auth0|agent-7", "CX-5555")
    assert not await store.assignments.is_assigned(TENANT, "auth0|agent-7", "CX-1234")
    assert not await store.assignments.is_assigned(TENANT, "auth0|agent-8", "CX-5555")
    assert not await store.assignments.is_assigned(OTHER_TENANT, "auth0|agent-7", "CX-5555")


async def test_assigning_twice_is_harmless_and_revoking_reports_what_happened(
    store: Any,
) -> None:
    await store.assignments.assign(TENANT, "auth0|agent-7", "CX-5555", by="sup", now=NOW)
    await store.assignments.assign(TENANT, "auth0|agent-7", "CX-5555", by="sup", now=NOW)

    assert await store.assignments.list_for_agent(TENANT, "auth0|agent-7") == ["CX-5555"]
    assert await store.assignments.revoke(TENANT, "auth0|agent-7", "CX-5555") is True
    assert await store.assignments.revoke(TENANT, "auth0|agent-7", "CX-5555") is False


# --- approvals ----------------------------------------------------------------------


async def test_the_pending_queue_is_oldest_first_so_nothing_waits_forever(store: Any) -> None:
    for days in (0, 2, 1):
        await store.approvals.insert(
            builders.approval(request_id=f"APR-{days}", created_at=NOW - timedelta(days=days))
        )
    await store.approvals.insert(
        builders.approval(request_id="APR-done", state=ApprovalState.APPROVED)
    )

    found, total = await store.approvals.list_pending(TENANT, limit=10)

    assert [r.request_id for r in found] == ["APR-2", "APR-1", "APR-0"]
    assert total == 3


async def test_deciding_moves_the_request_and_records_who_decided(store: Any) -> None:
    await store.approvals.insert(builders.approval())
    decision = {
        "decided_by": "auth0|supervisor-1",
        "decided_by_role": "supervisor_approver",
        "decided_at": NOW,
        "decision": "approved",
        "note": "Duplicate charge confirmed against the invoice.",
    }

    decided = await store.approvals.decide(
        TENANT, "APR-0001", decision=decision, state=ApprovalState.APPROVED
    )

    assert decided is not None
    assert decided.state is ApprovalState.APPROVED
    assert decided.decision is not None
    assert decided.decision.decided_by == "auth0|supervisor-1"


async def test_only_the_first_of_two_simultaneous_decisions_wins(store: Any) -> None:
    # Two supervisors clicking at the same instant must produce one decision, not a
    # race won by whoever wrote last.
    await store.approvals.insert(builders.approval())
    decision = {
        "decided_by": "auth0|supervisor-1",
        "decided_by_role": "supervisor_approver",
        "decided_at": NOW,
        "decision": "approved",
    }

    first = await store.approvals.decide(
        TENANT, "APR-0001", decision=decision, state=ApprovalState.APPROVED
    )
    second = await store.approvals.decide(
        TENANT,
        "APR-0001",
        decision=dict(decision, decided_by="auth0|supervisor-2", decision="rejected"),
        state=ApprovalState.REJECTED,
    )

    assert first is not None
    assert second is None, "a decided request must not be decidable again"
    stored = await store.approvals.get(TENANT, "APR-0001")
    assert stored is not None
    assert stored.state is ApprovalState.APPROVED


async def test_deciding_an_unknown_request_returns_none(store: Any) -> None:
    assert (
        await store.approvals.decide(
            TENANT,
            "APR-nope",
            decision={
                "decided_by": "s",
                "decided_by_role": "supervisor_approver",
                "decided_at": NOW,
                "decision": "approved",
            },
            state=ApprovalState.APPROVED,
        )
        is None
    )


async def test_the_evidence_the_requester_saw_is_stored_with_the_request(store: Any) -> None:
    await store.approvals.insert(builders.approval())

    found = await store.approvals.get(TENANT, "APR-0001")

    assert found is not None
    assert found.evidence["invoice_id"] == "INV-2026-08"


# --- cases --------------------------------------------------------------------------


async def test_an_interrupted_case_is_the_one_offered_for_resume(store: Any) -> None:
    await store.cases.upsert(builders.case(case_id="CASE-active"))
    await store.cases.upsert(
        builders.case(
            case_id="CASE-old", status=CaseStatus.INTERRUPTED, updated_at=NOW - timedelta(hours=2)
        )
    )
    await store.cases.upsert(
        builders.case(case_id="CASE-recent", status=CaseStatus.INTERRUPTED, updated_at=NOW)
    )

    resumable = await store.cases.find_resumable(TENANT, CUSTOMER)

    assert resumable is not None
    assert resumable.case_id == "CASE-recent", "the most recent interruption is the one to resume"


async def test_a_closed_case_is_never_offered_for_resume(store: Any) -> None:
    await store.cases.upsert(builders.case(status=CaseStatus.CLOSED))

    assert await store.cases.find_resumable(TENANT, CUSTOMER) is None


# --- audit --------------------------------------------------------------------------


async def test_the_audit_head_starts_at_genesis_and_then_follows_the_last_record(
    store: Any,
) -> None:
    assert await store.audit.head(TENANT) == (0, "0" * 64)

    await store.audit.append(builders.audit(seq=1, entry_hash="a" * 64))
    await store.audit.append(
        builders.audit(seq=2, record_id="AUD-2", previous_hash="a" * 64, entry_hash="b" * 64)
    )

    assert await store.audit.head(TENANT) == (2, "b" * 64)


async def test_audit_heads_are_independent_per_tenant(store: Any) -> None:
    await store.audit.append(builders.audit(seq=1, entry_hash="a" * 64))

    assert await store.audit.head(OTHER_TENANT) == (0, "0" * 64)


async def test_audit_records_can_be_found_by_correlation_identifier(store: Any) -> None:
    await store.audit.append(builders.audit(seq=1, correlation_id="corr-a"))
    await store.audit.append(builders.audit(seq=2, record_id="AUD-2", correlation_id="corr-b"))

    found = await store.audit.list_recent(TENANT, limit=10, correlation_id="corr-b")

    assert [r.seq for r in found] == [2]


# --- outbox -------------------------------------------------------------------------


def event(sequence: int, **overrides: Any) -> DomainEvent:
    base: dict[str, Any] = {
        "event_id": f"evt-{sequence}",
        "type": EventType.APPROVAL_REQUESTED,
        "tenant_id": TENANT,
        "sequence": sequence,
        "occurred_at": NOW,
        "correlation_id": "corr-1",
        "subject": "approval_requests/APR-0001",
        "payload": {"state": "pending"},
    }
    base.update(overrides)
    return DomainEvent.model_validate(base)


async def test_sequences_are_monotonic_within_a_tenant_and_independent_across_them(
    store: Any,
) -> None:
    first = await store.outbox.next_sequence(TENANT)
    second = await store.outbox.next_sequence(TENANT)
    other = await store.outbox.next_sequence(OTHER_TENANT)

    assert (first, second) == (1, 2)
    assert other == 1


async def test_pending_events_are_returned_until_they_are_marked_published(
    store: Any,
) -> None:
    await store.outbox.add(event(1))
    await store.outbox.add(event(2))

    pending = await store.outbox.fetch_pending(limit=10)
    assert [e.sequence for e in pending] == [1, 2]

    await store.outbox.mark_published([e.event_id for e in pending])

    assert await store.outbox.fetch_pending(limit=10) == []


async def test_marking_nothing_published_is_harmless(store: Any) -> None:
    await store.outbox.mark_published([])


async def test_a_subscriber_can_replay_exactly_what_it_missed(store: Any) -> None:
    for sequence in (1, 2, 3):
        await store.outbox.add(event(sequence))
    await store.outbox.add(event(1, event_id="other-1", tenant_id=OTHER_TENANT))

    replayed = await store.outbox.replay_since(TENANT, after_sequence=1, limit=10)

    assert [e.sequence for e in replayed] == [2, 3]


# --- idempotency --------------------------------------------------------------------


async def test_the_first_reservation_is_new_and_a_repeat_is_in_progress(store: Any) -> None:
    state, result = await store.idempotency.reserve(
        TENANT, "tickets", "idem-1", "hash-a", now=RESERVED_AT, ttl_s=86_400
    )
    assert (state, result) == ("new", None)

    state, result = await store.idempotency.reserve(
        TENANT, "tickets", "idem-1", "hash-a", now=RESERVED_AT, ttl_s=86_400
    )
    assert (state, result) == ("in_progress", None)


async def test_a_completed_reservation_replays_the_original_result(store: Any) -> None:
    await store.idempotency.reserve(
        TENANT, "tickets", "idem-1", "hash-a", now=RESERVED_AT, ttl_s=86_400
    )
    await store.idempotency.complete(TENANT, "tickets", "idem-1", {"ticket_id": "TCK-1"})

    state, result = await store.idempotency.reserve(
        TENANT, "tickets", "idem-1", "hash-a", now=RESERVED_AT, ttl_s=86_400
    )

    assert state == "completed"
    assert result == {"ticket_id": "TCK-1"}


async def test_the_same_key_with_different_input_is_an_error_not_a_silent_replay(
    store: Any,
) -> None:
    await store.idempotency.reserve(
        TENANT, "tickets", "idem-1", "hash-a", now=RESERVED_AT, ttl_s=86_400
    )

    with pytest.raises(IdempotencyKeyReusedError):
        await store.idempotency.reserve(
            TENANT, "tickets", "idem-1", "hash-b", now=RESERVED_AT, ttl_s=86_400
        )


async def test_a_released_reservation_lets_a_genuine_retry_proceed(store: Any) -> None:
    await store.idempotency.reserve(
        TENANT, "tickets", "idem-1", "hash-a", now=RESERVED_AT, ttl_s=86_400
    )
    await store.idempotency.release(TENANT, "tickets", "idem-1")

    state, _ = await store.idempotency.reserve(
        TENANT, "tickets", "idem-1", "hash-a", now=RESERVED_AT, ttl_s=86_400
    )

    assert state == "new"


async def test_releasing_a_completed_key_does_not_lose_the_result(store: Any) -> None:
    await store.idempotency.reserve(
        TENANT, "tickets", "idem-1", "hash-a", now=RESERVED_AT, ttl_s=86_400
    )
    await store.idempotency.complete(TENANT, "tickets", "idem-1", {"ticket_id": "TCK-1"})
    await store.idempotency.release(TENANT, "tickets", "idem-1")

    state, result = await store.idempotency.reserve(
        TENANT, "tickets", "idem-1", "hash-a", now=RESERVED_AT, ttl_s=86_400
    )

    assert (state, result) == ("completed", {"ticket_id": "TCK-1"})


async def test_keys_are_namespaced_by_tenant_and_by_scope(store: Any) -> None:
    await store.idempotency.reserve(
        TENANT, "tickets", "idem-1", "hash-a", now=RESERVED_AT, ttl_s=86_400
    )

    other_tenant, _ = await store.idempotency.reserve(
        OTHER_TENANT, "tickets", "idem-1", "hash-a", now=RESERVED_AT, ttl_s=86_400
    )
    other_scope, _ = await store.idempotency.reserve(
        TENANT, "callbacks", "idem-1", "hash-a", now=RESERVED_AT, ttl_s=86_400
    )

    assert other_tenant == "new"
    assert other_scope == "new"


# --- lifecycle ----------------------------------------------------------------------


async def test_a_started_store_answers_its_readiness_probe(store: Any) -> None:
    await store.ping()


async def test_a_started_store_can_be_closed_twice_without_complaint(store: Any) -> None:
    # A shutdown path that raises on the second call turns a restart into an incident.
    await store.close()
    await store.close()


async def test_a_transaction_commits_its_writes(store: Any) -> None:
    async with store.transaction():
        await store.tickets.insert(builders.ticket())
        await store.audit.append(builders.audit())

    assert await store.tickets.get(TENANT, "TCK-0001") is not None
    assert (await store.audit.head(TENANT))[0] == 1


async def test_an_upsert_of_a_case_replaces_its_predecessor(store: Any) -> None:
    await store.cases.upsert(builders.case(tool_steps_used=1))
    await store.cases.upsert(builders.case(tool_steps_used=2))

    found = await store.cases.get(TENANT, "CASE-0001")

    assert found is not None
    assert found.tool_steps_used == 2


async def test_a_callback_is_stored_and_read_back(store: Any) -> None:
    await store.callbacks.insert(builders.callback())

    found = await store.callbacks.get(TENANT, "CB-0001")

    assert found is not None
    assert found.window == "morning"
    assert await store.callbacks.get(OTHER_TENANT, "CB-0001") is None


async def test_network_status_is_shared_across_the_customers_in_an_area(store: Any) -> None:
    await store.network.upsert(builders.network())

    assert (await store.network.get_for_area(TENANT, "AREA-EDI-04")) is not None
    assert (await store.network.get_for_area(TENANT, "AREA-NOWHERE")) is None
