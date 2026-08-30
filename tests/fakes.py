"""Deterministic implementations of the ambient ports, for tests only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class FrozenClock:
    """A clock that only moves when a test moves it."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        self._monotonic = 1000.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._monotonic

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
        self._monotonic += seconds


class SequentialIds:
    """Predictable identifiers, so assertions can name them."""

    def __init__(self, prefix: str = "id") -> None:
        self._prefix = prefix
        self._n = 0

    def new_id(self) -> str:
        self._n += 1
        return f"{self._prefix}-{self._n}"


class NoJitter:
    """Removes randomness from backoff so delays are exact in tests."""

    def uniform(self, low: float, high: float) -> float:
        return low
