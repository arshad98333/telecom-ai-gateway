"""The live feed: what a supervisor sees, and what they must not."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from telecom_middleware.domain.events import DomainEvent, EventType
from telecom_middleware.realtime.broker import SUBSCRIBER_QUEUE_SIZE, EventBroker
from telecom_middleware.realtime.relay import OutboxRelay
from telecom_middleware.security.permissions import ROLE_SCOPES, Role, Scope
from telecom_middleware.security.principal import Principal
from tests.builders import NOW, TENANT
from tests.integration.conftest import CUSTOMER, Harness

API = "/api/v1"


def principal(
    role: Role = Role.SUPERVISOR_APPROVER, *, tenant: str = TENANT, scopes: Any = None
) -> Principal:
    from datetime import timedelta

    return Principal(
        subject=f"auth0|{role}",
        tenant_id=tenant,
        role=role,
        granted_scopes=scopes if scopes is not None else ROLE_SCOPES[role],
        expires_at=NOW + timedelta(minutes=10),
        cx_id=CUSTOMER if role is Role.CUSTOMER else None,
    )


def event(sequence: int = 1, **overrides: Any) -> DomainEvent:
    base: dict[str, Any] = {
        "event_id": f"evt-{sequence}",
        "type": EventType.APPROVAL_REQUESTED,
        "tenant_id": TENANT,
        "sequence": sequence,
        "occurred_at": NOW,
        "correlation_id": "corr-1",
        "subject": "approval_requests/APR-1",
        "payload": {"state": "pending"},
    }
    base.update(overrides)
    return DomainEvent.model_validate(base)


# --- the broker ---------------------------------------------------------------------


async def test_a_supervisor_receives_an_approval_event() -> None:
    broker = EventBroker()

    async with broker.subscribe(principal()) as subscriber:
        broker.publish(event())

        received = await asyncio.wait_for(subscriber.queue.get(), timeout=1)

    assert received.subject == "approval_requests/APR-1"


async def test_an_event_from_another_tenant_never_reaches_a_subscriber() -> None:
    broker = EventBroker()

    async with broker.subscribe(principal()) as subscriber:
        broker.publish(event(tenant_id="tenant-us-9"))

        assert subscriber.queue.empty()


async def test_a_subscriber_without_the_scope_does_not_receive_the_event() -> None:
    # A customer holds case:read but not refund:approve, so they never see the queue.
    broker = EventBroker()

    async with broker.subscribe(principal(Role.CUSTOMER)) as subscriber:
        broker.publish(event())

        assert subscriber.queue.empty()


async def test_scope_is_re_checked_per_event_not_only_at_subscribe_time() -> None:
    broker = EventBroker()
    narrowed = principal(scopes=frozenset({Scope.CASE_READ}))

    async with broker.subscribe(narrowed) as subscriber:
        broker.publish(event(type=EventType.CASE_STARTED, subject="cases/CASE-1"))
        broker.publish(event(sequence=2, event_id="evt-2"))  # approval: not permitted

        first = await asyncio.wait_for(subscriber.queue.get(), timeout=1)
        assert first.type is EventType.CASE_STARTED
        assert subscriber.queue.empty()


async def test_a_subscriber_that_falls_behind_is_dropped_rather_than_buffered_forever() -> None:
    # One stalled browser tab must not grow the server's memory until something dies.
    broker = EventBroker()

    async with broker.subscribe(principal()) as subscriber:
        for sequence in range(SUBSCRIBER_QUEUE_SIZE + 5):
            broker.publish(event(sequence=sequence + 1, event_id=f"evt-{sequence}"))

        assert subscriber.dropped is True
        assert broker.subscriber_count == 0


async def test_the_subscriber_limit_is_enforced() -> None:
    broker = EventBroker(max_subscribers=1)

    async with broker.subscribe(principal()):
        with pytest.raises(RuntimeError, match="subscriber limit"):
            async with broker.subscribe(principal()):
                pass


def test_an_event_renders_as_a_server_sent_event_with_its_sequence_as_the_id() -> None:
    rendered = event(sequence=41).to_sse()

    assert rendered.startswith("id: 41\nevent: approval.requested\ndata: {")
    assert rendered.endswith("\n\n")


# --- the relay ----------------------------------------------------------------------


async def test_the_relay_publishes_pending_events_and_marks_them(harness: Harness) -> None:
    broker = EventBroker()
    relay = OutboxRelay(harness.store, broker, batch_size=10)
    await harness.store.outbox.add(event())

    async with broker.subscribe(principal()) as subscriber:
        published = await relay.drain_once()

        assert published == 1
        assert (await asyncio.wait_for(subscriber.queue.get(), timeout=1)).sequence == 1

    assert await relay.drain_once() == 0, "a published event is not published twice"


async def test_the_relay_marks_only_after_the_fan_out(harness: Harness) -> None:
    # Marked first, a crash mid-batch would lose the event; marked after, it replays.
    broker = EventBroker()
    relay = OutboxRelay(harness.store, broker)
    await harness.store.outbox.add(event())

    assert await harness.store.outbox.fetch_pending(limit=10) != []
    await relay.drain_once()
    assert await harness.store.outbox.fetch_pending(limit=10) == []


# --- the endpoint -------------------------------------------------------------------


async def test_a_customer_cannot_open_the_stream_a_supervisor_uses(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    # The stream requires case:read; the events inside it are filtered again by type.
    response = await client.get(
        f"{API}/stream", headers=seeded.headers(permissions=["account:read"])
    )

    assert response.status_code == 403


async def test_the_stream_replays_exactly_what_a_reconnecting_client_missed(
    seeded: Harness,
) -> None:
    """Driven against the stream generator directly.

    An ASGI test transport buffers a streaming response, so driving this through an
    HTTP client would test the transport rather than the endpoint. The generator is the
    part with the logic in it.
    """
    from telecom_middleware.api.routes.stream import _event_stream

    seeded.context.broker = EventBroker()
    for sequence in (1, 2, 3):
        await seeded.store.outbox.add(event(sequence=sequence, event_id=f"evt-{sequence}"))

    class NeverDisconnected:
        async def is_disconnected(self) -> bool:
            return False

    stream = _event_stream(seeded.context, principal(), NeverDisconnected(), last_event_id=1)
    seen = [await anext(stream), await anext(stream)]
    await stream.aclose()

    assert [line.splitlines()[0] for line in seen] == ["id: 2", "id: 3"]


async def test_the_stream_sends_a_heartbeat_when_nothing_is_happening(
    seeded: Harness,
) -> None:
    # Without it, a proxy or load balancer closes an idle connection and the supervisor
    # silently stops receiving anything.
    from telecom_middleware.api.routes.stream import HEARTBEAT, _event_stream

    seeded.context.broker = EventBroker()
    seeded.context.settings = seeded.context.settings.model_copy(update={"sse_heartbeat_s": 0.01})

    class NeverDisconnected:
        async def is_disconnected(self) -> bool:
            return False

    stream = _event_stream(seeded.context, principal(), NeverDisconnected(), last_event_id=None)
    first = await asyncio.wait_for(anext(stream), timeout=2)
    await stream.aclose()

    assert first == HEARTBEAT


async def test_the_stream_stops_when_the_client_disconnects(seeded: Harness) -> None:
    from telecom_middleware.api.routes.stream import _event_stream

    seeded.context.broker = EventBroker()

    class Disconnected:
        async def is_disconnected(self) -> bool:
            return True

    stream = _event_stream(seeded.context, principal(), Disconnected(), last_event_id=None)

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(stream), timeout=2)
