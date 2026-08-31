"""Permissions, roles and risk classes. Deny by default, everywhere.

Scopes are the unit of authorization. A role is a named bundle of scopes, maintained
by IAM; this package never invents a scope for an identity, it only checks the ones
the identity was granted.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class Scope(StrEnum):
    """The scopes this package understands. Anything else is ignored, not inferred."""

    ACCOUNT_READ = "account:read"
    SERVICE_READ = "service:read"
    ORDER_READ = "order:read"
    BILLING_READ = "billing:read"
    NETWORK_READ = "network:read"
    TICKET_WRITE = "ticket:write"
    CALLBACK_WRITE = "callback:write"
    REFUND_REQUEST = "refund:request"
    SERVICE_CHANGE = "service:change"
    SERVICE_CANCEL = "service:cancel"


class Role(StrEnum):
    """Roles as maintained by IAM/IT Security."""

    CUSTOMER = "customer"
    SUPPORT_AGENT = "support_agent"
    SUPERVISOR_APPROVER = "supervisor_approver"
    ADMIN_SECURITY = "admin_security"


class RiskClass(StrEnum):
    """How dangerous an action is, which decides what controls apply to it."""

    #: Reads nothing but the caller's own data, changes nothing.
    READ_ONLY = "read_only"
    #: Creates a record that can be cancelled before it is processed.
    LOW_RISK_WRITE = "low_risk_write"
    #: Requires a named human approver before anything happens.
    RESTRICTED = "restricted"


#: What each role may hold. An identity's effective scopes are the intersection of
#: the scopes in its token and the scopes its role is allowed to hold, so a token
#: minted with an over-broad scope still cannot exceed the role.
ROLE_SCOPES: Final[dict[Role, frozenset[Scope]]] = {
    Role.CUSTOMER: frozenset(
        {
            Scope.ACCOUNT_READ,
            Scope.SERVICE_READ,
            Scope.ORDER_READ,
            Scope.BILLING_READ,
            Scope.NETWORK_READ,
            Scope.TICKET_WRITE,
            Scope.CALLBACK_WRITE,
            Scope.REFUND_REQUEST,
        }
    ),
    Role.SUPPORT_AGENT: frozenset(
        {
            Scope.ACCOUNT_READ,
            Scope.SERVICE_READ,
            Scope.ORDER_READ,
            Scope.BILLING_READ,
            Scope.NETWORK_READ,
            Scope.TICKET_WRITE,
            Scope.CALLBACK_WRITE,
            Scope.REFUND_REQUEST,
        }
    ),
    Role.SUPERVISOR_APPROVER: frozenset(
        {
            Scope.ACCOUNT_READ,
            Scope.SERVICE_READ,
            Scope.ORDER_READ,
            Scope.BILLING_READ,
            Scope.NETWORK_READ,
            Scope.TICKET_WRITE,
            Scope.CALLBACK_WRITE,
            Scope.REFUND_REQUEST,
            Scope.SERVICE_CHANGE,
            Scope.SERVICE_CANCEL,
        }
    ),
    # Security administration configures roles; it does not read customer data.
    Role.ADMIN_SECURITY: frozenset(),
}


def effective_scopes(role: Role, granted: frozenset[Scope]) -> frozenset[Scope]:
    """Scopes an identity may actually use.

    The intersection is deliberate. If a token is ever minted with a scope its role
    should not hold, the role still wins, so a mistake in the identity provider
    cannot widen access here.
    """
    return granted & ROLE_SCOPES.get(role, frozenset())


def parse_scopes(values: object) -> frozenset[Scope]:
    """Parse scopes from a token claim, ignoring anything we do not recognise.

    Accepts a list or a space-delimited string, which are the two shapes identity
    providers actually emit. Unknown values are dropped rather than rejected, because
    a token legitimately carries scopes for other services.
    """
    if isinstance(values, str):
        candidates: list[str] = values.split()
    elif isinstance(values, (list, tuple, set, frozenset)):
        candidates = [str(value) for value in values]
    else:
        return frozenset()
    known = {scope.value for scope in Scope}
    return frozenset(Scope(value) for value in candidates if value in known)
