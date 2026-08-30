"""The caller's identity, carried to the adapter without changing every signature.

The middleware authorizes the *person*, not this service. That means the customer's own
token has to reach the HTTP adapter, along with the correlation identifier so one trace
covers both services. Threading those through every backend method would put a
transport concern into the domain's shape, so they travel in a context variable that
the executor sets around one call and the adapter reads.

Set and reset around each call, so two concurrent requests can never see each other's
token — the failure that makes this pattern dangerous when it is done carelessly.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CallContext:
    """What the backend needs to know about the caller of the current tool call."""

    token: str
    correlation_id: str
    idempotency_key: str | None = None
    case_id: str | None = None


_context: ContextVar[CallContext | None] = ContextVar("call_context", default=None)


def current_call() -> CallContext | None:
    return _context.get()


def set_call(context: CallContext) -> object:
    return _context.set(context)


def reset_call(token: object) -> None:
    _context.reset(token)  # type: ignore[arg-type]
