"""Retry, backoff and the circuit breaker.

Three rules, each of which exists because its absence causes a specific incident.

*Only retry what is safe to repeat.* A retry of an unsafe write is how a customer gets
charged twice. Safety is declared per tool in the catalogue, never guessed here.

*Wait longer between attempts, and vary the wait.* Retrying immediately in a tight
loop turns a small outage into one you caused, and retrying in lockstep across
replicas produces a thundering herd the moment a dependency recovers.

*Stop calling a dependency that is failing.* The breaker turns a slow cascade of
timeouts into a fast, safe refusal, which is what keeps a five-minute voice case
inside its budget.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar

from telecom_mcp.domain.errors import (
    BackendError,
    BackendTimeoutError,
    CircuitOpenError,
    TelecomMCPError,
)
from telecom_mcp.domain.ports import Clock, Jitter

T = TypeVar("T")

#: Only these are ever retried. Anything else is a bug or a refusal, and repeating it
#: just spends the customer's time.
RETRYABLE_ERRORS: tuple[type[TelecomMCPError], ...] = (BackendTimeoutError, BackendError)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How many times, how long to wait, and whether this operation may repeat at all."""

    attempts: int = 2
    base_delay_s: float = 0.2
    max_delay_s: float = 2.0
    #: Jitter is a fraction of the computed delay, applied downward.
    jitter_ratio: float = 0.5

    def delay_for(self, attempt: int, jitter: Jitter) -> float:
        """Exponential backoff with decorrelated jitter, capped."""
        raw = min(self.base_delay_s * (2**attempt), self.max_delay_s)
        low = raw * (1.0 - self.jitter_ratio)
        return jitter.uniform(low, raw)


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """One breaker per backend route.

    Closed: calls pass. After ``failure_threshold`` consecutive failures it opens and
    refuses immediately. After ``reset_timeout_s`` it half-opens and lets exactly one
    call through; that call decides whether it closes again or reopens.
    """

    def __init__(
        self,
        *,
        clock: Clock,
        failure_threshold: int = 5,
        reset_timeout_s: float = 30.0,
        name: str = "backend",
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        self._clock = clock
        self._threshold = failure_threshold
        self._reset_timeout_s = reset_timeout_s
        self._name = name
        self._failures = 0
        self._opened_at: float | None = None
        self._half_open_in_flight = False

    @property
    def state(self) -> BreakerState:
        if self._opened_at is None:
            return BreakerState.CLOSED
        if self._clock.monotonic() - self._opened_at >= self._reset_timeout_s:
            return BreakerState.HALF_OPEN
        return BreakerState.OPEN

    def before_call(self) -> None:
        """Raise when the breaker refuses this call."""
        state = self.state
        if state is BreakerState.OPEN:
            raise CircuitOpenError(operation=self._name)
        if state is BreakerState.HALF_OPEN:
            if self._half_open_in_flight:
                # Only one probe at a time; the rest fail fast rather than pile on.
                raise CircuitOpenError(operation=self._name)
            self._half_open_in_flight = True

    def on_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._half_open_in_flight = False

    def on_failure(self) -> None:
        self._half_open_in_flight = False
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = self._clock.monotonic()


async def call_with_reliability(
    operation: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
    retry_safe: bool,
    timeout_s: float,
    clock: Clock,
    jitter: Jitter,
    breaker: CircuitBreaker | None = None,
    on_attempt: Callable[[int], None] | None = None,
) -> T:
    """Run ``operation`` with a total time budget, bounded retries and a breaker.

    ``timeout_s`` is the budget for everything, not per attempt. A caller promised an
    answer in ten seconds must not wait thirty because three attempts each took ten.
    """
    deadline = clock.monotonic() + timeout_s
    max_attempts = policy.attempts + 1 if retry_safe else 1
    last_error: TelecomMCPError | None = None

    for attempt in range(max_attempts):
        remaining = deadline - clock.monotonic()
        if remaining <= 0:
            raise last_error or BackendTimeoutError(operation="call_with_reliability")

        if breaker is not None:
            breaker.before_call()
        if on_attempt is not None:
            on_attempt(attempt)

        try:
            result = await asyncio.wait_for(operation(), timeout=remaining)
        except TimeoutError as exc:
            last_error = BackendTimeoutError(operation="call_with_reliability")
            if breaker is not None:
                breaker.on_failure()
            # The budget is spent by definition; there is no time left to try again.
            raise last_error from exc
        except RETRYABLE_ERRORS as exc:
            last_error = exc
            if breaker is not None:
                breaker.on_failure()
        except TelecomMCPError:
            # Not retryable: a refusal or a malformed response. Repeating it is waste.
            if breaker is not None:
                breaker.on_success()
            raise
        else:
            if breaker is not None:
                breaker.on_success()
            return result

        if attempt + 1 >= max_attempts:
            break
        delay = policy.delay_for(attempt, jitter)
        if clock.monotonic() + delay >= deadline:
            break
        await asyncio.sleep(delay)

    raise last_error or BackendError(operation="call_with_reliability")
