"""One write path, so no endpoint invents its own deduplication.

Every write goes through here. The shape is the same one the tools package uses, for
the same reason: a repeat that arrives while the first call is still running must not
start a second execution, and a key reused with different input must be an error rather
than a silent replay of the wrong result.

The key is scoped by tenant, customer and operation, so two tenants cannot collide and
a ticket key cannot be replayed against a refund.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from hashlib import sha256
from typing import Any

from telecom_middleware.api.context import AppContext
from telecom_middleware.domain.errors import InvalidInputError, RateLimitedError

#: Told to a caller whose identical request is still running. Retryable by design.
IN_PROGRESS_TITLE = "An identical request is still being processed; retry shortly."


def request_fingerprint(payload: dict[str, Any]) -> str:
    """A stable hash of what the caller asked for, independent of key order."""
    return sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


async def idempotent_write(
    context: AppContext,
    *,
    tenant_id: str,
    scope: str,
    key: str | None,
    payload: dict[str, Any],
    operation: Callable[[], Awaitable[dict[str, Any]]],
) -> tuple[dict[str, Any], bool]:
    """Run ``operation`` at most once per key. Returns the result and whether it replayed."""
    if not key:
        raise InvalidInputError(
            "a write requires an idempotency key",
            detail={"header": "Idempotency-Key"},
        )

    fingerprint = request_fingerprint(payload)
    state, stored = await context.store.idempotency.reserve(
        tenant_id,
        scope,
        key,
        fingerprint,
        now=context.clock.now(),
        ttl_s=context.settings.idempotency_ttl_s,
    )

    if state == "completed" and stored is not None:
        return stored, True
    if state == "in_progress":
        error = RateLimitedError("an identical request is still in flight")
        error.title = IN_PROGRESS_TITLE
        raise error

    try:
        result = await operation()
    except Exception:
        # Free the key so a genuine retry can proceed rather than being told forever
        # that a call is in progress.
        await context.store.idempotency.release(tenant_id, scope, key)
        raise

    await context.store.idempotency.complete(tenant_id, scope, key, result)
    return result, False
