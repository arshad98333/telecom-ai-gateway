"""A bucket that refills continuously, and a table that cannot grow forever."""

from __future__ import annotations

from datetime import UTC, datetime

from telecom_mcp.guardrails.decision import GuardrailStage
from telecom_mcp.guardrails.policy import GuardrailPolicy
from telecom_mcp.guardrails.rate_limit import RateLimiter


class SteppableClock:
    def __init__(self) -> None:
        self.seconds = 0.0

    def now(self) -> datetime:
        return datetime(2026, 3, 1, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.seconds

    def advance(self, seconds: float) -> None:
        self.seconds += seconds


POLICY = GuardrailPolicy(rate_limit_per_minute=60, rate_limit_burst=3)


def test_a_burst_is_allowed_and_then_refused() -> None:
    limiter = RateLimiter(POLICY, SteppableClock())
    assert all(limiter.check("t1", "cx-1").allowed for _ in range(3))
    decision = limiter.check("t1", "cx-1")
    assert decision.violation is not None
    assert decision.violation.stage is GuardrailStage.RATE_LIMIT


def test_the_bucket_refills_with_time_rather_than_on_a_boundary() -> None:
    clock = SteppableClock()
    limiter = RateLimiter(POLICY, clock)
    for _ in range(3):
        limiter.check("t1", "cx-1")
    assert not limiter.check("t1", "cx-1").allowed
    clock.advance(1.0)  # 60 per minute is one token per second
    assert limiter.check("t1", "cx-1").allowed
    assert not limiter.check("t1", "cx-1").allowed


def test_refill_never_exceeds_the_burst_capacity() -> None:
    clock = SteppableClock()
    limiter = RateLimiter(POLICY, clock)
    limiter.check("t1", "cx-1")
    clock.advance(3_600)
    assert all(limiter.check("t1", "cx-1").allowed for _ in range(3))
    assert not limiter.check("t1", "cx-1").allowed


def test_identities_do_not_share_a_bucket() -> None:
    limiter = RateLimiter(POLICY, SteppableClock())
    for _ in range(3):
        limiter.check("t1", "cx-1")
    assert not limiter.check("t1", "cx-1").allowed
    assert limiter.check("t1", "cx-2").allowed


def test_tenants_do_not_share_a_bucket_even_for_the_same_subject() -> None:
    limiter = RateLimiter(POLICY, SteppableClock())
    for _ in range(3):
        limiter.check("t1", "cx-1")
    assert limiter.check("t2", "cx-1").allowed


def test_the_reason_never_names_the_identity() -> None:
    limiter = RateLimiter(POLICY, SteppableClock())
    for _ in range(4):
        decision = limiter.check("tenant-alpha", "cx-secret-1001")
    assert decision.violation is not None
    assert "cx-secret-1001" not in decision.violation.reason
    assert "tenant-alpha" not in decision.violation.reason


def test_the_bucket_table_is_bounded() -> None:
    limiter = RateLimiter(POLICY, SteppableClock(), max_tracked=10)
    for index in range(50):
        limiter.check("t1", f"cx-{index}")
    assert limiter.tracked <= 10


def test_a_disabled_policy_limits_nothing() -> None:
    limiter = RateLimiter(GuardrailPolicy.disabled(), SteppableClock())
    assert all(limiter.check("t1", "cx-1").allowed for _ in range(100))
