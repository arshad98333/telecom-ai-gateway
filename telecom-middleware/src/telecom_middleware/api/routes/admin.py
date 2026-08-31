"""Administration: assignments, and the audit trail.

Two different stakeholders live here. A supervisor manages who may act on which
accounts, which is the lever that makes agent access finite. A security administrator
reads the audit trail and verifies its chain, and holds no customer-data permission at
all - reading bills is not part of administering security.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from telecom_middleware.api.dependencies import AppContextDep, CorrelationIdDep, requires
from telecom_middleware.api.schemas import (
    AssignmentRequest,
    AssignmentsResponse,
    AuditRecordResponse,
    AuditResponse,
)
from telecom_middleware.domain.errors import NotFoundError
from telecom_middleware.security.permissions import Scope
from telecom_middleware.security.principal import Principal
from telecom_middleware.services.recording import verify_chain

router = APIRouter(tags=["administration"])

AgentSubPath = Annotated[str, Path(min_length=1, max_length=128)]
CxIdPath = Annotated[str, Path(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")]


@router.post(
    "/assignments",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Assign an account to an agent",
)
async def create_assignment(
    body: AssignmentRequest,
    context: AppContextDep,
    correlation_id: CorrelationIdDep,
    principal: Annotated[Principal, Depends(requires(Scope.ASSIGNMENT_WRITE))],
) -> None:
    async with context.store.transaction():
        await context.store.assignments.assign(
            principal.tenant_id,
            body.agent_sub,
            body.cx_id,
            by=principal.subject,
            now=context.clock.now(),
        )
        await context.recorder.audit(
            principal=principal,
            action="assign_account",
            resource=f"agent_assignments/{body.agent_sub}",
            decision="accepted",
            outcome="assigned",
            correlation_id=correlation_id,
            cx_id=body.cx_id,
        )


@router.delete(
    "/assignments/{agent_sub}/{cx_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an assignment",
)
async def revoke_assignment(
    agent_sub: AgentSubPath,
    cx_id: CxIdPath,
    context: AppContextDep,
    correlation_id: CorrelationIdDep,
    principal: Annotated[Principal, Depends(requires(Scope.ASSIGNMENT_WRITE))],
) -> None:
    async with context.store.transaction():
        revoked = await context.store.assignments.revoke(principal.tenant_id, agent_sub, cx_id)
        if not revoked:
            raise NotFoundError("no such assignment")
        await context.recorder.audit(
            principal=principal,
            action="revoke_assignment",
            resource=f"agent_assignments/{agent_sub}",
            decision="accepted",
            outcome="revoked",
            correlation_id=correlation_id,
            cx_id=cx_id,
        )


@router.get(
    "/assignments/{agent_sub}",
    response_model=AssignmentsResponse,
    summary="What an agent may act on",
)
async def list_assignments(
    agent_sub: AgentSubPath,
    context: AppContextDep,
    principal: Annotated[Principal, Depends(requires(Scope.ASSIGNMENT_READ))],
) -> AssignmentsResponse:
    accounts = await context.store.assignments.list_for_agent(principal.tenant_id, agent_sub)
    return AssignmentsResponse(agent_sub=agent_sub, accounts=accounts)


@router.get(
    "/audit",
    response_model=AuditResponse,
    summary="Recent audit records, with the chain verified",
)
async def read_audit(
    context: AppContextDep,
    principal: Annotated[Principal, Depends(requires(Scope.AUDIT_READ))],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    correlation_id: Annotated[str | None, Query(max_length=128)] = None,
) -> AuditResponse:
    """Read the trail and say whether it verifies.

    Returning the records without the verdict would leave every reader to work out for
    themselves whether the chain holds, which nobody does. The verdict is computed on
    the window returned, and a broken chain is reported by index.
    """
    records = await context.store.audit.list_recent(
        principal.tenant_id, limit=limit, correlation_id=correlation_id
    )
    oldest_first = list(reversed(records))
    # Only a window starting at the first record can be verified against genesis; a
    # later window is checked for internal consistency by the same function.
    broken = verify_chain(oldest_first) if oldest_first and oldest_first[0].seq == 1 else None
    return AuditResponse(
        records=[
            AuditRecordResponse(
                seq=record.seq,
                record_id=record.record_id,
                at=record.at,
                correlation_id=record.correlation_id,
                actor_role=record.actor_role,
                cx_ref=record.cx_ref,
                action=record.action,
                resource=record.resource,
                decision=record.decision,
                outcome=record.outcome,
                failure_reason=record.failure_reason,
                entry_hash=record.entry_hash,
            )
            for record in records
        ],
        chain_broken_at=broken,
    )
