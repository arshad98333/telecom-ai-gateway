"""An MCP client for telecom-mcp's streamable-HTTP endpoint.

Implements the handshake the MCP spec actually requires — `initialize`, then the
`notifications/initialized` notification, then `tools/list` / `tools/call` — over a
plain `httpx.AsyncClient` rather than the `mcp` SDK. The SDK's client session brings a
lot of machinery (its own retry-free assumptions, its own timeout model, stdio and SSE
transports this client will never use) that would have to be worked around to get the
typed-outcome and selective-retry behaviour this client needs; a lean JSON-RPC layer
over streamable HTTP is a few hundred lines and gives full control over both.

Two things this module is careful about, because they are exactly the footguns
telecom-mcp's own README calls out:

* **The trailing slash.** Every POST goes to ``{base_url}/mcp/`` — never
  ``{base_url}/mcp``. telecom-mcp answers the slash-less path with a 307, and most
  clients that don't re-POST to the Location silently send their JSON-RPC body to a
  GET. This client never triggers that redirect in the first place; if a redirect
  ever comes back anyway (a proxy rewrote the URL, say), it is treated as a protocol
  error, not followed.
* **Retry safety.** `httpx.AsyncClient` is created with ``follow_redirects=False``
  and this client never retries a 4xx or an MCP-level tool refusal — see `Outcome` in
  `models.py` for the full reasoning.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from types import TracebackType
from typing import Any, Final

import httpx
import structlog

from telecom_mcp_client.models import Outcome, ToolCallError, ToolCallOk, ToolCallResult
from telecom_mcp_client.retry import backoff_delay_s
from telecom_mcp_client.tools import is_read_only

logger = structlog.get_logger(__name__)

PROTOCOL_VERSION: Final = "2025-06-18"
_ACCEPT: Final = "application/json, text/event-stream"


class MCPClientError(Exception):
    """Raised only for programmer error or a failed handshake — never for a tool call
    outcome, which is always a `ToolCallResult`, not an exception."""


class MCPHandshakeError(MCPClientError):
    """`initialize` did not complete. Nothing after this point can proceed."""


def _parse_body(response: httpx.Response) -> dict[str, Any]:
    """Streamable HTTP responses are either a single JSON object (the
    `json_response=True` mode telecom-mcp runs with) or a `text/event-stream` body
    carrying one or more `data: <json>` frames, per the MCP transport spec. Either way
    this client wants the last (and, for `json_response=True`, only) JSON-RPC message.
    """
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        last: dict[str, Any] | None = None
        for line in response.text.splitlines():
            if line.startswith("data:"):
                last = json.loads(line[len("data:") :].strip())
        if last is None:
            raise ValueError("event-stream response carried no data: frame")
        return last
    result: Any = response.json()
    if not isinstance(result, dict):
        raise ValueError(f"expected a JSON object, got {type(result).__name__}")
    return result


class MCPClient:
    """One client per telecom-mcp server. Use as an async context manager:

    async with MCPClient(base_url="http://localhost:8080", token=token) as client:
        await client.initialize()
        result = await client.call_tool("get_customer_account", {"cx_id": "CX-1"})
    """

    def __init__(
        self,
        *,
        base_url: str,
        token: str | None = None,
        connect_timeout_s: float = 3.0,
        read_timeout_s: float = 10.0,
        max_retries: int = 3,
        backoff_base_s: float = 0.2,
        backoff_cap_s: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._endpoint = base_url.rstrip("/") + "/mcp/"
        self._token = token
        self._max_retries = max_retries
        self._backoff_base_s = backoff_base_s
        self._backoff_cap_s = backoff_cap_s
        self._session_id: str | None = None
        self._ids = itertools.count(1)
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=connect_timeout_s, read=read_timeout_s, write=5.0, pool=5.0
            ),
            follow_redirects=False,
            transport=transport,
        )
        self._initialized = False

    async def __aenter__(self) -> MCPClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": _ACCEPT}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        response = await self._http.post(self._endpoint, json=payload, headers=self._headers())
        if response.status_code in (307, 308):
            # Should never happen — every request already targets the trailing-slash
            # path — but if a proxy or a future server version rewrites it anyway,
            # surface that plainly rather than silently following (or silently
            # dropping) the redirect.
            raise MCPClientError(
                f"server redirected ({response.status_code}) despite the trailing "
                f"slash; a redirected POST is a known way to lose the request body"
            )
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id
        return response

    async def initialize(self, *, client_name: str = "telecom-mcp-client") -> dict[str, Any]:
        """`initialize` then `notifications/initialized`. Must be called once before
        `list_tools` / `call_tool`."""
        request = {
            "jsonrpc": "2.0",
            "id": next(self._ids),
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": client_name, "version": "0.1.0"},
            },
        }
        try:
            response = await self._post(request)
        except httpx.HTTPError as exc:
            raise MCPHandshakeError(f"could not reach the server: {exc}") from exc
        if response.status_code != 200:
            raise MCPHandshakeError(
                f"initialize was refused: HTTP {response.status_code} {response.text[:500]}"
            )
        try:
            body = _parse_body(response)
        except ValueError as exc:
            raise MCPHandshakeError(f"initialize returned a malformed response: {exc}") from exc
        if "error" in body:
            raise MCPHandshakeError(f"initialize returned a JSON-RPC error: {body['error']}")

        # The initialized notification carries no id and gets no JSON-RPC response —
        # a 202 with an empty body is success.
        notify = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        try:
            await self._post(notify)
        except httpx.HTTPError as exc:
            raise MCPHandshakeError(f"notifications/initialized failed: {exc}") from exc

        self._initialized = True
        result: dict[str, Any] = body.get("result", {})
        logger.info("mcp_client.initialized", server=result.get("serverInfo"))
        return result

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise MCPClientError("call initialize() before using this client")

    async def list_tools(self) -> list[dict[str, Any]]:
        """Returns the raw tool descriptors telecom-mcp advertises for this identity —
        only the tools the caller's token may invoke (telecom-mcp narrows the listing
        per-identity, per its README)."""
        self._require_initialized()
        request = {"jsonrpc": "2.0", "id": next(self._ids), "method": "tools/list", "params": {}}
        try:
            response = await self._post(request)
        except httpx.HTTPError as exc:
            raise MCPClientError(f"tools/list failed: {exc}") from exc
        if response.status_code != 200:
            raise MCPClientError(f"tools/list refused: HTTP {response.status_code}")
        body = _parse_body(response)
        if "error" in body:
            raise MCPClientError(f"tools/list returned a JSON-RPC error: {body['error']}")
        tools: list[dict[str, Any]] = body.get("result", {}).get("tools", [])
        return tools

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> ToolCallResult:
        """Call one tool. Always returns a `ToolCallResult` — see `models.py` for what
        each outcome means and when a call is retried."""
        self._require_initialized()
        attempt = 1
        while True:
            result = await self._call_tool_once(tool, arguments, attempt=attempt)
            if result.outcome is Outcome.OK or not self._should_retry(result, attempt):
                return result
            delay = backoff_delay_s(attempt, base_s=self._backoff_base_s, cap_s=self._backoff_cap_s)
            logger.warning(
                "mcp_client.retrying",
                tool=tool,
                attempt=attempt,
                outcome=result.outcome.value,
                delay_s=round(delay, 3),
            )
            await asyncio.sleep(delay)
            attempt += 1

    def _should_retry(self, result: ToolCallResult, attempt: int) -> bool:
        if attempt >= self._max_retries:
            return False
        error = result.error
        if error is None:
            return False
        if error.outcome is Outcome.TRANSPORT_ERROR:
            # The request never reached the server (or we cannot tell that it did,
            # e.g. connection reset before any bytes came back) — safe for any tool.
            return True
        if error.outcome is Outcome.TIMEOUT:
            # We stopped waiting; the server may still be processing. Only safe to
            # retry when the tool cannot duplicate an effect either way.
            return bool(error.tool and is_read_only(error.tool))
        # REFUSED and MALFORMED_RESPONSE are never retried.
        return False

    async def _call_tool_once(
        self, tool: str, arguments: dict[str, Any], *, attempt: int
    ) -> ToolCallResult:
        request = {
            "jsonrpc": "2.0",
            "id": next(self._ids),
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
        try:
            response = await self._post(request)
        except httpx.TimeoutException as exc:
            return ToolCallResult(
                outcome=Outcome.TIMEOUT,
                error=ToolCallError(
                    outcome=Outcome.TIMEOUT,
                    message=f"timed out waiting for {tool}: {exc}",
                    tool=tool,
                    retryable=is_read_only(tool),
                    attempts=attempt,
                ),
            )
        except httpx.HTTPError as exc:
            # DNS failure, connection refused, connection reset, TLS failure: the
            # request did not get a response, which is what makes it always safe to
            # retry regardless of whether the tool is a write.
            return ToolCallResult(
                outcome=Outcome.TRANSPORT_ERROR,
                error=ToolCallError(
                    outcome=Outcome.TRANSPORT_ERROR,
                    message=f"transport failure calling {tool}: {exc}",
                    tool=tool,
                    retryable=True,
                    attempts=attempt,
                ),
            )
        except MCPClientError as exc:
            # e.g. the redirect guard in _post.
            return ToolCallResult(
                outcome=Outcome.MALFORMED_RESPONSE,
                error=ToolCallError(
                    outcome=Outcome.MALFORMED_RESPONSE,
                    message=str(exc),
                    tool=tool,
                    attempts=attempt,
                ),
            )

        if response.status_code in (401, 403):
            return ToolCallResult(
                outcome=Outcome.REFUSED,
                error=ToolCallError(
                    outcome=Outcome.REFUSED,
                    message=(
                        f"authorization refused: HTTP {response.status_code} {response.text[:500]}"
                    ),
                    tool=tool,
                    status_code=response.status_code,
                    attempts=attempt,
                ),
            )
        if response.status_code == 429:
            # Rate-limited — same shape as a transport hiccup: nothing ran, safe to
            # retry, but it is a clean HTTP status rather than a connection failure.
            return ToolCallResult(
                outcome=Outcome.TRANSPORT_ERROR,
                error=ToolCallError(
                    outcome=Outcome.TRANSPORT_ERROR,
                    message="rate limited (HTTP 429)",
                    tool=tool,
                    status_code=429,
                    retryable=True,
                    attempts=attempt,
                ),
            )
        if 400 <= response.status_code < 500:
            return ToolCallResult(
                outcome=Outcome.REFUSED,
                error=ToolCallError(
                    outcome=Outcome.REFUSED,
                    message=f"request refused: HTTP {response.status_code} {response.text[:500]}",
                    tool=tool,
                    status_code=response.status_code,
                    attempts=attempt,
                ),
            )
        if response.status_code >= 500:
            return ToolCallResult(
                outcome=Outcome.TRANSPORT_ERROR,
                error=ToolCallError(
                    outcome=Outcome.TRANSPORT_ERROR,
                    message=f"server error: HTTP {response.status_code} {response.text[:500]}",
                    tool=tool,
                    status_code=response.status_code,
                    retryable=True,
                    attempts=attempt,
                ),
            )

        try:
            body = _parse_body(response)
        except ValueError as exc:
            return ToolCallResult(
                outcome=Outcome.MALFORMED_RESPONSE,
                error=ToolCallError(
                    outcome=Outcome.MALFORMED_RESPONSE,
                    message=f"response body was not a well-formed MCP message: {exc}",
                    tool=tool,
                    attempts=attempt,
                ),
            )

        if "error" in body:
            rpc_error = body["error"]
            return ToolCallResult(
                outcome=Outcome.REFUSED,
                error=ToolCallError(
                    outcome=Outcome.REFUSED,
                    message=f"JSON-RPC error: {rpc_error}",
                    tool=tool,
                    attempts=attempt,
                    details={"jsonrpc_error": rpc_error},
                ),
            )

        result = body.get("result")
        if not isinstance(result, dict):
            return ToolCallResult(
                outcome=Outcome.MALFORMED_RESPONSE,
                error=ToolCallError(
                    outcome=Outcome.MALFORMED_RESPONSE,
                    message="JSON-RPC response carried no usable 'result' object",
                    tool=tool,
                    attempts=attempt,
                ),
            )

        if "content" not in result:
            return ToolCallResult(
                outcome=Outcome.MALFORMED_RESPONSE,
                error=ToolCallError(
                    outcome=Outcome.MALFORMED_RESPONSE,
                    message="CallToolResult carried no 'content' field",
                    tool=tool,
                    attempts=attempt,
                ),
            )
        content = result["content"]
        if not isinstance(content, list):
            return ToolCallResult(
                outcome=Outcome.MALFORMED_RESPONSE,
                error=ToolCallError(
                    outcome=Outcome.MALFORMED_RESPONSE,
                    message="CallToolResult.content was not a list",
                    tool=tool,
                    attempts=attempt,
                ),
            )

        is_error = bool(result.get("isError", False))
        if is_error:
            # The tool itself refused (validation, authz on the underlying data, a
            # business rule) — a well-formed answer, not a crash, and never retried.
            return ToolCallResult(
                outcome=Outcome.REFUSED,
                error=ToolCallError(
                    outcome=Outcome.REFUSED,
                    message="tool reported isError=true",
                    tool=tool,
                    attempts=attempt,
                    details={"content": content},
                ),
            )

        return ToolCallResult(
            outcome=Outcome.OK,
            ok=ToolCallOk(
                tool=tool,
                content=content,
                structured_content=result.get("structuredContent"),
                is_error=False,
            ),
        )
