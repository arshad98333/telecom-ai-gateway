"""Startup, shutdown, and the branches that only appear when something goes sideways."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from telecom_middleware.api.app import build_app
from telecom_middleware.api.container import build_context
from telecom_middleware.api.idempotent import IN_PROGRESS_TITLE, idempotent_write
from telecom_middleware.config.settings import load_settings
from telecom_middleware.domain.errors import InvalidInputError, RateLimitedError
from telecom_middleware.realtime.broker import EventBroker
from telecom_middleware.repositories.memory import MemoryStore
from telecom_middleware.security.permissions import Role
from tests.integration.conftest import BASE_ENV, CUSTOMER, Harness, MovableClock, SequentialIds

API = "/api/v1"


async def test_the_realtime_layer_starts_and_stops_with_the_application() -> None:
    settings = load_settings(
        dict(
            BASE_ENV,
            TELECOM_MW_CHANGE_STREAM_ENABLED="true",
            TELECOM_MW_OUTBOX_POLL_INTERVAL_S="0.05",
        )
    )
    store = MemoryStore()
    context = build_context(
        settings, store=store, clock=MovableClock(), ids=SequentialIds("id"), configure_logs=False
    )
    app = build_app(context, start_realtime=True)

    async with app.router.lifespan_context(app):
        assert context.broker is not None
        assert context.broker.subscriber_count == 0

    # Shut down cleanly: nothing is left feeding a closed store.
    assert context.broker is not None


async def test_the_broker_consumes_a_change_feed_until_it_is_stopped() -> None:
    from telecom_middleware.domain.events import DomainEvent, EventType
    from tests.builders import NOW, TENANT

    broker = EventBroker()
    delivered: asyncio.Queue[Any] = asyncio.Queue()

    async def source() -> Any:
        yield DomainEvent(
            event_id="evt-1",
            type=EventType.CASE_STARTED,
            tenant_id=TENANT,
            sequence=1,
            occurred_at=NOW,
            correlation_id="c",
            subject="cases/CASE-1",
        )
        await delivered.put(True)
        # Block until the broker's task is cancelled, without spinning.
        await asyncio.Event().wait()

    broker.start(source())
    await asyncio.wait_for(delivered.get(), timeout=2)
    await broker.stop()
    await broker.stop()  # stopping twice is harmless


async def test_the_memory_store_delivers_to_a_live_watcher() -> None:
    from telecom_middleware.domain.events import DomainEvent, EventType
    from tests.builders import NOW, TENANT

    store = MemoryStore()
    watcher = store.watch()
    consume = asyncio.create_task(anext(watcher))
    await asyncio.sleep(0)  # let the watcher register

    await store.publish(
        DomainEvent(
            event_id="evt-1",
            type=EventType.CASE_STARTED,
            tenant_id=TENANT,
            sequence=1,
            occurred_at=NOW,
            correlation_id="c",
            subject="cases/CASE-1",
        )
    )

    event = await asyncio.wait_for(consume, timeout=2)
    assert event.event_id == "evt-1"
    await watcher.aclose()


# --- idempotency edges --------------------------------------------------------------


async def test_a_write_with_no_key_names_the_header_it_wants(harness: Harness) -> None:
    async def never_called() -> dict[str, Any]:
        raise AssertionError("the operation must not run without a key")

    with pytest.raises(InvalidInputError) as caught:
        await idempotent_write(
            harness.context,
            tenant_id="tenant-eu-1",
            scope="tickets",
            key=None,
            payload={},
            operation=never_called,
        )

    assert caught.value.detail["header"] == "Idempotency-Key"


async def test_a_repeat_while_the_first_call_is_running_is_told_to_wait(
    harness: Harness,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow() -> dict[str, Any]:
        started.set()
        await release.wait()
        return {"ticket_id": "TCK-1"}

    first = asyncio.create_task(
        idempotent_write(
            harness.context,
            tenant_id="tenant-eu-1",
            scope="tickets",
            key="idem-slow-0001",
            payload={"a": 1},
            operation=slow,
        )
    )
    await started.wait()

    async def quick() -> dict[str, Any]:
        raise AssertionError("a second execution must not start")

    with pytest.raises(RateLimitedError) as caught:
        await idempotent_write(
            harness.context,
            tenant_id="tenant-eu-1",
            scope="tickets",
            key="idem-slow-0001",
            payload={"a": 1},
            operation=quick,
        )

    assert caught.value.title == IN_PROGRESS_TITLE
    release.set()
    result, replayed = await first
    assert result == {"ticket_id": "TCK-1"}
    assert replayed is False


async def test_a_failed_operation_frees_its_key(harness: Harness) -> None:
    async def failing() -> dict[str, Any]:
        raise RuntimeError("backend fell over")

    with pytest.raises(RuntimeError):
        await idempotent_write(
            harness.context,
            tenant_id="tenant-eu-1",
            scope="tickets",
            key="idem-fail-0001",
            payload={"a": 1},
            operation=failing,
        )

    async def succeeding() -> dict[str, Any]:
        return {"ticket_id": "TCK-2"}

    result, replayed = await idempotent_write(
        harness.context,
        tenant_id="tenant-eu-1",
        scope="tickets",
        key="idem-fail-0001",
        payload={"a": 1},
        operation=succeeding,
    )

    assert result == {"ticket_id": "TCK-2"}
    assert replayed is False


# --- approval edges -----------------------------------------------------------------


async def test_reading_one_approval_request(client: httpx.AsyncClient, seeded: Harness) -> None:
    supervisor = seeded.headers(role=Role.SUPERVISOR_APPROVER, cx_id=None)

    found = await client.get(f"{API}/approvals/APR-seed-0001", headers=supervisor)
    missing = await client.get(f"{API}/approvals/APR-nope", headers=supervisor)

    assert found.json()["request_id"] == "APR-seed-0001"
    assert missing.status_code == 403


async def test_a_refund_against_an_unknown_invoice_is_refused(
    client: httpx.AsyncClient, seeded: Harness
) -> None:
    response = await client.post(
        f"{API}/customers/{CUSTOMER}/refund-approvals",
        headers={**seeded.headers(), "Idempotency-Key": "idem-refund-unknown"},
        json={
            "cx_id": CUSTOMER,
            "invoice_id": "INV-NOPE",
            "amount_minor": 100,
            "currency": "GBP",
            "reason": "goodwill",
            "justification": "There is no such invoice.",
        },
    )

    assert response.status_code == 403


async def test_an_unexpected_failure_answers_with_a_generic_problem(harness: Harness) -> None:
    """The message is dropped on purpose: it is the most likely thing to leak."""

    async def explode(tenant_id: str, cx_id: str) -> None:
        del tenant_id, cx_id
        raise RuntimeError("connection string mongodb://user:hunter2@host is invalid")

    harness.store.customers.get = explode  # type: ignore[method-assign]

    # raise_app_exceptions=False so the transport returns the handler's response the way
    # a real server does, instead of re-raising for the test runner.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=harness.app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as isolated:
        response = await isolated.get(f"{API}/customers/{CUSTOMER}", headers=harness.headers())

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "hunter2" not in response.text
    assert response.headers["X-Correlation-Id"]
