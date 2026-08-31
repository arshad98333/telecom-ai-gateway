"""The approval workflow: where the stakeholders meet.

A customer or an agent raises a request and nothing moves. A supervisor sees it appear
live, reviews the same evidence the requester saw, and decides. The decision is
recorded with who made it, when, and on what basis, and the whole chain is one audit
trail from the request to the outcome.

Three properties are load-bearing and each has a test:

* raising a request moves no money, and the response says so on every read;
* the person who raised a request cannot be the person who decides it;
* two supervisors deciding at the same instant produce one decision, not a race.
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
    ApprovalResponse,
    ApprovalsResponse,
    DecideApprovalRequest,
    RequestRefundApprovalRequest,
)
from telecom_middleware.domain.errors import ConflictError, NotFoundError
from telecom_middleware.domain.events import EventType
from telecom_middleware.domain.models import (
    ApprovalAction,
    ApprovalRequest,
    ApprovalState,
)
from telecom_middleware.security.access import (
    require_account_access,
    require_approval_authority,
)
from telecom_middleware.security.permissions import Scope
from telecom_middleware.security.principal import Principal

router = APIRouter(tags=["approvals"])

CxIdPath = Annotated[str, Path(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")]
RequestIdPath = Annotated[str, Path(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")]

#: How long a request waits for a human before it expires unactioned. A request that
#: sits forever is worse than one that is refused: the customer is told nothing.
APPROVAL_WINDOW = timedelta(days=2)


def _to_response(request: ApprovalRequest, *, deduplicated: bool = False) -> ApprovalResponse:
    return ApprovalResponse(
        request_id=request.request_id,
        cx_id=request.cx_id,
        action=str(request.action),
        amount_minor=request.amount_minor,
        currency=request.currency,
        reason=request.reason,
        justification=request.justification,
        evidence=request.evidence,
        state=str(request.state),
        requested_by_role=request.requested_by_role,
        created_at=request.created_at,
        expires_at=request.expires_at,
        decision=None if request.decision is None else request.decision.model_dump(),  # type: ignore[arg-type]
        deduplicated=deduplicated,
    )


@router.post(
    "/customers/{cx_id}/refund-approvals",
    response_model=ApprovalResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ask a supervisor to approve a refund. Moves no money.",
)
async def request_refund_approval(
    cx_id: CxIdPath,
    body: RequestRefundApprovalRequest,
    context: AppContextDep,
    correlation_id: CorrelationIdDep,
    idempotency_key: IdempotencyKeyDep,
    response: Response,
    principal: Annotated[Principal, Depends(requires(Scope.REFUND_REQUEST))],
) -> ApprovalResponse:
    await require_account_access(principal, cx_id, context.store.assignments)

    async def create() -> dict[str, object]:
        now = context.clock.now()
        # The evidence is gathered here, from the record, rather than taken from the
        # caller: an approver must review what is true, not what the requester typed.
        invoices, _ = await context.store.invoices.list_for_customer(
            principal.tenant_id, cx_id, limit=1, invoice_id=body.invoice_id
        )
        if not invoices:
            raise NotFoundError("no such invoice")
        invoice = invoices[0]
        if body.currency is not invoice.currency:
            raise ConflictError(
                "refund currency does not match the invoice",
                detail={"invoice_currency": str(invoice.currency)},
            )
        if body.amount_minor > invoice.total_minor:
            raise ConflictError(
                "refund exceeds the invoice total",
                detail={"invoice_total_minor": invoice.total_minor},
            )

        request = ApprovalRequest(
            tenant_id=principal.tenant_id,
            request_id=f"APR-{context.ids.new_id()}",
            cx_id=cx_id,
            action=ApprovalAction.REFUND,
            amount_minor=body.amount_minor,
            currency=body.currency,
            reason=body.reason,
            justification=body.justification,
            evidence={
                "invoice_id": invoice.invoice_id,
                "invoice_total_minor": invoice.total_minor,
                "invoice_outstanding_minor": invoice.outstanding_minor,
                "invoice_state": str(invoice.state),
            },
            state=ApprovalState.PENDING,
            requested_by=principal.subject,
            requested_by_role=str(principal.role),
            created_at=now,
            expires_at=now + APPROVAL_WINDOW,
            case_id=body.case_id,
        )
        async with context.store.transaction():
            await context.store.approvals.insert(request)
            await context.recorder.audit(
                principal=principal,
                action="request_refund_approval",
                resource=f"approval_requests/{request.request_id}",
                decision="accepted",
                outcome="pending_approval",
                correlation_id=correlation_id,
                cx_id=cx_id,
                case_id=body.case_id,
                detail={
                    "amount_minor": body.amount_minor,
                    "currency": str(body.currency),
                    "reason": str(body.reason),
                },
            )
            await context.recorder.emit(
                event_type=EventType.APPROVAL_REQUESTED,
                principal=principal,
                subject=f"approval_requests/{request.request_id}",
                correlation_id=correlation_id,
                cx_id=cx_id,
                case_id=body.case_id,
                payload={
                    "request_id": request.request_id,
                    "action": str(request.action),
                    "amount_minor": request.amount_minor,
                    "currency": str(request.currency),
                    "state": str(request.state),
                },
            )
        return _to_response(request).model_dump(mode="json")

    result, replayed = await idempotent_write(
        context,
        tenant_id=principal.tenant_id,
        scope=f"refund-approvals:{cx_id}",
        key=idempotency_key,
        payload=body.model_dump(mode="json"),
        operation=create,
    )
    if replayed:
        response.status_code = status.HTTP_200_OK
    return ApprovalResponse.model_validate({**result, "deduplicated": replayed})


@router.get(
    "/approvals",
    response_model=ApprovalsResponse,
    summary="The supervisor queue: pending requests, oldest first",
)
async def list_pending(
    context: AppContextDep,
    principal: Annotated[Principal, Depends(requires(Scope.REFUND_APPROVE))],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
) -> ApprovalsResponse:
    found, total = await context.store.approvals.list_pending(principal.tenant_id, limit=limit)
    return ApprovalsResponse(
        approvals=[_to_response(request) for request in found],
        total_count=total,
        truncated=len(found) < total,
    )


@router.get(
    "/approvals/{request_id}", response_model=ApprovalResponse, summary="One approval request"
)
async def get_approval(
    request_id: RequestIdPath,
    context: AppContextDep,
    principal: Annotated[Principal, Depends(requires(Scope.REFUND_APPROVE))],
) -> ApprovalResponse:
    request = await context.store.approvals.get(principal.tenant_id, request_id)
    if request is None:
        raise NotFoundError("no such approval request")
    return _to_response(request)


@router.post(
    "/approvals/{request_id}/decision",
    response_model=ApprovalResponse,
    summary="Approve or reject a pending request",
)
async def decide_approval(
    request_id: RequestIdPath,
    body: DecideApprovalRequest,
    context: AppContextDep,
    correlation_id: CorrelationIdDep,
    principal: Annotated[Principal, Depends(requires(Scope.REFUND_APPROVE))],
) -> ApprovalResponse:
    request = await context.store.approvals.get(principal.tenant_id, request_id)
    if request is None:
        raise NotFoundError("no such approval request")

    # Authority, separation of duties, and the amount limit, all before anything moves.
    require_approval_authority(principal, request)

    now = context.clock.now()
    new_state = ApprovalState.APPROVED if body.decision == "approved" else ApprovalState.REJECTED
    decision = {
        "decided_by": principal.subject,
        "decided_by_role": str(principal.role),
        "decided_at": now,
        "decision": body.decision,
        "note": body.note,
    }

    async with context.store.transaction():
        decided = await context.store.approvals.decide(
            principal.tenant_id, request_id, decision=decision, state=new_state
        )
        if decided is None:
            # Someone else decided it between the read and the write. Not an error in
            # this service; a fact the caller needs, with the current state to look at.
            raise ConflictError(
                "this request was decided by someone else",
                detail={"request_id": request_id},
            )
        await context.recorder.audit(
            principal=principal,
            action="decide_approval",
            resource=f"approval_requests/{request_id}",
            decision="accepted",
            outcome=body.decision,
            correlation_id=correlation_id,
            cx_id=decided.cx_id,
            case_id=decided.case_id,
            detail={
                "amount_minor": decided.amount_minor,
                "requested_by_role": decided.requested_by_role,
                "evidence": decided.evidence,
            },
        )
        await context.recorder.emit(
            event_type=EventType.APPROVAL_DECIDED,
            principal=principal,
            subject=f"approval_requests/{request_id}",
            correlation_id=correlation_id,
            cx_id=decided.cx_id,
            case_id=decided.case_id,
            payload={
                "request_id": request_id,
                "state": str(decided.state),
                "decision": body.decision,
            },
        )
    return _to_response(decided)
