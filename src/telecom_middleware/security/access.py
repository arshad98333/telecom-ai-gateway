"""Ownership and authority: *which* records this identity may touch.

Holding a permission says you may read *an* account; it does not say *which*. That
second question is answered here, in one module, and every endpoint routes through it.

Three rules, each of which exists because its absence is a breach:

* a customer may act only on the account their token says they are;
* any other role may act only on an account the service layer has assigned to them,
  which is a lookup, never a claim in the token - a claim goes stale between issue and
  use, and grows without bound for a busy agent;
* an approval is decided by someone other than the person who raised it, within their
  authority limit.

Every refusal raises the same error with the same wording, because the difference
between "no such record" and "not yours" is an oracle for discovering which customers,
tickets and approvals exist.
"""

from __future__ import annotations

from typing import Protocol

from telecom_middleware.domain.errors import (
    ApprovalNotPendingError,
    ForbiddenError,
    SelfApprovalDeniedError,
)
from telecom_middleware.domain.models import ApprovalRequest, ApprovalState
from telecom_middleware.security.permissions import Role, Scope
from telecom_middleware.security.principal import Principal

#: The most a role may approve on its own, in minor units. Anything above goes to a
#: higher authority; there is no role here that can approve without limit.
APPROVAL_LIMIT_MINOR: dict[Role, int] = {
    Role.SUPERVISOR_APPROVER: 50_000,  # 500.00 in a two-decimal currency
}


class AssignmentLookup(Protocol):
    """Answers whether an agent or supervisor is assigned to an account."""

    async def is_assigned(self, tenant_id: str, agent_sub: str, cx_id: str) -> bool: ...


def require_scope(principal: Principal, scope: Scope) -> None:
    """Refuse unless the identity holds the permission, after its role cap is applied."""
    if not principal.has(scope):
        raise ForbiddenError(f"{principal.subject} lacks {scope}")


def require_same_tenant(principal: Principal, tenant_id: str) -> None:
    """Refuse a request whose target belongs to another tenant.

    Belt as well as braces: the repositories already filter by tenant, and this catches
    the case where a route is handed a tenant from somewhere other than the token.
    """
    if principal.tenant_id != tenant_id:
        raise ForbiddenError("tenant mismatch")


def require_human(principal: Principal) -> None:
    """Refuse a customer-data call that carries only a service credential.

    The MCP server authenticates itself with client credentials, but it must present the
    customer's own token for anything touching customer data. A compromised service
    credential must not be able to read one customer record.
    """
    if principal.is_service:
        raise ForbiddenError("a service credential cannot act on customer data alone")


async def require_account_access(
    principal: Principal, cx_id: str, assignments: AssignmentLookup
) -> None:
    """Refuse unless this identity may act on this account."""
    require_human(principal)

    if principal.is_customer:
        if principal.cx_id != cx_id:
            raise ForbiddenError("customer may only act on their own account")
        return

    if not principal.may_act_for_others:
        # Security administration, for example: it has no customer-data scopes at all,
        # and no assignment can give it any.
        raise ForbiddenError(f"role {principal.role} may not act on a customer account")

    if not await assignments.is_assigned(principal.tenant_id, principal.subject, cx_id):
        raise ForbiddenError("no assignment for this account")


def require_approval_authority(principal: Principal, request: ApprovalRequest) -> None:
    """Refuse unless this identity may decide this particular approval request."""
    require_human(principal)
    require_scope(principal, Scope.REFUND_APPROVE)
    require_same_tenant(principal, request.tenant_id)

    if request.state is not ApprovalState.PENDING:
        # Not a permission problem: the caller may look, but the decision is made.
        raise ApprovalNotPendingError(f"request is {request.state}")

    if request.requested_by == principal.subject:
        raise SelfApprovalDeniedError("requester and approver are the same identity")

    limit = APPROVAL_LIMIT_MINOR.get(principal.role)
    if limit is None:
        raise ForbiddenError(f"role {principal.role} has no approval authority")

    amount = request.amount_minor or 0
    if amount > limit:
        raise ForbiddenError(f"amount {amount} exceeds the {principal.role} limit of {limit}")
