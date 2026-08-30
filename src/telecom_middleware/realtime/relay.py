"""The outbox relay: publish what was written, mark it, repeat.

The relay exists so a consumer never has to poll the database, and so an event survives
a crash between the write and the delivery. It is deliberately at-least-once: an event
may be delivered twice after a restart, so every consumer must tolerate a repeat. The
event's sequence makes that easy - a consumer that has seen sequence 41 ignores it a
second time.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from telecom_middleware.observability.logging import get_logger

logger = get_logger(__name__)


class OutboxRelay:
    """Drains pending events and hands them to the broker."""

    def __init__(
        self, store: Any, broker: Any, *, batch_size: int = 100, interval_s: float = 1.0
    ) -> None:
        self._store = store
        self._broker = broker
        self._batch_size = batch_size
        self._interval_s = interval_s
        self._task: asyncio.Task[None] | None = None

    async def drain_once(self) -> int:
        """Publish one batch. Returns how many events were published."""
        pending = await self._store.outbox.fetch_pending(limit=self._batch_size)
        if not pending:
            return 0
        for event in pending:
            self._broker.publish(event)
        # Marked only after the fan-out, so a crash mid-batch replays rather than loses.
        await self._store.outbox.mark_published([event.event_id for event in pending])
        return len(pending)

    async def run(self) -> None:
        while True:
            try:
                published = await self.drain_once()
            except Exception:
                logger.exception("outbox_relay_failed")
                published = 0
            await asyncio.sleep(self._interval_s if published == 0 else 0)

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
