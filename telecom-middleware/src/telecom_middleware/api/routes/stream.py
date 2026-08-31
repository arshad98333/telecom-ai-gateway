"""Server-sent events: the supervisor's queue, live.

SSE rather than WebSockets because the traffic is one-way, it survives proxies that
mangle upgrades, and browsers reconnect on their own with ``Last-Event-ID`` — which is
exactly the replay handle the outbox already provides.

Authorization is applied twice: once to open the stream, and again for every event, so
a subscriber never receives something their permissions no longer cover.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse

from telecom_middleware.api.context import AppContext
from telecom_middleware.api.dependencies import AppContextDep, requires
from telecom_middleware.domain.errors import RateLimitedError
from telecom_middleware.security.permissions import Scope
from telecom_middleware.security.principal import Principal

router = APIRouter(tags=["realtime"])

#: Sent when nothing has happened, so proxies and load balancers keep the connection.
HEARTBEAT = ": heartbeat\n\n"
MAX_REPLAY = 500


async def _event_stream(
    context: AppContext, principal: Principal, request: Request, last_event_id: int | None
) -> AsyncGenerator[str, None]:
    heartbeat_s = context.settings.sse_heartbeat_s

    if last_event_id is not None:
        # A reconnecting subscriber gets exactly what it missed, from the outbox.
        missed = await context.store.outbox.replay_since(
            principal.tenant_id, after_sequence=last_event_id, limit=MAX_REPLAY
        )
        for event in missed:
            if event.tenant_id == principal.tenant_id and principal.has(
                Scope(event.required_scope())
            ):
                yield event.to_sse()

    async with context.broker.subscribe(principal) as subscriber:
        while True:
            if await request.is_disconnected():
                return
            try:
                event = await asyncio.wait_for(subscriber.queue.get(), timeout=heartbeat_s)
            except TimeoutError:
                yield HEARTBEAT
                continue
            # Re-checked here as well as in the broker: the permission may have changed
            # between the fan-out and this subscriber being scheduled.
            if subscriber.may_receive(event):
                yield event.to_sse()


@router.get("/stream", summary="Live events for this tenant, as server-sent events")
async def stream(
    request: Request,
    context: AppContextDep,
    principal: Annotated[Principal, Depends(requires(Scope.CASE_READ))],
    last_event_id: Annotated[int | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    if context.broker is None:  # pragma: no cover - only when realtime is disabled
        raise RateLimitedError("the realtime stream is not enabled on this deployment")
    if context.broker.subscriber_count >= context.settings.sse_max_subscribers:
        raise RateLimitedError("too many live subscribers")

    return StreamingResponse(
        _event_stream(context, principal, request, last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Nginx buffers by default, which would hold every event until the buffer
            # fills and make a live feed anything but live.
            "X-Accel-Buffering": "no",
        },
    )
