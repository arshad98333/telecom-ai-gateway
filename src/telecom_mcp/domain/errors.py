"""The error taxonomy for this package.

Every failure the caller can see is one of these. Library errors (httpx, jwt, redis)
are translated into these at the adapter boundary, so the domain never sees a driver
error and the MCP client never sees a stack trace or an internal message.

Each error carries three things, because a failure message must answer three
questions: what was being attempted (``operation``), what went wrong (``code`` and
``message``), and what identifier lets someone find the rest of the story
(``correlation_id``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """Stable, machine-readable failure codes. Part of the v1 contract."""

    # Identity and access
    UNAUTHENTICATED = "unauthenticated"
    TOKEN_INVALID = "token_invalid"  # noqa: S105 - an error code, not a credential
    TOKEN_EXPIRED = "token_expired"  # noqa: S105 - an error code, not a credential
    FORBIDDEN = "forbidden"
    CROSS_ACCOUNT_DENIED = "cross_account_denied"
    TENANT_MISMATCH = "tenant_mismatch"

    # Request shape
    UNKNOWN_TOOL = "unknown_tool"
    TOOL_BLOCKED = "tool_blocked"
    INVALID_INPUT = "invalid_input"
    IDEMPOTENCY_KEY_REQUIRED = "idempotency_key_required"
    IDEMPOTENCY_KEY_REUSED = "idempotency_key_reused"
    UNSUPPORTED_CONTRACT_VERSION = "unsupported_contract_version"

    # Downstream
    BACKEND_UNAVAILABLE = "backend_unavailable"
    BACKEND_TIMEOUT = "backend_timeout"
    BACKEND_BAD_RESPONSE = "backend_bad_response"
    CIRCUIT_OPEN = "circuit_open"
    NOT_FOUND = "not_found"

    # Capacity and configuration
    RATE_LIMITED = "rate_limited"
    OVERLOADED = "overloaded"
    CONFIGURATION_ERROR = "configuration_error"
    INTERNAL_ERROR = "internal_error"


#: Message shown to a caller when we could not verify that a write completed.
#: The wording matters: it tells the agent nothing happened, so it is safe to ask a human.
SAFE_FAILURE_MESSAGE = "The requested service is temporarily unavailable; no action was completed."


@dataclass(frozen=True, slots=True)
class ErrorEnvelope:
    """What the caller receives. Never contains internal detail or secrets."""

    code: ErrorCode
    message: str
    operation: str
    correlation_id: str
    retryable: bool
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "code": str(self.code),
                "message": self.message,
                "operation": self.operation,
                "correlation_id": self.correlation_id,
                "retryable": self.retryable,
                "details": self.details,
            }
        }


class TelecomMCPError(Exception):
    """Base class for every error this package raises deliberately."""

    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    #: Whether repeating the identical request could plausibly succeed.
    retryable: bool = False
    #: What the caller is told. Subclasses that touch customer data keep this generic.
    public_message: str = "The request could not be completed."

    def __init__(
        self,
        message: str | None = None,
        *,
        operation: str = "unknown",
        details: dict[str, Any] | None = None,
    ) -> None:
        self.operation = operation
        self.details = details or {}
        super().__init__(message or self.public_message)

    def envelope(self, correlation_id: str) -> ErrorEnvelope:
        """Build the caller-facing envelope. Uses ``public_message``, never ``str(self)``."""
        return ErrorEnvelope(
            code=self.code,
            message=self.public_message,
            operation=self.operation,
            correlation_id=correlation_id,
            retryable=self.retryable,
            details=self.details,
        )


# --- Identity and access ------------------------------------------------------------


class AuthenticationError(TelecomMCPError):
    code = ErrorCode.UNAUTHENTICATED
    public_message = "Authentication is required for this request."


class TokenInvalidError(AuthenticationError):
    code = ErrorCode.TOKEN_INVALID
    public_message = "The access token is not valid."


class TokenExpiredError(AuthenticationError):
    code = ErrorCode.TOKEN_EXPIRED
    public_message = "The access token has expired."
    retryable = True  # the caller can refresh and try again


class AuthorizationError(TelecomMCPError):
    code = ErrorCode.FORBIDDEN
    public_message = "This action is not permitted for the authenticated identity."


class CrossAccountAccessError(AuthorizationError):
    code = ErrorCode.CROSS_ACCOUNT_DENIED
    # Deliberately identical wording to AuthorizationError so a caller cannot use the
    # difference to probe whether another account exists.
    public_message = "This action is not permitted for the authenticated identity."


class TenantMismatchError(AuthorizationError):
    code = ErrorCode.TENANT_MISMATCH
    public_message = "This action is not permitted for the authenticated identity."


# --- Request shape ------------------------------------------------------------------


class UnknownToolError(TelecomMCPError):
    code = ErrorCode.UNKNOWN_TOOL
    public_message = "The requested tool does not exist."


class ToolBlockedError(TelecomMCPError):
    code = ErrorCode.TOOL_BLOCKED
    public_message = "This tool is not available in this version."


class InvalidInputError(TelecomMCPError):
    code = ErrorCode.INVALID_INPUT
    public_message = "The request input did not match the tool schema."


class IdempotencyKeyRequiredError(InvalidInputError):
    code = ErrorCode.IDEMPOTENCY_KEY_REQUIRED
    public_message = "A write operation requires an idempotency key."


class IdempotencyKeyReusedError(InvalidInputError):
    code = ErrorCode.IDEMPOTENCY_KEY_REUSED
    public_message = "This idempotency key was already used with different input."


class UnsupportedContractVersionError(TelecomMCPError):
    code = ErrorCode.UNSUPPORTED_CONTRACT_VERSION
    public_message = "The requested tool contract version is not supported."


# --- Downstream ---------------------------------------------------------------------


class BackendError(TelecomMCPError):
    code = ErrorCode.BACKEND_UNAVAILABLE
    retryable = True
    public_message = SAFE_FAILURE_MESSAGE


class BackendTimeoutError(BackendError):
    code = ErrorCode.BACKEND_TIMEOUT


class BackendBadResponseError(BackendError):
    code = ErrorCode.BACKEND_BAD_RESPONSE
    # A malformed response will not become well-formed by asking again.
    retryable = False


class CircuitOpenError(BackendError):
    code = ErrorCode.CIRCUIT_OPEN


class NotFoundError(TelecomMCPError):
    code = ErrorCode.NOT_FOUND
    public_message = "The requested record was not found."


# --- Capacity and configuration -----------------------------------------------------


class RateLimitedError(TelecomMCPError):
    code = ErrorCode.RATE_LIMITED
    retryable = True
    public_message = "Too many requests; please retry shortly."


class OverloadedError(TelecomMCPError):
    code = ErrorCode.OVERLOADED
    retryable = True
    public_message = "The service is at capacity; please retry shortly."


class ConfigurationError(TelecomMCPError):
    code = ErrorCode.CONFIGURATION_ERROR
    public_message = "The service is misconfigured."


class InternalError(TelecomMCPError):
    code = ErrorCode.INTERNAL_ERROR
    public_message = "An unexpected error occurred."
