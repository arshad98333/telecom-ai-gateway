"""The ambient database session, so a transaction does not change every signature.

A transactional outbox only works if the state change and its event commit together.
Threading a session argument through every repository method would put a storage
concept into every call site; instead the session lives in a context variable that the
MongoDB repositories read, and ``Store.transaction()`` sets it.

The context variable is set and reset around the block, so concurrent requests each
have their own session and none can accidentally join another's transaction.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_session: ContextVar[Any | None] = ContextVar("mongo_session", default=None)


def current_session() -> Any | None:
    """The session for the transaction in progress, or None outside one."""
    return _session.get()


def set_session(session: Any | None) -> Any:
    return _session.set(session)


def reset_session(token: Any) -> None:
    _session.reset(token)


def with_session(options: dict[str, Any]) -> dict[str, Any]:
    """Add the ambient session to a driver call's keyword arguments, if there is one."""
    session = current_session()
    if session is not None:
        options["session"] = session
    return options
