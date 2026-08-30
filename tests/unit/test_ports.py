"""The ambient ports must be UTC-aware and injectable."""

from datetime import UTC

from telecom_mcp.domain.ports import SystemClock, SystemJitter, UUIDGenerator
from tests.fakes import FrozenClock, NoJitter, SequentialIds


def test_system_clock_is_timezone_aware_utc() -> None:
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert now.utcoffset() == UTC.utcoffset(None)


def test_system_clock_monotonic_does_not_go_backwards() -> None:
    clock = SystemClock()
    assert clock.monotonic() <= clock.monotonic()


def test_uuid_generator_produces_distinct_values() -> None:
    gen = UUIDGenerator()
    assert gen.new_id() != gen.new_id()


def test_system_jitter_stays_inside_its_bounds() -> None:
    assert 0.0 <= SystemJitter().uniform(0.0, 1.0) <= 1.0


def test_frozen_clock_only_moves_when_told() -> None:
    clock = FrozenClock()
    first = clock.now()
    assert clock.now() == first
    clock.advance(30)
    assert (clock.now() - first).total_seconds() == 30
    assert clock.monotonic() == 1030.0


def test_test_doubles_are_deterministic() -> None:
    ids = SequentialIds("corr")
    assert [ids.new_id(), ids.new_id()] == ["corr-1", "corr-2"]
    assert NoJitter().uniform(0.5, 5.0) == 0.5
