"""Where the bearer token for a tool call comes from, per transport.

MCP itself carries no per-call credential, so the transport supplies one. Over HTTP
that is the ``Authorization`` header of the request the call arrived on; over stdio the
process is already running as one identity, so the token comes from the environment.

Both are behind one interface, so the server code never branches on transport and the
executor is handed a token the same way in every deployment.
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from typing import Protocol

BEARER_PREFIX = "Bearer "
STDIO_TOKEN_VARIABLE = "TELECOM_MCP_ACCESS_TOKEN"  # noqa: S105 - a variable name

_request_token: ContextVar[str | None] = ContextVar("request_token", default=None)


class TokenSource(Protocol):
    def current_token(self) -> str: ...


class EnvTokenSource:
    """Reads the token from the environment on every call, not once at startup.

    Reading it each time means a refreshed token is picked up without a restart.
    """

    def __init__(self, variable: str = STDIO_TOKEN_VARIABLE) -> None:
        self._variable = variable

    def current_token(self) -> str:
        return os.environ.get(self._variable, "")


class ContextTokenSource:
    """Reads the token bound by the HTTP layer for the request being handled."""

    def current_token(self) -> str:
        return _request_token.get() or ""


def bind_request_token(value: str | None) -> object:
    """Bind the token for this request. Returns the token needed to restore it."""
    return _request_token.set(_extract_bearer(value))


def reset_request_token(token: object) -> None:
    _request_token.reset(token)  # type: ignore[arg-type]


def _extract_bearer(header: str | None) -> str | None:
    if not header:
        return None
    if header.startswith(BEARER_PREFIX):
        return header[len(BEARER_PREFIX) :].strip()
    # A bare token is accepted so a misconfigured client fails at verification with a
    # clear reason rather than silently looking unauthenticated.
    return header.strip()
