"""The authenticated identity, and the request that carries it.

An identity is only ever produced by a token verifier. Nothing in this package builds
one from a caller's claim, because account ownership must come from a trusted
authentication and authorization layer, not from whoever is asking.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from telecom_mcp.domain.permissions import Role, Scope, effective_scopes


@dataclass(frozen=True, slots=True)
class Identity:
    """Who is calling, established by verifying a signed token."""

    #: The token subject. For a customer this is their CX ID.
    subject: str
    tenant_id: str
    role: Role
    #: Scopes present in the token.
    granted_scopes: frozenset[Scope]
    expires_at: datetime
    #: The token identifier, recorded in the audit trail so a specific token can be traced.
    token_id: str | None = None

    @property
    def scopes(self) -> frozenset[Scope]:
        """Scopes the identity may actually use: the token's, narrowed by its role."""
        return effective_scopes(self.role, self.granted_scopes)

    def has(self, scope: Scope) -> bool:
        return scope in self.scopes

    def owns(self, cx_id: str) -> bool:
        """Whether this identity is the customer being asked about.

        A support agent or supervisor is not the customer; their access to another
        account is decided by the ownership check against the backend, not here.
        """
        return self.role is Role.CUSTOMER and self.subject == cx_id

    def audit_view(self) -> dict[str, Any]:
        """The identity fields that belong in an audit record. Never the raw token."""
        return {
            "subject": self.subject,
            "tenant_id": self.tenant_id,
            "role": str(self.role),
            "scopes": sorted(str(scope) for scope in self.scopes),
            "token_id": self.token_id,
        }


@dataclass(frozen=True, slots=True)
class ToolRequest:
    """One tool call, before any validation has happened."""

    tool_name: str
    arguments: dict[str, Any]
    #: Raw bearer token. Held only long enough to verify it; never logged, never stored.
    token: str
    correlation_id: str
    case_id: str | None = None
    #: The contract version the caller was built against.
    contract_version: str = "1"
