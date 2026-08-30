"""Who may touch what. The tests that matter most in this service."""

from datetime import UTC, datetime, timedelta

import pytest

from telecom_middleware.domain.errors import (
    ApprovalNotPendingError,
    ForbiddenError,
    SelfApprovalDeniedError,
)
from telecom_middleware.domain.models import ApprovalRequest, ApprovalState
from telecom_middleware.domain.money import Currency
from telecom_middleware.security.access import (
    require_account_access,
    require_approval_authority,
    require_human,
    require_same_tenant,
    require_scope,
)
from telecom_middleware.security.permissions import ROLE_SCOPES, Role, Scope
from telecom_middleware.security.principal import Principal

TENANT = "tenant-eu-1"
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


class Assignments:
    """A stand-in for the assignment collection."""

    def __init__(self, pairs: set[tuple[str, str]] | None = None) -> None:
        self._pairs = pairs or set()

    async def is_assigned(self, tenant_id: str, agent_sub: str, cx_id: str) -> bool:
        del tenant_id
        return (agent_sub, cx_id) in self._pairs


def principal(
    role: Role = Role.CUSTOMER,
    *,
    subject: str = "auth0|customer-1",
    cx_id: str | None = "CX-1234",
    scopes: frozenset[Scope] | None = None,
    tenant: str = TENANT,
    is_service: bool = False,
) -> Principal:
    return Principal(
        subject=subject,
        tenant_id=tenant,
        role=role,
        granted_scopes=scopes if scopes is not None else ROLE_SCOPES[role],
        expires_at=NOW + timedelta(minutes=10),
        cx_id=cx_id if role is Role.CUSTOMER else None,
        is_service=is_service,
    )


def approval(**overrides: object) -> ApprovalRequest:
    base: dict[str, object] = {
        "tenant_id": TENANT,
        "request_id": "APR-1",
        "cx_id": "CX-1234",
        "action": "refund",
        "amount_minor": 450,
        "currency": Currency.GBP,
        "reason": "duplicate_charge",
        "justification": "Charged twice in August.",
        "evidence": {"invoice_id": "INV-2026-08"},
        "state": ApprovalState.PENDING,
        "requested_by": "auth0|customer-1",
        "requested_by_role": "customer",
        "created_at": NOW,
        "expires_at": NOW + timedelta(days=2),
    }
    base.update(overrides)
    return ApprovalRequest.model_validate(base)


# --- scopes -------------------------------------------------------------------------


def test_a_held_scope_passes_and_a_missing_one_is_refused() -> None:
    require_scope(principal(), Scope.ACCOUNT_READ)

    with pytest.raises(ForbiddenError):
        require_scope(principal(), Scope.REFUND_APPROVE)


def test_a_scope_the_role_may_not_hold_does_not_grant_access() -> None:
    # A token minted with refund:approve for a customer - a mistake in the identity
    # provider - must still not be able to approve anything.
    over_broad = principal(scopes=frozenset({Scope.ACCOUNT_READ, Scope.REFUND_APPROVE}))

    with pytest.raises(ForbiddenError):
        require_scope(over_broad, Scope.REFUND_APPROVE)


def test_security_administration_holds_no_customer_data_scopes() -> None:
    assert (
        ROLE_SCOPES[Role.ADMIN_SECURITY]
        & {
            Scope.ACCOUNT_READ,
            Scope.BILLING_READ,
            Scope.SERVICE_READ,
            Scope.ORDER_READ,
        }
        == frozenset()
    )


def test_no_role_may_both_request_and_approve_without_a_second_person() -> None:
    # A supervisor holds both scopes, which is legitimate; the separation is enforced
    # per request rather than by withholding the scope.
    assert Scope.REFUND_REQUEST in ROLE_SCOPES[Role.SUPERVISOR_APPROVER]
    assert Scope.REFUND_APPROVE in ROLE_SCOPES[Role.SUPERVISOR_APPROVER]
    assert Scope.REFUND_APPROVE not in ROLE_SCOPES[Role.CUSTOMER]
    assert Scope.REFUND_APPROVE not in ROLE_SCOPES[Role.SUPPORT_AGENT]


# --- tenancy and service credentials ------------------------------------------------


def test_a_request_for_another_tenant_is_refused() -> None:
    with pytest.raises(ForbiddenError, match="tenant mismatch"):
        require_same_tenant(principal(), "tenant-us-9")


def test_a_service_credential_alone_cannot_touch_customer_data() -> None:
    service = principal(role=Role.SERVICE, subject="mcp@clients", cx_id=None, is_service=True)

    with pytest.raises(ForbiddenError, match="service credential"):
        require_human(service)


