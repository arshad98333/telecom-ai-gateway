"""Scopes, roles and what each role may hold.

Auth0 holds identity and role assignment. This file holds what a role may *do*, so
adding a permission is a reviewed code change with a test, not a click in a dashboard
nobody reads. Terraform in ``infra/auth0`` creates the same scopes and roles in the
tenant; a test asserts the two definitions have not drifted apart.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class Scope(StrEnum):
    """Every permission this API understands. Anything else is ignored, not inferred."""

    ACCOUNT_READ = "account:read"
    SERVICE_READ = "service:read"
    ORDER_READ = "order:read"
    BILLING_READ = "billing:read"
    NETWORK_READ = "network:read"
    TICKET_READ = "ticket:read"
    TICKET_WRITE = "ticket:write"
    CALLBACK_WRITE = "callback:write"
    REFUND_REQUEST = "refund:request"
    REFUND_APPROVE = "refund:approve"
    CASE_READ = "case:read"
    CASE_WRITE = "case:write"
    AUDIT_READ = "audit:read"
    CONFIG_READ = "config:read"
    CONFIG_WRITE = "config:write"
    ASSIGNMENT_READ = "assignment:read"
    ASSIGNMENT_WRITE = "assignment:write"


class Role(StrEnum):
    CUSTOMER = "customer"
    SUPPORT_AGENT = "support_agent"
    SUPERVISOR_APPROVER = "supervisor_approver"
    ADMIN_SECURITY = "admin_security"
    #: The machine-to-machine identity used by the MCP tool server. It authenticates the
    #: service, never a person, and holds no customer-data scopes of its own.
    SERVICE = "service"


_CUSTOMER_READS: Final[frozenset[Scope]] = frozenset(
    {
        Scope.ACCOUNT_READ,
        Scope.SERVICE_READ,
        Scope.ORDER_READ,
        Scope.BILLING_READ,
        Scope.NETWORK_READ,
        Scope.TICKET_READ,
    }
)

#: The ceiling for each role. An identity's effective permissions are the intersection
#: of the permissions in its token and the ceiling for its role, so a token minted with
#: too much - a mistake in the identity provider, or a stale assignment - still cannot
#: exceed what the role is allowed to hold.
ROLE_SCOPES: Final[dict[Role, frozenset[Scope]]] = {
    Role.CUSTOMER: _CUSTOMER_READS
    | {Scope.TICKET_WRITE, Scope.CALLBACK_WRITE, Scope.REFUND_REQUEST, Scope.CASE_READ},
    Role.SUPPORT_AGENT: _CUSTOMER_READS
    | {
        Scope.TICKET_WRITE,
        Scope.CALLBACK_WRITE,
        Scope.REFUND_REQUEST,
        Scope.CASE_READ,
        Scope.CASE_WRITE,
    },
    Role.SUPERVISOR_APPROVER: _CUSTOMER_READS
    | {
        Scope.TICKET_WRITE,
        Scope.CALLBACK_WRITE,
        Scope.REFUND_REQUEST,
        Scope.REFUND_APPROVE,
        Scope.CASE_READ,
        Scope.CASE_WRITE,
        Scope.ASSIGNMENT_READ,
        Scope.ASSIGNMENT_WRITE,
    },
    # Administering security does not mean reading bills. This role holds no
    # customer-data scopes at all, and the emptiness is asserted by a test.
    Role.ADMIN_SECURITY: frozenset(
        {Scope.AUDIT_READ, Scope.CONFIG_READ, Scope.CONFIG_WRITE, Scope.ASSIGNMENT_READ}
    ),
    Role.SERVICE: frozenset(),
}

#: Roles that may act on an account they do not own, subject to an assignment check.
DELEGATED_ROLES: Final[frozenset[Role]] = frozenset({Role.SUPPORT_AGENT, Role.SUPERVISOR_APPROVER})


def effective_scopes(role: Role, granted: frozenset[Scope]) -> frozenset[Scope]:
    """What this identity may actually use: the token's permissions, capped by its role."""
    return granted & ROLE_SCOPES.get(role, frozenset())


def parse_scopes(values: object) -> frozenset[Scope]:
    """Parse permissions from a token claim, ignoring anything we do not recognise.

    Accepts a list (Auth0's ``permissions`` claim with RBAC enabled) or a space-delimited
    string (the standard ``scope`` claim). Unknown values are dropped rather than
    rejected, because a token legitimately carries permissions for other APIs.
    """
    if isinstance(values, str):
        candidates: list[str] = values.split()
    elif isinstance(values, (list, tuple, set, frozenset)):
        candidates = [str(value) for value in values]
    else:
        return frozenset()
    known = {scope.value for scope in Scope}
    return frozenset(Scope(value) for value in candidates if value in known)
