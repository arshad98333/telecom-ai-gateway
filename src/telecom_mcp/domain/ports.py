"""Ambient dependencies that make tests flaky when read straight from the system.

Time, randomness and identifier generation are injected, never imported at the point
of use, so a test can freeze them and assert exact values.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Wall clock. Always returns a timezone-aware UTC value."""

    def now(self) -> datetime: ...

    def monotonic(self) -> float:
        """Seconds from an arbitrary origin, used for durations and timeouts."""
        ...


class IdGenerator(Protocol):
    """Source of correlation, case and audit identifiers."""

    def new_id(self) -> str: ...


class Jitter(Protocol):
    """Randomness used by retry backoff, isolated so backoff is testable."""

    def uniform(self, low: float, high: float) -> float: ...


class SystemClock:
    """The real clock. Records in coordinated universal time, with an explicit zone."""

    __slots__ = ()

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        import time

        return time.monotonic()


class UUIDGenerator:
    """The real identifier source."""

    __slots__ = ()

    def new_id(self) -> str:
        return str(uuid.uuid4())


class SystemJitter:
    """The real jitter source. Not used for anything security-sensitive."""

    __slots__ = ()

    def uniform(self, low: float, high: float) -> float:
        return random.uniform(low, high)  # noqa: S311 - backoff jitter, not cryptography
