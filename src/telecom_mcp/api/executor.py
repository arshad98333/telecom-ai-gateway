"""The one path a tool call takes. Nothing executes any other way.

Order matters and is fixed: authorize, then deduplicate, then admit, then execute with
a time budget, then record. Every branch out of this function writes exactly one audit
record, so "we have no record of that call" is not a reachable state.

The executor returns either a validated tool output or an error envelope. It never
raises at its boundary, because the transport above it must always be able to answer
the agent with something safe to say to a customer.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from telecom_mcp.adapters.backend import TelecomBackend
from telecom_mcp.adapters.idempotency import (
    IdempotencyStore,
    ReservationState,
    fingerprint,
    scoped_key,
)
from telecom_mcp.adapters.reliability import CircuitBreaker, RetryPolicy, call_with_reliability
from telecom_mcp.domain.errors import (
    ErrorEnvelope,
    InternalError,
    OverloadedError,
    RateLimitedError,
    TelecomMCPError,
)
from telecom_mcp.domain.ports import Clock, Jitter
from telecom_mcp.domain.schemas import ToolOutput
from telecom_mcp.domain.tools import ToolSpec
from telecom_mcp.observability.logging import get_logger, request_context
from telecom_mcp.observability.metrics import Metrics
from telecom_mcp.observability.redaction import Redactor
from telecom_mcp.security.audit import AuditLog, Decision, Outcome
from telecom_mcp.security.authorization import (
    AuthorizationDeniedError,
    AuthorizedCall,
    Authorizer,
)
from telecom_mcp.security.identity import ToolRequest

logger = get_logger(__name__)

#: Told to a caller whose identical request is still running. Retryable by design.
IN_PROGRESS_MESSAGE = "An identical request is still being processed; retry shortly."


@dataclass(frozen=True, slots=True)
class ToolResult:
    """What the transport hands back. Exactly one of the two fields is set."""

    output: dict[str, Any] | None = None
    error: ErrorEnvelope | None = None
    deduplicated: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None


class ToolExecutor:
    """Wires the kernel, the store, the reliability layer and the audit trail together."""

    def __init__(
        self,
        *,
        authorizer: Authorizer,
        backend: TelecomBackend,
        idempotency: IdempotencyStore,
        audit: AuditLog,
        metrics: Metrics,
        redactor: Redactor,
        clock: Clock,
        jitter: Jitter,
        retry_policy: RetryPolicy,
        breaker: CircuitBreaker,
        max_concurrent_calls: int = 100,
        tool_timeout_s: float = 10.0,
    ) -> None:
        self._authorizer = authorizer
        self._backend = backend
        self._idempotency = idempotency
        self._audit = audit
        self._metrics = metrics
        self._redactor = redactor
        self._clock = clock
        self._jitter = jitter
        self._retry_policy = retry_policy
        self._breaker = breaker
        self._semaphore = asyncio.Semaphore(max_concurrent_calls)
        self._tool_timeout_s = tool_timeout_s

    @property
    def authorizer(self) -> Authorizer:
        return self._authorizer

    async def execute(self, request: ToolRequest) -> ToolResult:
        """Run one tool call. Never raises."""
        started = self._clock.monotonic()
        with request_context(request.correlation_id, request.case_id):
            try:
                call = await self._authorizer.authorize(request)
            except AuthorizationDeniedError as denied:
                return self._record_denial(request, denied, started)

            try:
                return await self._execute_authorized(call, started)
            except TelecomMCPError as error:
                return self._record_failure(call, error, started)
            except Exception:
                # The message is deliberately dropped: an unexpected exception is the
                # most likely thing to carry something that must not be shown.
                logger.exception("tool_call_crashed", tool=call.spec.name)
                return self._record_failure(call, InternalError(operation=call.spec.name), started)

    # --- the happy path -------------------------------------------------------------

    async def _execute_authorized(self, call: AuthorizedCall, started: float) -> ToolResult:
        spec = call.spec
        key: str | None = None

        if spec.requires_idempotency_key:
            key, replayed = await self._reserve(call)
            if replayed is not None:
                return replayed

        try:
            output = await self._admit_and_run(call)
        except TelecomMCPError:
            if key is not None:
                # Free the key so a genuine retry can proceed rather than being told
                # forever that a call is in progress.
                await self._idempotency.release(key)
            raise

        payload = self._project(output)
        if key is not None:
            await self._idempotency.complete(key, payload)

        self._record_success(call, started, deduplicated=False)
        return ToolResult(output=payload)

    async def _reserve(self, call: AuthorizedCall) -> tuple[str, ToolResult | None]:
        arguments = call.arguments.model_dump(mode="json")
        idempotency_key = str(arguments["idempotency_key"])
        key = scoped_key(call.identity.tenant_id, call.cx_id, call.spec.name, idempotency_key)
        request_hash = fingerprint(
            call.identity.tenant_id,
            call.cx_id,
            call.spec.name,
            {name: value for name, value in arguments.items() if name != "idempotency_key"},
        )
        reservation = await self._idempotency.reserve(key, request_hash)

        if reservation.state is ReservationState.COMPLETED and reservation.result is not None:
            replay = dict(reservation.result)
            replay["deduplicated"] = True
            self._record_success(call, self._clock.monotonic(), deduplicated=True)
            return key, ToolResult(output=replay, deduplicated=True)

        if reservation.state is ReservationState.IN_PROGRESS:
            error = RateLimitedError(operation=call.spec.name)
            error.public_message = IN_PROGRESS_MESSAGE
            raise error

        return key, None

    async def _admit_and_run(self, call: AuthorizedCall) -> ToolOutput:
        if self._semaphore.locked():
            self._metrics.increment("tool_calls_total", tool=call.spec.name, outcome="shed")
        try:
            # Shedding beats queuing: a voice case has a five-minute budget, so a fast
            # refusal the agent can act on beats an answer that arrives too late.
            await asyncio.wait_for(self._semaphore.acquire(), timeout=self._tool_timeout_s / 4)
        except TimeoutError as exc:
            raise OverloadedError(operation=call.spec.name) from exc

        try:
            return await call_with_reliability(
                lambda: self._invoke(call),
                policy=self._retry_policy,
                retry_safe=call.spec.retry_safe,
                timeout_s=min(call.spec.timeout_s, self._tool_timeout_s),
                clock=self._clock,
                jitter=self._jitter,
                breaker=self._breaker,
                on_attempt=lambda attempt: self._metrics.increment(
                    "backend_attempts_total", tool=call.spec.name, stage=str(attempt)
                ),
            )
        finally:
            self._semaphore.release()

    async def _invoke(self, call: AuthorizedCall) -> ToolOutput:
        method = getattr(self._backend, call.spec.name)
        result: ToolOutput = await method(call.identity.tenant_id, call.arguments)
        return result

    def _project(self, output: ToolOutput) -> dict[str, Any]:
        """Serialise and redact. ``in_logs=False``: a customer may see their own data."""
        payload: dict[str, Any] = output.model_dump(mode="json")
        redacted: dict[str, Any] = self._redactor.redact(payload, in_logs=False)
        return redacted

    # --- recording ------------------------------------------------------------------

    def _record_denial(
        self, request: ToolRequest, denied: AuthorizationDeniedError, started: float
    ) -> ToolResult:
        denial = denied.denial
        self._metrics.increment(
            "tool_calls_total",
            tool=request.tool_name if len(request.tool_name) < 64 else "unknown",
            outcome="denied",
            code=str(denial.error.code),
        )
        self._audit.record(
            tool=request.tool_name,
            decision=Decision.REJECTED,
            outcome=Outcome.NOT_EXECUTED,
            correlation_id=request.correlation_id,
            case_id=request.case_id,
            authorization_result=f"denied at {denial.stage}",
            action_requested=request.arguments,
            cx_id=_argument_cx_id(request),
            failure_reason=denial.reason,
            duration_ms=(self._clock.monotonic() - started) * 1000,
            contract_version=request.contract_version,
            extra={"stage": str(denial.stage)},
        )
        logger.warning(
            "tool_call_denied",
            tool=request.tool_name,
            stage=str(denial.stage),
            code=str(denial.error.code),
        )
        return ToolResult(error=denial.error.envelope(request.correlation_id))

    def _record_success(self, call: AuthorizedCall, started: float, *, deduplicated: bool) -> None:
        outcome = Outcome.DEDUPLICATED if deduplicated else Outcome.SUCCESS
        duration_ms = (self._clock.monotonic() - started) * 1000
        self._metrics.increment(
            "tool_calls_total",
            tool=call.spec.name,
            outcome="deduplicated" if deduplicated else "ok",
        )
        self._metrics.observe("tool_duration_seconds", duration_ms / 1000, tool=call.spec.name)
        self._audit.record(
            tool=call.spec.name,
            decision=Decision.ACCEPTED,
            outcome=outcome,
            correlation_id=call.correlation_id,
            case_id=call.case_id,
            authorization_result="allowed",
            approval_result=(
                "pending_supervisor" if call.spec.requires_human_approval else "not_required"
            ),
            action_requested=call.arguments.model_dump(mode="json"),
            cx_id=call.cx_id,
            tenant_id=call.identity.tenant_id,
            role=str(call.identity.role),
            action_executed=not deduplicated,
            duration_ms=duration_ms,
        )
        logger.info("tool_call_completed", tool=call.spec.name, deduplicated=deduplicated)

    def _record_failure(
        self, call: AuthorizedCall, error: TelecomMCPError, started: float
    ) -> ToolResult:
        duration_ms = (self._clock.monotonic() - started) * 1000
        self._metrics.increment(
            "tool_calls_total", tool=call.spec.name, outcome="failed", code=str(error.code)
        )
        self._audit.record(
            tool=call.spec.name,
            decision=Decision.ACCEPTED,
            outcome=Outcome.FAILURE,
            correlation_id=call.correlation_id,
            case_id=call.case_id,
            authorization_result="allowed",
            action_requested=call.arguments.model_dump(mode="json"),
            cx_id=call.cx_id,
            tenant_id=call.identity.tenant_id,
            role=str(call.identity.role),
            action_executed=False,
            failure_reason=str(error.code),
            duration_ms=duration_ms,
        )
        logger.error("tool_call_failed", tool=call.spec.name, code=str(error.code))
        return ToolResult(error=error.envelope(call.correlation_id))


def _argument_cx_id(request: ToolRequest) -> str | None:
    value = request.arguments.get("cx_id")
    return value if isinstance(value, str) and value else None


def visible_tools(scopes: frozenset[Any], specs: tuple[ToolSpec, ...]) -> list[ToolSpec]:
    """Only the tools this identity may actually call.

    Filtering the listing is a security control and a cost control at once: a caller
    cannot discover a tool it may not use, and the model is not charged for carrying
    schemas it can never invoke.
    """
    return [spec for spec in specs if spec.required_scope in scopes]
