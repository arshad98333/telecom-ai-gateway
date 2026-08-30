"""Structured logging: one machine-readable line per event, with redaction built in.

Every line carries the correlation identifier generated at the entry point, so a
single customer report ("it failed around three o'clock") can be traced end to end
without adding code. Timestamps are UTC with an explicit zone marker, because
correlating across machines is impossible without one.

Redaction runs as a processor rather than at each call site, so a developer cannot
forget it. There is no code path that writes a log line without it.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import structlog

from telecom_middleware.observability.redaction import Redactor

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_case_id: ContextVar[str | None] = ContextVar("case_id", default=None)

#: Keys the redactor must not rewrite: they are ours, and already safe.
_STRUCTURAL_KEYS = frozenset({"event", "level", "timestamp", "logger", "correlation_id", "case_id"})


def current_correlation_id() -> str | None:
    """The correlation identifier for the request being handled, if any."""
    return _correlation_id.get()


def current_case_id() -> str | None:
    return _case_id.get()


@contextmanager
def request_context(correlation_id: str, case_id: str | None = None) -> Iterator[None]:
    """Bind identifiers for the duration of one request, then restore what was there.

    Restoring rather than clearing matters: a nested call must not wipe the caller's
    context when it finishes.
    """
    correlation_token = _correlation_id.set(correlation_id)
    case_token = _case_id.set(case_id)
    try:
        yield
    finally:
        _correlation_id.reset(correlation_token)
        _case_id.reset(case_token)


def _add_context(
    _logger: object, _name: str, event: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    correlation_id = _correlation_id.get()
    if correlation_id is not None:
        event.setdefault("correlation_id", correlation_id)
    case_id = _case_id.get()
    if case_id is not None:
        event.setdefault("case_id", case_id)
    return event


def _redaction_processor(
    redactor: Redactor,
) -> Any:
    def process(
        _logger: object, _name: str, event: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        for key, value in list(event.items()):
            if key in _STRUCTURAL_KEYS:
                continue
            event[key] = redactor.redact({key: value})[key]
        return event

    return process


def configure_logging(
    *,
    level: str,
    service_name: str,
    redactor: Redactor,
    stream: Any | None = None,
    json_output: bool = True,
) -> None:
    """Configure logging for the whole process. Called once, at startup.

    Logs go to stderr so a container's stdout stays free for anything that needs it,
    and so a log collector reading stderr never interleaves with application output.
    """
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    logging.basicConfig(
        format="%(message)s",
        stream=stream or sys.stderr,
        level=getattr(logging, level),
        force=True,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _add_context,
            structlog.processors.StackInfoRenderer(),
            _redaction_processor(redactor),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level)),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )
    structlog.contextvars.bind_contextvars(service=service_name)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound logger. Never construct one directly."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
