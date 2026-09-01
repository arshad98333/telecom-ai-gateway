"""Typed result model for a tool call.

The whole point of this module is that a caller never has to `try/except` a
generic exception to find out what happened, and never has to guess whether a
non-2xx result is safe to treat as "the tool said no" versus "something broke".
`call_tool` always returns a `ToolCallResult`; it raises only for programmer
errors (bad arguments to the client itself), never for anything the server or
the network did.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Outcome(Enum):
    """What happened to a tool call, at the granularity a caller needs to branch on."""

    #: The call reached the server, was accepted, and the tool ran. `isError` on the
    #: MCP result may still be true — the *tool* can refuse (see REFUSED) — this is
    #: strictly "the transport and protocol worked and we have a real answer".
    OK = "ok"

    #: The server understood the request perfectly well and refused it: bad auth
    #: (401/403), a validation error, or an MCP `CallToolResult` with `isError=true`.
    #: This is an expected, well-formed outcome — not a crash — and must never be
    #: retried, because retrying an authz refusal or a validation error just wastes
    #: a round trip on an answer that will not change.
    REFUSED = "refused"

    #: The request never got a response at all: DNS failure, connection refused,
    #: connection reset, TLS failure. Distinct from a timeout because a transport
    #: failure this clean means the request never reached the server, which is
    #: exactly the condition under which a retry is safe even for a mutating tool.
    TRANSPORT_ERROR = "transport_error"

    #: The client gave up waiting for a response. Unlike TRANSPORT_ERROR, a timeout
    #: does not tell you whether the server received and is still processing the
    #: request — so retrying a non-idempotent tool on a timeout can duplicate a
    #: write. See `retryable` on `ToolCallError`.
    TIMEOUT = "timeout"

    #: The server responded, but the body was not a well-formed MCP/JSON-RPC
    #: response we can interpret — not valid JSON, missing `result`/`error`, or a
    #: shape this client does not recognise. Never retried: retrying a malformed
    #: response does not become a well-formed one.
    MALFORMED_RESPONSE = "malformed_response"


@dataclass(frozen=True, slots=True)
class ToolCallOk:
    """The tool ran and returned a result. `is_error` mirrors the MCP `CallToolResult`
    flag: a tool can still report `is_error=True` (see `ToolCallResult.refused` for the
    more common way that surfaces) if it returns content describing its own failure
    rather than using the JSON-RPC error channel."""

    tool: str
    content: list[dict[str, Any]]
    structured_content: dict[str, Any] | None = None
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class ToolCallError:
    """Something other than a clean success. `outcome` says which of the four
    non-OK categories this is; `message` is human-readable; `status_code` is the
    HTTP status when there was one; `retryable` is what the client itself decided
    about retrying this specific attempt (informational — the client does not
    re-raise this to ask the caller for permission, it already made the call)."""

    outcome: Outcome
    message: str
    tool: str | None = None
    status_code: int | None = None
    retryable: bool = False
    attempts: int = 1
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolCallResult:
    """What every `MCPClient.call_tool` returns. Exactly one of `ok` / `error` is set;
    check `.outcome` first, or use `.ok is not None` to narrow."""

    outcome: Outcome
    ok: ToolCallOk | None = None
    error: ToolCallError | None = None

    @property
    def refused(self) -> bool:
        return self.outcome is Outcome.REFUSED

    @property
    def succeeded(self) -> bool:
        return self.outcome is Outcome.OK and not (self.ok and self.ok.is_error)
