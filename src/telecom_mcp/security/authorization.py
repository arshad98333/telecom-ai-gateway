"""The security kernel: nothing executes without passing every stage of this pipeline.

The stages are the ones the build order names - token, tenant, CX ID, account
ownership, role, permission, input schema, tool scope - with one deliberate change of
order recorded in ``docs/decisions/0002-authorization-stage-order.md``: the tool is
resolved first, so an unknown or blocked name is refused before any work is done for
it, including verifying a signature.

Two properties make this worth having as one object rather than seven handler-level
checks. A tool cannot be executed without traversing it, because the registry only
hands back callables that are already wrapped. And every outcome, allowed or refused,
produces exactly one audit record, so "we have no record of that call" is not a state
this system can reach.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from pydantic import ValidationError

from telecom_mcp.domain.errors import (
    AuthenticationError,
    AuthorizationError,
    CrossAccountAccessError,
    InvalidInputError,
    TelecomMCPError,
    TenantMismatchError,
    TokenExpiredError,
    TokenInvalidError,
    ToolBlockedError,
    UnknownToolError,
    UnsupportedContractVersionError,
)
from telecom_mcp.domain.permissions import Role
from telecom_mcp.domain.schemas import ToolInput
from telecom_mcp.domain.tools import ToolSpec, get_spec, is_blocked
from telecom_mcp.security.identity import Identity, ToolRequest
from telecom_mcp.security.verifier import TokenVerificationError, TokenVerifier

SUPPORTED_CONTRACT_VERSIONS = frozenset({"1"})


class Stage(StrEnum):
    """Named so an audit record says exactly which control refused the call."""

    TOOL_SCOPE = "tool_scope"
    TOKEN = "token"  # noqa: S105 - a pipeline stage name, not a credential
    TENANT = "tenant"
    CX_ID = "cx_id"
    ACCOUNT_OWNERSHIP = "account_ownership"
    ROLE = "role"
    PERMISSION = "permission"
    INPUT_SCHEMA = "input_schema"


class OwnershipChecker(Protocol):
    """Confirms, server side, that an identity may act on an account.

    Never consulted for the answer the caller already claimed. The customer path is
    settled locally by comparing the token subject; this exists for the agent and
    supervisor paths, where the assignment lives in the service layer.
    """

    async def may_access(self, *, tenant_id: str, subject: str, cx_id: str) -> bool: ...


class DenyAllOwnership:
    """The safe default: no identity may act on an account that is not its own.

    Configured deployments replace this with a checker backed by the middleware API.
    Failing closed is the point; an absent checker must not mean "allow".
    """

    async def may_access(self, *, tenant_id: str, subject: str, cx_id: str) -> bool:
        # The arguments are unused on purpose: this implementation refuses everything.
        del tenant_id, subject, cx_id
        return False


@dataclass(frozen=True, slots=True)
class AuthorizedCall:
    """The result of a successful pass through every stage."""

    identity: Identity
    spec: ToolSpec
    arguments: ToolInput
    cx_id: str
    correlation_id: str
    case_id: str | None


@dataclass(frozen=True, slots=True)
class Denial:
    """Why a call was refused, in a form the audit record can use verbatim."""

    stage: Stage
    reason: str
    error: TelecomMCPError


class AuthorizationDeniedError(Exception):
    """Raised to unwind the pipeline. Carries the denial for the caller to record."""

    def __init__(self, denial: Denial) -> None:
        self.denial = denial
        super().__init__(denial.reason)


class Authorizer:
    """Runs every stage, in order, failing closed."""

    def __init__(
        self,
        *,
        verifier: TokenVerifier,
        ownership: OwnershipChecker | None = None,
        expected_tenants: frozenset[str] | None = None,
    ) -> None:
        self._verifier = verifier
        self._ownership = ownership or DenyAllOwnership()
        #: When set, tokens from any other tenant are refused outright, which is what
        #: makes a single-tenant deployment safe to run without trusting the issuer.
        self._expected_tenants = expected_tenants

    @property
    def verifier(self) -> TokenVerifier:
        """Exposed so a transport can identify a caller without executing anything."""
        return self._verifier

    async def authorize(self, request: ToolRequest) -> AuthorizedCall:
        """Return an authorized call, or raise ``AuthorizationDeniedError``."""
        spec = self._stage_tool_scope(request)
        identity = await self._stage_token(request)
        self._stage_tenant(identity)
        cx_id = self._stage_cx_id(request)
        await self._stage_account_ownership(identity, cx_id)
        self._stage_role(identity)
        self._stage_permission(identity, spec)
        arguments = self._stage_input_schema(request, spec)
        return AuthorizedCall(
            identity=identity,
            spec=spec,
            arguments=arguments,
            cx_id=cx_id,
            correlation_id=request.correlation_id,
            case_id=request.case_id,
        )

    # --- stages ---------------------------------------------------------------------

    def _stage_tool_scope(self, request: ToolRequest) -> ToolSpec:
        if request.contract_version not in SUPPORTED_CONTRACT_VERSIONS:
            raise _deny(
                Stage.TOOL_SCOPE,
                f"unsupported contract version {request.contract_version!r}",
                UnsupportedContractVersionError(operation=request.tool_name),
            )
        if is_blocked(request.tool_name):
            raise _deny(
                Stage.TOOL_SCOPE,
                f"tool {request.tool_name!r} is blocked in this version",
                ToolBlockedError(operation=request.tool_name),
            )
        spec = get_spec(request.tool_name)
        if spec is None:
            raise _deny(
                Stage.TOOL_SCOPE,
                f"unknown tool {request.tool_name!r}",
                UnknownToolError(operation=request.tool_name),
            )
        return spec

    async def _stage_token(self, request: ToolRequest) -> Identity:
        if not request.token:
            raise _deny(
                Stage.TOKEN,
                "no bearer token presented",
                AuthenticationError(operation=request.tool_name),
            )
        try:
            return await self._verifier.verify(request.token)
        except TokenVerificationError as exc:
            expired = "expired" in str(exc)
            error: TelecomMCPError = (
                TokenExpiredError(operation=request.tool_name)
                if expired
                else TokenInvalidError(operation=request.tool_name)
            )
            raise _deny(Stage.TOKEN, str(exc), error) from exc

    def _stage_tenant(self, identity: Identity) -> None:
        if not identity.tenant_id:
            raise _deny(Stage.TENANT, "identity carries no tenant", TenantMismatchError())
        if self._expected_tenants is not None and identity.tenant_id not in self._expected_tenants:
            raise _deny(
                Stage.TENANT,
                f"tenant {identity.tenant_id!r} is not served by this deployment",
                TenantMismatchError(),
            )

    def _stage_cx_id(self, request: ToolRequest) -> str:
        raw = request.arguments.get("cx_id")
        if not isinstance(raw, str) or not raw.strip():
            raise _deny(
                Stage.CX_ID,
                "request carries no customer reference",
                InvalidInputError(operation=request.tool_name, details={"field": "cx_id"}),
            )
        return raw.strip()

    async def _stage_account_ownership(self, identity: Identity, cx_id: str) -> None:
        if identity.owns(cx_id):
            return
        allowed = await self._ownership.may_access(
            tenant_id=identity.tenant_id, subject=identity.subject, cx_id=cx_id
        )
        if not allowed:
            # The reason is detailed for the audit trail; the caller sees the generic
            # denial message, which is identical to a plain permission failure.
            raise _deny(
                Stage.ACCOUNT_OWNERSHIP,
                "identity does not own or have assignment to the requested account",
                CrossAccountAccessError(),
            )

    def _stage_role(self, identity: Identity) -> None:
        if identity.role is Role.ADMIN_SECURITY:
            raise _deny(
                Stage.ROLE,
                "security administration holds no customer-data scopes",
                AuthorizationError(),
            )
        if not identity.scopes:
            raise _deny(Stage.ROLE, "role grants no usable scopes", AuthorizationError())

    def _stage_permission(self, identity: Identity, spec: ToolSpec) -> None:
        if not identity.has(spec.required_scope):
            raise _deny(
                Stage.PERMISSION,
                f"identity lacks {spec.required_scope}",
                AuthorizationError(operation=spec.name),
            )

    def _stage_input_schema(self, request: ToolRequest, spec: ToolSpec) -> ToolInput:
        try:
            return spec.input_model.model_validate(request.arguments)
        except ValidationError as exc:
            errors = exc.errors()
            location = errors[0]["loc"] if errors else ()
            message = errors[0]["msg"] if errors else "invalid"
            field = ".".join(str(part) for part in location) or "<request>"
            raise _deny(
                Stage.INPUT_SCHEMA,
                f"input failed validation at {field}: {message}",
                InvalidInputError(operation=spec.name, details={"field": field}),
            ) from exc


def _deny(stage: Stage, reason: str, error: TelecomMCPError) -> AuthorizationDeniedError:
    return AuthorizationDeniedError(Denial(stage=stage, reason=reason, error=error))


def describe_denial(denial: Denial) -> dict[str, Any]:
    """The audit view of a refusal."""
    return {
        "stage": str(denial.stage),
        "reason": denial.reason,
        "code": str(denial.error.code),
    }
