"""Retry and breaker behaviour, asserted exactly, with no sleeping in real time."""

import asyncio

import pytest

from telecom_mcp.adapters.reliability import (
    BreakerState,
    CircuitBreaker,
    RetryPolicy,
    call_with_reliability,
)
from telecom_mcp.domain.errors import (
    BackendError,
    BackendTimeoutError,
    CircuitOpenError,
    InvalidInputError,
)
from tests.fakes import FrozenClock, NoJitter


class Recorder:
    """An operation that fails a set number of times, then succeeds."""

    def __init__(self, failures: int = 0, error: Exception | None = None) -> None:
        self.calls = 0
        self._failures = failures
        self._error = error or BackendError()

    async def __call__(self) -> str:
        self.calls += 1
        if self.calls <= self._failures:
            raise self._error
        return "ok"


async def run(
    operation: Recorder,
    *,
    retry_safe: bool = True,
    attempts: int = 2,
    timeout_s: float = 10.0,
    breaker: CircuitBreaker | None = None,
    clock: FrozenClock | None = None,
) -> str:
    return await call_with_reliability(
        operation,
        policy=RetryPolicy(attempts=attempts, base_delay_s=0.001, max_delay_s=0.002),
        retry_safe=retry_safe,
        timeout_s=timeout_s,
        clock=clock or FrozenClock(),
        jitter=NoJitter(),
        breaker=breaker,
    )


async def test_a_successful_call_is_made_once() -> None:
    operation = Recorder()

    assert await run(operation) == "ok"
    assert operation.calls == 1


async def test_a_transient_failure_is_retried_and_then_succeeds() -> None:
    operation = Recorder(failures=1)

    assert await run(operation) == "ok"
    assert operation.calls == 2


async def test_retries_are_bounded_and_the_last_error_is_raised() -> None:
    operation = Recorder(failures=99)

    with pytest.raises(BackendError):
        await run(operation, attempts=2)

    assert operation.calls == 3  # the first try plus two retries, never more


async def test_an_unsafe_operation_is_never_retried() -> None:
    # This is the rule that stops a refund being submitted twice.
    operation = Recorder(failures=99)

    with pytest.raises(BackendError):
        await run(operation, retry_safe=False)

    assert operation.calls == 1


async def test_a_refusal_is_not_retried_because_repeating_it_is_waste() -> None:
    operation = Recorder(failures=99, error=InvalidInputError())

    with pytest.raises(InvalidInputError):
        await run(operation)

    assert operation.calls == 1


async def test_backoff_grows_and_stays_inside_its_cap() -> None:
    policy = RetryPolicy(attempts=5, base_delay_s=0.1, max_delay_s=1.0, jitter_ratio=0.0)
    jitter = NoJitter()

    delays = [policy.delay_for(attempt, jitter) for attempt in range(5)]

    assert delays == [0.1, 0.2, 0.4, 0.8, 1.0]


async def test_jitter_only_ever_shortens_the_wait_and_never_removes_it() -> None:
    policy = RetryPolicy(base_delay_s=1.0, jitter_ratio=0.5)

    assert policy.delay_for(0, NoJitter()) == pytest.approx(0.5)


async def test_the_total_budget_is_not_multiplied_by_the_attempt_count() -> None:
    async def slow() -> str:
        await asyncio.sleep(5)
        return "never"

    with pytest.raises(BackendTimeoutError):
        await call_with_reliability(
            slow,
            policy=RetryPolicy(attempts=2, base_delay_s=0.001),
            retry_safe=True,
            timeout_s=0.02,
            clock=FrozenClock(),
            jitter=NoJitter(),
        )


async def test_the_breaker_opens_after_the_threshold_and_then_refuses_immediately() -> None:
    clock = FrozenClock()
    breaker = CircuitBreaker(clock=clock, failure_threshold=2, reset_timeout_s=30.0)
    operation = Recorder(failures=99)

    with pytest.raises(BackendError):
        await run(operation, attempts=1, breaker=breaker, clock=clock)

    assert breaker.state is BreakerState.OPEN

    calls_before = operation.calls
    with pytest.raises(CircuitOpenError):
        await run(operation, attempts=1, breaker=breaker, clock=clock)

    assert operation.calls == calls_before, "an open breaker must not call the backend"


async def test_the_breaker_half_opens_after_the_reset_timeout_and_closes_on_success() -> None:
    clock = FrozenClock()
    breaker = CircuitBreaker(clock=clock, failure_threshold=1, reset_timeout_s=30.0)

    with pytest.raises(BackendError):
        await run(Recorder(failures=99), attempts=0, breaker=breaker, clock=clock)
    after_failure = breaker.state
    clock.advance(31)
    after_timeout = breaker.state
    result = await run(Recorder(), attempts=0, breaker=breaker, clock=clock)
    after_probe = breaker.state

    assert (after_failure, after_timeout, result, after_probe) == (
        BreakerState.OPEN,
        BreakerState.HALF_OPEN,
        "ok",
        BreakerState.CLOSED,
    )


async def test_a_failed_probe_reopens_the_breaker_rather_than_closing_it() -> None:
    clock = FrozenClock()
    breaker = CircuitBreaker(clock=clock, failure_threshold=1, reset_timeout_s=30.0)

    with pytest.raises(BackendError):
        await run(Recorder(failures=99), attempts=0, breaker=breaker, clock=clock)
    clock.advance(31)

    with pytest.raises(BackendError):
        await run(Recorder(failures=99), attempts=0, breaker=breaker, clock=clock)

    assert breaker.state is BreakerState.OPEN


async def test_only_one_probe_is_allowed_through_a_half_open_breaker() -> None:
    clock = FrozenClock()
    breaker = CircuitBreaker(clock=clock, failure_threshold=1, reset_timeout_s=30.0)
    breaker.on_failure()
    clock.advance(31)

    breaker.before_call()  # the probe
    with pytest.raises(CircuitOpenError):
        breaker.before_call()  # everything else fails fast


async def test_a_successful_call_resets_the_failure_count() -> None:
    clock = FrozenClock()
    breaker = CircuitBreaker(clock=clock, failure_threshold=3, reset_timeout_s=30.0)

    breaker.on_failure()
    breaker.on_failure()
    breaker.on_success()
    breaker.on_failure()
    breaker.on_failure()

    assert breaker.state is BreakerState.CLOSED


def test_a_nonsensical_threshold_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        CircuitBreaker(clock=FrozenClock(), failure_threshold=0)
