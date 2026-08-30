"""Who is calling. Produced only by verifying a signed token, never from a claim we are told."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from telecom_middleware.security.permissions import (
    DELEGATED_ROLES,
    Role,
    Scope,
    effective_scopes,
)


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated identity, with its permissions already capped by its role."""

    subject: str
    tenant_id: str
    role: Role
    granted_scopes: frozenset[Scope]
    expires_at: datetime
    #: Present only for a customer: the account this identity *is*.
    cx_id: str | None = None
    token_id: str | None = None
    #: True when the token was issued by client credentials rather than to a person.
    is_service: bool = False

    @property
    def scopes(self) -> frozenset[Scope]:
        return effective_scopes(self.role, self.granted_scopes)

    def has(self, scope: Scope) -> bool:
        return scope in self.scopes

    @property
    def is_customer(self) -> bool:
        return self.role is Role.CUSTOMER

    @property
    def may_act_for_others(self) -> bool:
        """Whether this role can ever touch an account it does not own.

        Saying yes here does not grant access to any particular account; it only means
        the assignment check is the thing that decides, rather than an outright refusal.
        """
        return self.role in DELEGATED_ROLES

    def audit_view(self) -> dict[str, Any]:
        """The identity fields that belong in an audit record. Never the raw token."""
        return {
            "actor_sub": self.subject,
            "actor_role": str(self.role),
            "tenant_id": self.tenant_id,
            "token_id": self.token_id,
            "scopes": sorted(str(scope) for scope in self.scopes),
        }