# --- account access -----------------------------------------------------------------


async def test_a_customer_may_act_on_their_own_account() -> None:
    await require_account_access(principal(), "CX-1234", Assignments())


async def test_a_customer_may_not_act_on_another_account() -> None:
    with pytest.raises(ForbiddenError):
        await require_account_access(principal(), "CX-9999", Assignments())


async def test_an_agent_may_act_only_on_an_assigned_account() -> None:
    agent = principal(role=Role.SUPPORT_AGENT, subject="auth0|agent-7", cx_id=None)
    assignments = Assignments({("auth0|agent-7", "CX-5555")})

    await require_account_access(agent, "CX-5555", assignments)

    with pytest.raises(ForbiddenError, match="no assignment"):
        await require_account_access(agent, "CX-1234", assignments)


async def test_an_assignment_belonging_to_another_agent_does_not_help() -> None:
    agent = principal(role=Role.SUPPORT_AGENT, subject="auth0|agent-8", cx_id=None)
    assignments = Assignments({("auth0|agent-7", "CX-5555")})

    with pytest.raises(ForbiddenError):
        await require_account_access(agent, "CX-5555", assignments)


async def test_security_administration_cannot_reach_a_customer_account_at_all() -> None:
    admin = principal(role=Role.ADMIN_SECURITY, subject="auth0|sec-1", cx_id=None)
    everything_assigned = Assignments({("auth0|sec-1", "CX-1234")})

    with pytest.raises(ForbiddenError, match="may not act"):
        await require_account_access(admin, "CX-1234", everything_assigned)


async def test_the_mcp_service_account_cannot_reach_an_account_on_its_own() -> None:
    service = principal(role=Role.SERVICE, subject="mcp@clients", cx_id=None, is_service=True)

    with pytest.raises(ForbiddenError):
        await require_account_access(service, "CX-1234", Assignments())


# --- approval authority -------------------------------------------------------------


def supervisor(subject: str = "auth0|supervisor-1") -> Principal:
    return principal(role=Role.SUPERVISOR_APPROVER, subject=subject, cx_id=None)


def test_a_supervisor_may_decide_a_pending_request_from_someone_else() -> None:
    require_approval_authority(supervisor(), approval())


def test_nobody_may_approve_their_own_request() -> None:
    # Separation of duties, enforced per request rather than by role.
    own = approval(requested_by="auth0|supervisor-1", requested_by_role="supervisor_approver")

    with pytest.raises(SelfApprovalDeniedError):
        require_approval_authority(supervisor(), own)


def test_an_agent_may_not_approve_anything() -> None:
    agent = principal(role=Role.SUPPORT_AGENT, subject="auth0|agent-7", cx_id=None)

    with pytest.raises(ForbiddenError):
        require_approval_authority(agent, approval())


def test_a_customer_may_not_approve_their_own_refund() -> None:
    with pytest.raises(ForbiddenError):
        require_approval_authority(principal(), approval())


@pytest.mark.parametrize(
    "state", [ApprovalState.APPROVED, ApprovalState.REJECTED, ApprovalState.EXPIRED]
)
def test_an_already_decided_request_cannot_be_decided_again(state: ApprovalState) -> None:
    decided = approval(state=state)

    with pytest.raises(ApprovalNotPendingError):
        require_approval_authority(supervisor(), decided)


def test_an_amount_above_the_role_limit_is_refused() -> None:
    with pytest.raises(ForbiddenError, match="exceeds"):
        require_approval_authority(supervisor(), approval(amount_minor=50_001))


def test_an_amount_exactly_at_the_limit_is_allowed() -> None:
    require_approval_authority(supervisor(), approval(amount_minor=50_000))


def test_a_request_from_another_tenant_cannot_be_decided() -> None:
    with pytest.raises(ForbiddenError, match="tenant mismatch"):
        require_approval_authority(supervisor(), approval(tenant_id="tenant-us-9"))


def test_a_supervisor_from_another_tenant_is_refused_before_the_amount_is_considered() -> None:
    # Ordering matters: a tenant mismatch must not be reported as an amount problem,
    # because the amount is information about another tenant's request.
    from telecom_middleware.domain.errors import ForbiddenError

    other = principal(
        role=Role.SUPERVISOR_APPROVER, subject="auth0|sup-2", cx_id=None, tenant="tenant-us-9"
    )

    with pytest.raises(ForbiddenError, match="tenant mismatch"):
        require_approval_authority(other, approval(amount_minor=999_999))
