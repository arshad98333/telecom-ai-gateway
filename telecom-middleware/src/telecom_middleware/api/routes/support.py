"""Support writes: tickets and callbacks.

Both are low-risk actions a customer or an agent may take without approval, and both are
cancellable for a window afterwards, which is what makes them low risk. Both require an
idempotency key, because a voice agent that retries after a timeout must not leave two
tickets behind.

Each write, its audit record and its event commit together in one transaction. A ticket
with no audit record, or an event for a ticket that rolled back, are both states this
service cannot reach.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status

from telecom_middleware.api.dependencies import (
    AppContextDep,
    CorrelationIdDep,
    IdempotencyKeyDep,
    requires,
)
from telecom_middleware.api.idempotent import idempotent_write
from telecom_middleware.api.schemas import (
    MAX_PAGE_SIZE,
    CallbackResponse,
    CreateTicketRequest,
    ScheduleCallbackRequest,
    TicketResponse,
    TicketsResponse,
)
from telecom_middleware.domain.events import EventType
from telecom_middleware.domain.models import Callback, Ticket, TicketState
from telecom_middleware.security.access import require_account_access
from telecom_middleware.security.permissions import Scope
from telecom_middleware.security.principal import Principal

router = APIRouter(prefix="/customers", tags=["support"])

CxIdPath = Annotated[str, Path(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")]

#: How long a low-risk write stays cancellable. The reason these need no approval.
TICKET_CANCELLATION_WINDOW = timedelta(minutes=15)
CALLBACK_CANCELLATION_LEAD = timedelta(hours=4)


@router.post(
    "/{cx_id}/tickets",
    response_model=TicketResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Raise a support ticket",
)
async def create_ticket(
    cx_id: CxIdPath,
    body: CreateTicketRequest,
    context: AppContextDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    response: Response,
    principal: Annotated[Principal, Depends(requires(Scope.TICKET_WRITE))],
) -> TicketResponse:
    await require_account_access(principal, cx_id, context.store.assignments)

    async def create() -> dict[str, object]:
        now = context.clock.now()
        ticket = Ticket(
            tenant_id=principal.tenant_id,
            ticket_id=f"TCK-{context.ids.new_id()}",
            cx_id=cx_id,
            category=body.category,
            subject=body.subject,
            description=body.description,
            priority=body.priority,
            state=TicketState.OPEN,
            created_at=now,
            created_by=principal.subject,
            updated_at=now,
            cancellable_until=now + TICKET_CANCELLATION_WINDOW,
            case_id=body.case_id,
        )
        async with context.store.transaction():
            await context.store.tickets.insert(ticket)
            await context.recorder.audit(
                principal=principal,
                action="create_support_ticket",
                resource=f"tickets/{ticket.ticket_id}",
                decision="accepted",
                outcome="created",
                correlation_id=correlation_id,
                cx_id=cx_id,
                case_id=body.case_id,
                detail={"category": str(body.category), "priority": str(body.priority)},
            )
            await context.recorder.emit(
                event_type=EventType.TICKET_CREATED,
                principal=principal,
                subject=f"tickets/{ticket.ticket_id}",
                correlation_id=correlation_id,
                cx_id=cx_id,
                case_id=body.case_id,
                payload={
                    "ticket_id": ticket.ticket_id,
                    "category": str(ticket.category),
                    "priority": str(ticket.priority),
                    "state": str(ticket.state),
                },
            )
        return TicketResponse(
            ticket_id=ticket.ticket_id,
            cx_id=ticket.cx_id,
            category=ticket.category,
            subject=ticket.subject,
            state=ticket.state,
            priority=ticket.priority,
            created_at=ticket.created_at,
            cancellable_until=ticket.cancellable_until,
        ).model_dump(mode="json")

    result, replayed = await idempotent_write(
        context,
        tenant_id=principal.tenant_id,
        scope=f"tickets:{cx_id}",
        key=idempotency_key,
        payload=body.model_dump(mode="json"),
        operation=create,
    )
    if replayed:
        # A replay created nothing, so it is not a 201.
        response.status_code = status.HTTP_200_OK
    return TicketResponse.model_validate({**result, "deduplicated": replayed})


@router.get(
    "/{cx_id}/tickets", response_model=TicketsResponse, summary="A customer's recent tickets"
)
async def list_tickets(
    cx_id: CxIdPath,
    context: AppContextDep,
    principal: Annotated[Principal, Depends(requires(Scope.TICKET_READ))],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 5,
) -> TicketsResponse:
    await require_account_access(principal, cx_id, context.store.assignments)
    found, total = await context.store.tickets.list_for_customer(
        principal.tenant_id, cx_id, limit=limit
    )
    return TicketsResponse(
        tickets=[
            TicketResponse(
                ticket_id=t.ticket_id,
                cx_id=t.cx_id,
                category=t.category,
                subject=t.subject,
                state=t.state,
                priority=t.priority,
                created_at=t.created_at,
                cancellable_until=t.cancellable_until,
            )
            for t in found
        ],
        total_count=total,
        truncated=len(found) < total,
    )


@router.post(
    "/{cx_id}/callbacks",
    response_model=CallbackResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Book a callback",
)
async def schedule_callback(
    cx_id: CxIdPath,
    body: ScheduleCallbackRequest,
    context: AppContextDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    response: Response,
    principal: Annotated[Principal, Depends(requires(Scope.CALLBACK_WRITE))],
) -> CallbackResponse:
    await require_account_access(principal, cx_id, context.store.assignments)

    async def create() -> dict[str, object]:
        now = context.clock.now()
        callback = Callback(
            tenant_id=principal.tenant_id,
            callback_id=f"CB-{context.ids.new_id()}",
            cx_id=cx_id,
            scheduled_for=body.preferred_date,
            window=body.window,
            reason=body.reason,
            state="scheduled",
            created_at=now,
            created_by=principal.subject,
            # Cancellable until shortly before it happens, never after: a cancellation
            # that arrives once the agent has dialled is not a cancellation.
            cancellable_until=max(now, body.preferred_date - CALLBACK_CANCELLATION_LEAD),
        )
        async with context.store.transaction():
            await context.store.callbacks.insert(callback)
            await context.recorder.audit(
                principal=principal,
                action="schedule_callback",
                resource=f"callbacks/{callback.callback_id}",
                decision="accepted",
                outcome="scheduled",
                correlation_id=correlation_id,
                cx_id=cx_id,
                case_id=body.case_id,
                detail={"window": str(body.window)},
            )
            await context.recorder.emit(
                event_type=EventType.CALLBACK_SCHEDULED,
                principal=principal,
                subject=f"callbacks/{callback.callback_id}",
                correlation_id=correlation_id,
                cx_id=cx_id,
                case_id=body.case_id,
                payload={
                    "callback_id": callback.callback_id,
                    "window": str(callback.window),
                    "scheduled_for": callback.scheduled_for.isoformat(),
                },
            )
        return CallbackResponse(
            callback_id=callback.callback_id,
            cx_id=callback.cx_id,
            scheduled_for=callback.scheduled_for,
            window=callback.window,
            cancellable_until=callback.cancellable_until,
        ).model_dump(mode="json")

    result, replayed = await idempotent_write(
        context,
        tenant_id=principal.tenant_id,
        scope=f"callbacks:{cx_id}",
        key=idempotency_key,
        payload=body.model_dump(mode="json"),
        operation=create,
    )
    if replayed:
        response.status_code = status.HTTP_200_OK
    return CallbackResponse.model_validate({**result, "deduplicated": replayed})
