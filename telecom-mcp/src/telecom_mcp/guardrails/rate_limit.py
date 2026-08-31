"""A token bucket per identity, so one caller cannot spend everyone's capacity.

This is not the same control as the concurrency semaphore in the executor. The
semaphore protects the process from doing too much at once and is fair to nobody in
particular; this protects every other tenant from one identity's loop, and it answers
before any work is done rather than after a slot is refused.

A bucket refills continuously rather than resetting on a boundary, because a fixed
window lets a caller spend a full window at 59 seconds and another at 61 and be
correct twice while sending double the rate.

The bucket table is bounded. An unbounded per-identity map is a memory leak with a
denial of service attached: mint tokens for new subjects and the table grows forever.
Least-recently-used entries are dropped, which is safe because a dropped bucket
returns full and the worst case is one caller getting a free burst.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Final

from telecom_mcp.domain.ports import Clock
from telecom_mcp.guardrails.decision import ALLOWED, GuardrailDecision, GuardrailStage
from telecom_mcp.guardrails.policy import GuardrailPolicy

#: How many identities are tracked before the coldest is forgotten.
MAX_TRACKED_IDENTITIES: Final = 10_000


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated_at: float


class RateLimiter:
    """Continuous-refill token buckets keyed by tenant and subject."""

    def __init__(
        self,
        policy: GuardrailPolicy,
        clock: Clock,
        *,
        max_tracked: int = MAX_TRACKED_IDENTITIES,
    ) -> None:
        self._clock = clock
        self._policy = policy
        self._max_tracked = max_tracked
        self._capacity = float(max(policy.rate_limit_burst, 1))
        self._refill_per_second = policy.rate_limit_per_minute / 60.0
        self._lock = threading.Lock()
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()

    @property
    def tracked(self) -> int:
        """How many identities currently hold a bucket. For tests and for a gauge."""
        with self._lock:
            return len(self._buckets)

    def check(self, tenant_id: str, subject: str) -> GuardrailDecision:
        """Spend one token, or refuse. Never blocks and never sleeps."""
        if not self._policy.enabled:
            return ALLOWED

        key = f"{tenant_id}\x1f{subject}"
        now = self._clock.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=self._capacity, updated_at=now)
                self._buckets[key] = bucket
                self._evict_if_needed()
            else:
                self._buckets.move_to_end(key)
                elapsed = max(now - bucket.updated_at, 0.0)
                bucket.tokens = min(
                    self._capacity, bucket.tokens + elapsed * self._refill_per_second
                )
                bucket.updated_at = now

            if bucket.tokens < 1.0:
                # The identity is not named in the reason. A rate-limit message that
                # carries a subject turns a log line into a customer record.
                return GuardrailDecision.block(
                    GuardrailStage.RATE_LIMIT,
                    "per_identity",
                    f"identity exceeded {self._policy.rate_limit_per_minute} calls per "
                    f"minute (burst {self._policy.rate_limit_burst})",
                )
            bucket.tokens -= 1.0
            return ALLOWED

    def _evict_if_needed(self) -> None:
        """Caller holds the lock."""
        while len(self._buckets) > self._max_tracked:
            self._buckets.popitem(last=False)
