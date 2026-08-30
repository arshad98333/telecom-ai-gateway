"""The error taxonomy, rendered to callers as RFC 9457 problem details.

Every failure a caller can see is one of these. Driver errors (pymongo, jwt, httpx) are
translated at the adapter boundary so the domain never sees them and no client ever
sees a stack trace, a Mongo error code, or an internal message.

Denials are deliberately indistinguishable from one another. "Not found" and "not
yours" return the same status and the same wording, because a difference between them
is an oracle for discovering which customers, tickets and approvals exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    UNAUTHENTICATED = "unauthenticated"
    TOKEN_INVALID = "token_invalid"  # noqa: S105 - an error code, not a credential
    TOKEN_EXPIRED = "token_expired"  # noqa: S105 - an error code, not a credential
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    INVALID_INPUT = "invalid_input"
    CONFLICT = "conflict"
    IDEMPOTENCY_KEY_REUSED = "idempotency_key_reused"
    PASSCODE_INCORRECT = "passcode_incorrect"
    ACCOUNT_LOCKED = "account_locked"
    APPROVAL_NOT_PENDING = "approval_not_pending"
    SELF_APPROVAL_DENIED = "self_approval_denied"
    RATE_LIMITED = "rate_limited"
    STORE_UNAVAILABLE = "store_unavailable"
    CONFIGURATION_ERROR = "configuration_error"
    INTERNAL_ERROR = "internal_error"


#: The single wording used for every "you may not see this" outcome.
DENIAL_MESSAGE = "The requested resource is not available to this identity."


@dataclass(frozen=True, slots=True)
class ProblemDetails:
    """RFC 9457 body. Never carries an internal message or a record's contents."""

    status: int
    code: ErrorCode
    title: str
    correlation_id: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": f"https://telecom.example/problems/{self.code}",
            "title": self.title,
            "status": self.status,
            "code": str(self.code),
            "correlation_id": self.correlation_id,
            **({"detail": self.detail} if self.detail else {}),
        }


class MiddlewareError(Exception):
    """Base class for every error this service raises deliberately."""

    status: int = 500
    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    title: str = "An unexpected error occurred."

    def __init__(self, message: str | None = None, *, detail: dict[str, Any] | None = None) -> None:
        self.detail = detail or {}
        super().__init__(message or self.title)

    def problem(self, correlation_id: str) -> ProblemDetails:
        """Build the response body. Uses ``title``, never ``str(self)``."""
        return ProblemDetails(
            status=self.status,
            code=self.code,
            title=self.title,
            correlation_id=correlation_id,
            detail=self.detail,
        )


class AuthenticationError(MiddlewareError):
    status = 401
    code = ErrorCode.UNAUTHENTICATED
    title = "Authentication is required."


class TokenInvalidError(AuthenticationError):
    code = ErrorCode.TOKEN_INVALID
    title = "The access token is not valid."


class TokenExpiredError(AuthenticationError):
    code = ErrorCode.TOKEN_EXPIRED
    title = "The access token has expired."


class ForbiddenError(MiddlewareError):
    status = 403
    code = ErrorCode.FORBIDDEN
    title = DENIAL_MESSAGE


class NotFoundError(MiddlewareError):
    # 403, not 404, and the same wording: whether a record exists is itself information.
    status = 403
    code = ErrorCode.FORBIDDEN
    title = DENIAL_MESSAGE


class InvalidInputError(MiddlewareError):
    status = 422
    code = ErrorCode.INVALID_INPUT
    title = "The request did not match the expected schema."


class ConflictError(MiddlewareError):
    status = 409
    code = ErrorCode.CONFLICT
    title = "The request conflicts with the current state of the resource."


class IdempotencyKeyReusedError(ConflictError):
    code = ErrorCode.IDEMPOTENCY_KEY_REUSED
    title = "This idempotency key was already used with different input."


class PasscodeIncorrectError(MiddlewareError):
    status = 401
    code = ErrorCode.PASSCODE_INCORRECT
    # No hint about how many attempts remain: that is a gift to whoever is guessing.
    title = "Authentication failed."


class AccountLockedError(MiddlewareError):
    status = 423
    code = ErrorCode.ACCOUNT_LOCKED
    title = "Authentication is temporarily locked. Contact support."


class ApprovalNotPendingError(ConflictError):
    code = ErrorCode.APPROVAL_NOT_PENDING
    title = "This approval request has already been decided."


class SelfApprovalDeniedError(ForbiddenError):
    code = ErrorCode.SELF_APPROVAL_DENIED
    title = "An approval request cannot be decided by the identity that raised it."


class RateLimitedError(MiddlewareError):
    status = 429
    code = ErrorCode.RATE_LIMITED
    title = "Too many requests; please retry shortly."


class StoreUnavailableError(MiddlewareError):
    status = 503
    code = ErrorCode.STORE_UNAVAILABLE
    title = "The service is temporarily unavailable; no action was completed."


class ConfigurationError(MiddlewareError):
    code = ErrorCode.CONFIGURATION_ERROR
    title = "The service is misconfigured."
