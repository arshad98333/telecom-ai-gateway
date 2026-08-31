"""Voice case state, so an interrupted call resumes instead of restarting.

The SOP names this failure directly: a caller drops mid-conversation and has to explain
everything again to a system that has forgotten them. The case document is updated at
every turn, marked interrupted the moment the line goes, and offered back only after
the customer re-authenticates.

Steps are bounded. A runaway case must not become an unbounded document, and the last
handful of turns is all a resume needs.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from telecom_middleware.api.dependencies import AppContextDep, CorrelationIdDep, requires
from telecom_middleware.api.schemas import CaseResponse, CaseStepRequest, UpsertCaseRequest
from telecom_middleware.domain.errors import NotFoundError
from telecom_middleware.domain.events import EventType
from telecom_middleware.domain.models import Case, CaseStatus, CaseStep
from telecom_middleware.security.access import require_account_access
from telecom_middleware.security.permissions import Scope
from telecom_middleware.security.principal import Principal

router = APIRouter(tags=["cases"])

CaseIdPath = Annotated[str, Path(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")]
CxIdPath = Annotated[str, Path(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")]

#: The most turns a case keeps. Older ones are dropped, not stored forever.
MAX_STEPS = 50

_STATUS_EVENTS = {
    CaseStatus.ACTIVE: EventType.CASE_STARTED,
    CaseStatus.INTERRUPTED: EventType.CASE_INTERRUPTED,
    CaseStatus.HANDED_OVER: EventType.CASE_HANDED_OVER,
    CaseStatus.CLOSED: EventType.CASE_CLOSED,
}


def _to_response(case: Case) -> CaseResponse:
    return CaseResponse(
        case_id=case.case_id,
        cx_id=case.cx_id,
        status=case.status,
        started_at=case.started_at,
        updated_at=case.updated_at,
        tool_steps_used=case.tool_steps_used,
        steps=[
            CaseStepRequest(intent=step.intent, tool=step.tool, outcome=step.outcome)
            for step in case.steps
        ],
        handover_reason=case.handover_reason,
    )


@router.put("/cases", response_model=CaseResponse, summary="Record or update case state")
async def upsert_case(
    body: UpsertCaseRequest,
    context: AppContextDep,
    correlation_id: CorrelationIdDep,
    principal: Annotated[Principal, Depends(requires(Scope.CASE_WRITE))],
) -> CaseResponse:
    await require_account_access(principal, body.cx_id, context.store.assignments)
    now = context.clock.now()
    existing = await context.store.cases.get(principal.tenant_id, body.case_id)

    steps = list(existing.steps) if existing else []
    tool_steps = existing.tool_steps_used if existing else 0
    if body.step is not None:
        steps.append(
            CaseStep(
                at=now, intent=body.step.intent, tool=body.step.tool, outcome=body.step.outcome
            )
        )
        # Keep the most recent window rather than growing without bound.
        steps = steps[-MAX_STEPS:]
        if body.step.tool:
            tool_steps += 1

    case = Case(
        tenant_id=principal.tenant_id,
        case_id=body.case_id,
        cx_id=body.cx_id,
        status=body.status,
        started_at=existing.started_at if existing else now,
        updated_at=now,
        steps=steps,
        tool_steps_used=tool_steps,
        consent_recorded_at=(
            now
            if body.consent_recorded and (existing is None or existing.consent_recorded_at is None)
            else (existing.consent_recorded_at if existing else None)
        ),
        handover_reason=body.handover_reason,
        closed_at=now if body.status is CaseStatus.CLOSED else None,
    )

    async with context.store.transaction():
        await context.store.cases.upsert(case)
        await context.recorder.audit(
            principal=principal,
            action="upsert_case",
            resource=f"cases/{case.case_id}",
            decision="accepted",
            outcome=str(case.status),
            correlation_id=correlation_id,
            cx_id=case.cx_id,
            case_id=case.case_id,
            detail={"tool_steps_used": case.tool_steps_used},
        )
        if existing is None or existing.status is not case.status:
            await context.recorder.emit(
                event_type=_STATUS_EVENTS[case.status],
                principal=principal,
                subject=f"cases/{case.case_id}",
                correlation_id=correlation_id,
                cx_id=case.cx_id,
                case_id=case.case_id,
                payload={"status": str(case.status)},
            )
    return _to_response(case)


@router.get("/cases/{case_id}", response_model=CaseResponse, summary="Read one case")
async def get_case(
    case_id: CaseIdPath,
    context: AppContextDep,
    principal: Annotated[Principal, Depends(requires(Scope.CASE_READ))],
) -> CaseResponse:
    case = await context.store.cases.get(principal.tenant_id, case_id)
    if case is None:
        raise NotFoundError("no such case")
    await require_account_access(principal, case.cx_id, context.store.assignments)
    return _to_response(case)


@router.post(
    "/customers/{cx_id}/cases/resume",
    response_model=CaseResponse,
    summary="Resume the most recently interrupted case, after re-authentication",
)
async def resume_case(
    cx_id: CxIdPath,
    context: AppContextDep,
    correlation_id: CorrelationIdDep,
    principal: Annotated[Principal, Depends(requires(Scope.CASE_WRITE))],
) -> CaseResponse:
    """Hand back an interrupted case.

    The caller reaches this only with a token issued after re-authentication, which is
    the SOP's rule: a dropped call may be resumed, but not without proving identity
    again. This service enforces the token; the voice agent enforces the passcode step
    that produced it.
    """
    await require_account_access(principal, cx_id, context.store.assignments)
    case = await context.store.cases.find_resumable(principal.tenant_id, cx_id)
    if case is None:
        raise NotFoundError("no interrupted case to resume")

    now = context.clock.now()
    resumed = case.model_copy(update={"status": CaseStatus.ACTIVE, "updated_at": now})
    async with context.store.transaction():
        await context.store.cases.upsert(resumed)
        await context.recorder.audit(
            principal=principal,
            action="resume_case",
            resource=f"cases/{case.case_id}",
            decision="accepted",
            outcome="resumed",
            correlation_id=correlation_id,
            cx_id=cx_id,
            case_id=case.case_id,
        )
        await context.recorder.emit(
            event_type=EventType.CASE_RESUMED,
            principal=principal,
            subject=f"cases/{case.case_id}",
            correlation_id=correlation_id,
            cx_id=cx_id,
            case_id=case.case_id,
            payload={"status": str(resumed.status), "steps_kept": len(resumed.steps)},
        )
    return _to_response(resumed)
