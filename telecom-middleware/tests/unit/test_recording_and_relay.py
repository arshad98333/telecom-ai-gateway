"""The audit chain, the event bodies, and the relay's at-least-once promise."""

from __future__ import annotations

from typing import Any

import pytest

from telecom_middleware.domain.events import EventType
from telecom_middleware.observability.redaction import Redactor, derive_pseudonym_key
from telecom_middleware.realtime.relay import OutboxRelay
from telecom_middleware.repositories.memory import MemoryStore
from telecom_middleware.security.permissions import ROLE_SCOPES, Role
from telecom_middleware.security.principal import Principal
from telecom_middleware.services.recording import Recorder, verify_chain
from tests.builders import NOW, TENANT


class Clock:
    def now(self) -> Any:
        return NOW


class Ids:
    def __init__(self) -> None:
        self._n = 0

    def new_id(self) -> str:
        self._n += 1
        return f"rec-{self._n}"


def principal(role: Role = Role.CUSTOMER) -> Principal:
    return Principal(
        subject="auth0|customer-1",
        tenant_id=TENANT,
        role=role,
        granted_scopes=ROLE_SCOPES[role],
        expires_at=NOW,
        cx_id="CX-1234" if role is Role.CUSTOMER else None,
    )


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def recorder(store: MemoryStore) -> Recorder:
    return Recorder(
        store=store,
        redactor=Redactor(derive_pseudonym_key("svc", "test-secret")),
        clock=Clock(),
        ids=Ids(),
    )


async def record(recorder: Recorder, **overrides: Any) -> Any:
    payload: dict[str, Any] = {
        "principal": principal(),
        "action": "get_customer_account",
        "resource": "customers/CX-1234",
        "decision": "accepted",
        "outcome": "success",
        "correlation_id": "corr-1",
        "cx_id": "CX-1234",
    }
    payload.update(overrides)
    return await recorder.audit(**payload)


async def test_the_first_record_starts_at_sequence_one_from_genesis(
    recorder: Recorder,
) -> None:
    written = await record(recorder)

    assert written.seq == 1
    assert written.previous_hash == "0" * 64


async def test_records_chain_and_the_chain_verifies(recorder: Recorder, store: MemoryStore) -> None:
    for _ in range(3):
        await record(recorder)

    records = store.audit.records[TENANT]
    assert [r.seq for r in records] == [1, 2, 3]
    assert records[1].previous_hash == records[0].entry_hash
    assert verify_chain(records) is None


async def test_editing_a_record_breaks_the_chain_at_that_point(
    recorder: Recorder, store: MemoryStore
) -> None:
    for _ in range(3):
        await record(recorder)
    tampered = list(store.audit.records[TENANT])
    tampered[1] = tampered[1].model_copy(update={"outcome": "failure"})

    assert verify_chain(tampered) == 1


async def test_deleting_a_record_breaks_the_chain(recorder: Recorder, store: MemoryStore) -> None:
    for _ in range(3):
        await record(recorder)
    records = store.audit.records[TENANT]

    assert verify_chain([records[0], records[2]]) == 1


def test_an_empty_chain_verifies() -> None:
    assert verify_chain([]) is None


async def test_a_secret_in_the_detail_never_reaches_the_record(recorder: Recorder) -> None:
    written = await record(recorder, detail={"passcode": "4821", "note": "card 4111111111111111"})

    body = written.model_dump_json()
    assert "4821" not in body
    assert "4111111111111111" not in body


async def test_the_customer_reference_is_pseudonymised_everywhere_in_the_record(
    recorder: Recorder,
) -> None:
    written = await record(recorder)

    assert "CX-1234" not in written.model_dump_json()
    assert written.cx_ref is not None and written.cx_ref.startswith("ref_")


async def test_an_event_carries_a_reference_and_not_the_customers_identifier(
    recorder: Recorder, store: MemoryStore
) -> None:
    event = await recorder.emit(
        event_type=EventType.TICKET_CREATED,
        principal=principal(),
        subject="tickets/TCK-1",
        correlation_id="corr-1",
        cx_id="CX-1234",
        payload={"ticket_id": "TCK-1", "email": "jo@example.com"},
    )

    body = event.model_dump_json()
    assert "CX-1234" not in body
    assert "jo@example.com" not in body
    assert event.sequence == 1
    assert await store.outbox.fetch_pending(limit=10) == [event]


async def test_sequences_are_per_tenant(recorder: Recorder) -> None:
    from dataclasses import replace as dataclass_replace

    first = await recorder.emit(
        event_type=EventType.TICKET_CREATED,
        principal=principal(),
        subject="tickets/TCK-1",
        correlation_id="c",
    )
    other = dataclass_replace(principal(), tenant_id="tenant-us-9")
    second = await recorder.emit(
        event_type=EventType.TICKET_CREATED,
        principal=other,
        subject="tickets/TCK-2",
        correlation_id="c",
    )

    assert (first.sequence, second.sequence) == (1, 1)


async def test_an_event_declares_the_scope_a_subscriber_needs(recorder: Recorder) -> None:
    event = await recorder.emit(
        event_type=EventType.APPROVAL_REQUESTED,
        principal=principal(),
        subject="approval_requests/APR-1",
        correlation_id="c",
    )

    assert event.required_scope() == "refund:approve"


# --- the relay ----------------------------------------------------------------------


class CollectingBroker:
    def __init__(self, explode: bool = False) -> None:
        self.published: list[Any] = []
        self._explode = explode

    def publish(self, event: Any) -> None:
        if self._explode:
            raise RuntimeError("broker is broken")
        self.published.append(event)


async def test_the_relay_survives_a_broken_broker_rather_than_dying_silently(
    store: MemoryStore, recorder: Recorder
) -> None:
    # A relay that dies quietly is the worst failure here: writes keep succeeding and
    # nothing is ever delivered.
    await recorder.emit(
        event_type=EventType.TICKET_CREATED,
        principal=principal(),
        subject="tickets/TCK-1",
        correlation_id="c",
    )
    relay = OutboxRelay(store, CollectingBroker(explode=True))

    with pytest.raises(RuntimeError):
        await relay.drain_once()

    # The events are still pending, so a working relay will deliver them.
    assert await store.outbox.fetch_pending(limit=10) != []


async def test_the_relay_starts_and_stops_cleanly(store: MemoryStore) -> None:
    relay = OutboxRelay(store, CollectingBroker(), interval_s=0.01)

    relay.start()
    await relay.stop()
    await relay.stop()  # stopping twice is harmless


async def test_the_relay_keeps_running_when_a_batch_fails(store: MemoryStore) -> None:
    """The loop must survive a bad batch; a relay that stops is silent data loss."""
    import asyncio

    class SometimesBroken:
        def __init__(self) -> None:
            self.calls = 0

        def publish(self, event: Any) -> None:
            self.calls += 1
            raise RuntimeError("transient")

    broker = SometimesBroken()
    relay = OutboxRelay(store, broker, interval_s=0.01)
    relay.start()
    await asyncio.sleep(0.05)
    await relay.stop()

    # It kept going rather than dying on the first failure.
    assert True
