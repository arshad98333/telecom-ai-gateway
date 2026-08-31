"""Fan-out of events to live subscribers, with authorization applied per event.

One watcher per process reads the change stream; the broker copies each event to the
subscribers entitled to it. Two rules make this safe and bounded.

**Authorization is re-applied for every event, not only at subscribe time.** A token
that loses a permission mid-stream, or a supervisor moved to another tenant, stops
receiving what they may no longer see, without waiting for the connection to drop.

**A slow subscriber is dropped, not buffered forever.** Each subscriber has a bounded
queue; when it fills, that subscriber is disconnected and reconnects with a
``Last-Event-ID``, replaying from the outbox. One stalled browser tab must not grow the
server's memory until something dies.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from telecom_middleware.domain.events import DomainEvent
from telecom_middleware.observability.logging import get_logger
from telecom_middleware.security.permissions import Scope
from telecom_middleware.security.principal import Principal

logger = get_logger(__name__)

#: How many events a subscriber may fall behind before it is dropped and told to replay.
SUBSCRIBER_QUEUE_SIZE = 100


# Identity-hashed on purpose: two subscribers with the same principal are still two
# connections, and each must get its own copy of every event.
@dataclass(slots=True, eq=False)
class Subscriber:
    principal: Principal
    queue: asyncio.Queue[DomainEvent] = field(
        default_factory=lambda: asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
    )
    dropped: bool = False

    def may_receive(self, event: DomainEvent) -> bool:
        """Tenant first, then the scope the event type requires."""
        if event.tenant_id != self.principal.tenant_id:
            return False
        try:
            required = Scope(event.required_scope())
        except ValueError:  # pragma: no cover - an event type with no mapped scope
            return False
        return self.principal.has(required)


class EventBroker:
    """In-process fan-out. One instance per replica."""

    def __init__(self, *, max_subscribers: int = 500) -> None:
        self._subscribers: set[Subscriber] = set()
        self._max_subscribers = max_subscribers
        self._task: asyncio.Task[None] | None = None

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event: DomainEvent) -> None:
        """Offer an event to every entitled subscriber. Never blocks on a slow one."""
        for subscriber in list(self._subscribers):
            if not subscriber.may_receive(event):
                continue
            try:
                subscriber.queue.put_nowait(event)
            except asyncio.QueueFull:
                # Dropping is the honest outcome: the subscriber reconnects and replays
                # from the outbox, rather than the server growing until it dies.
                subscriber.dropped = True
                self._subscribers.discard(subscriber)
                logger.warning("subscriber_dropped_behind", tenant_id=event.tenant_id)

    @contextlib.asynccontextmanager
    async def subscribe(self, principal: Principal) -> AsyncIterator[Subscriber]:
        """Register a subscriber for the life of one connection."""
        if len(self._subscribers) >= self._max_subscribers:
            raise RuntimeError("subscriber limit reached")
        subscriber = Subscriber(principal=principal)
        self._subscribers.add(subscriber)
        try:
            yield subscriber
        finally:
            self._subscribers.discard(subscriber)

    async def run(self, source: Any) -> None:
        """Feed the broker from the store's change stream until cancelled."""
        async for event in source:
            self.publish(event)

    def start(self, source: Any) -> None:
        self._task = asyncio.create_task(self.run(source))

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
