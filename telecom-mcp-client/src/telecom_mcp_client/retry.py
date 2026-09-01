"""Exponential backoff with full jitter.

Not a generic retry decorator — the decision of *whether* to retry a given
failure lives in `client.py`, next to the typed outcome it is deciding about.
This module only computes *how long to wait* between attempts once that
decision has already been made to retry.
"""

from __future__ import annotations

import random


def backoff_delay_s(attempt: int, *, base_s: float, cap_s: float) -> float:
    """Delay before the given attempt (1-indexed: the wait before attempt 2, 3, ...).

    Full jitter (AWS's term): draw uniformly from ``[0, min(cap, base * 2**attempt))``.
    Picked over "equal jitter" or no jitter because it is what actually breaks up a
    thundering herd of clients that all failed at the same moment and would otherwise
    all retry at the same moment too.
    """
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    ceiling = min(cap_s, base_s * (2 ** (attempt - 1)))
    return random.uniform(0, ceiling)  # noqa: S311  # nosec B311 - jitter, not crypto
