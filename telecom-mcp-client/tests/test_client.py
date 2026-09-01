from __future__ import annotations

from typing import Any

import httpx
import pytest

from telecom_mcp_client.client import MCPClient, MCPClientError, MCPHandshakeError
from telecom_mcp_client.models import Outcome
from tests.conftest import FakeServer, jsonrpc_error, ok_result, refused_result


async def _client(fake_server: FakeServer, **kwargs: Any) -> MCPClient:
    return MCPClient(
        base_url="http://testserver",
        token=kwargs.pop("token", "good-token"),
        transport=fake_server.transport(),
        backoff_base_s=0.001,
        backoff_cap_s=0.01,
        **kwargs,
    )


async def test_successful_tool_call(fake_server: FakeServer) -> None:
    fake_server.require_bearer = "good-token"
    fake_server.tool_call_queue.append(
        lambda body: ok_result(body, content=[{"type": "text", "text": "hello"}])
    )
    async with await _client(fake_server) as client:
        await client.initialize()
        result = await client.call_tool("get_customer_account", {"cx_id": "CX-1"})

    assert result.outcome is Outcome.OK
    assert result.succeeded
    assert result.ok is not None
    assert result.ok.content == [{"type": "text", "text": "hello"}]
    # Every request, including the handshake, hit the trailing-slash path.
    assert all(r.url.path == "/mcp/" for r in fake_server.requests_seen)


async def test_auth_failure_surfaces_and_is_not_retried(fake_server: FakeServer) -> None:
    fake_server.require_bearer = "good-token"
    async with await _client(fake_server, token="wrong-token", max_retries=5) as client:
        with pytest.raises(MCPHandshakeError):
            await client.initialize()

    # Only the one initialize attempt — a 401 is never retried, even during the
    # handshake.
    assert len(fake_server.requests_seen) == 1


async def test_auth_failure_on_tool_call_is_not_retried(fake_server: FakeServer) -> None:
    """Once past a successful handshake, an auth failure on the call itself (a token
    that expired between initialize and call, say) is REFUSED, not retried."""
    calls = {"n": 0}

    def handler(body: dict[str, Any]) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(403, text="forbidden")

    fake_server.tool_call_queue.append(handler)
    async with await _client(fake_server, max_retries=5) as client:
        await client.initialize()
        result = await client.call_tool("get_customer_account", {"cx_id": "CX-1"})

    assert result.outcome is Outcome.REFUSED
    assert result.error is not None
    assert result.error.status_code == 403
    assert calls["n"] == 1


async def test_validation_failure_is_refused_not_retried(fake_server: FakeServer) -> None:
    fake_server.tool_call_queue.append(lambda body: jsonrpc_error(body, message="cx_id required"))
    async with await _client(fake_server, max_retries=5) as client:
        await client.initialize()
        result = await client.call_tool("get_customer_account", {})

    assert result.outcome is Outcome.REFUSED
    assert result.error is not None
    assert "cx_id required" in result.error.message
    assert len(fake_server.tool_call_queue) == 0  # consumed exactly once, never retried


async def test_tool_level_refusal_is_refused_not_retried(fake_server: FakeServer) -> None:
    """An MCP CallToolResult with isError=true — the tool ran and said no — is
    REFUSED, distinct from a JSON-RPC protocol-level error, and also never retried."""
    fake_server.tool_call_queue.append(
        lambda body: refused_result(body, text="cross_account_denied")
    )
    async with await _client(fake_server, max_retries=5) as client:
        await client.initialize()
        result = await client.call_tool("get_customer_account", {"cx_id": "CX-2"})

    assert result.outcome is Outcome.REFUSED
    assert result.error is not None
    assert "isError" in result.error.message


async def test_transient_failure_is_retried_then_succeeds(fake_server: FakeServer) -> None:
    def fails(body: dict[str, Any]) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=None)

    fake_server.tool_call_queue.append(fails)
    fake_server.tool_call_queue.append(fails)
    fake_server.tool_call_queue.append(lambda body: ok_result(body))

    async with await _client(fake_server, max_retries=5) as client:
        await client.initialize()
        result = await client.call_tool("create_support_ticket", {"subject": "x"})

    assert result.outcome is Outcome.OK
    assert result.ok is not None


async def test_retries_exhausted_surfaces_the_last_failure(fake_server: FakeServer) -> None:
    def fails(body: dict[str, Any]) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=None)

    fake_server.tool_call_queue.extend([fails] * 5)

    async with await _client(fake_server, max_retries=3) as client:
        await client.initialize()
        result = await client.call_tool("get_customer_account", {"cx_id": "CX-1"})

    assert result.outcome is Outcome.TRANSPORT_ERROR
    assert result.error is not None
    assert result.error.attempts == 3


async def test_mutating_tool_is_not_retried_on_timeout(fake_server: FakeServer) -> None:
    """A write's mid-call timeout must never be silently retried: the first attempt
    may still land on the server, and a second attempt could duplicate it."""

    def times_out(body: dict[str, Any]) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=None)

    fake_server.tool_call_queue.append(times_out)
    async with await _client(fake_server, max_retries=5) as client:
        await client.initialize()
        result = await client.call_tool("create_support_ticket", {"subject": "x"})

    assert result.outcome is Outcome.TIMEOUT
    assert result.error is not None
    assert result.error.attempts == 1  # never retried
    assert len(fake_server.tool_call_queue) == 0  # popped exactly once


async def test_read_only_tool_retried_on_timeout_succeeds(fake_server: FakeServer) -> None:
    def times_out(body: dict[str, Any]) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=None)

    fake_server.tool_call_queue.append(times_out)
    fake_server.tool_call_queue.append(lambda body: ok_result(body))

    async with await _client(fake_server, max_retries=5) as client:
        await client.initialize()
        result = await client.call_tool("get_customer_account", {"cx_id": "CX-1"})

    assert result.outcome is Outcome.OK


async def test_malformed_response_body(fake_server: FakeServer) -> None:
    fake_server.tool_call_queue.append(lambda body: httpx.Response(200, text="not json at all"))
    async with await _client(fake_server, max_retries=5) as client:
        await client.initialize()
        result = await client.call_tool("get_customer_account", {"cx_id": "CX-1"})

    assert result.outcome is Outcome.MALFORMED_RESPONSE
    assert len(fake_server.tool_call_queue) == 0  # never retried


async def test_result_missing_content_is_malformed(fake_server: FakeServer) -> None:
    def bad(body: dict[str, Any]) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {}})

    fake_server.tool_call_queue.append(bad)
    async with await _client(fake_server, max_retries=5) as client:
        await client.initialize()
        result = await client.call_tool("get_customer_account", {"cx_id": "CX-1"})

    assert result.outcome is Outcome.MALFORMED_RESPONSE


async def test_client_always_posts_the_trailing_slash(fake_server: FakeServer) -> None:
    """The exact footgun telecom-mcp's README warns about: a POST to /mcp (no slash)
    gets a 307 and most clients silently lose the body re-POSTing to a GET. This
    client is asserted (in FakeServer.handler) to always target /mcp/; this test
    additionally proves a call succeeds end-to-end with that invariant enforced."""
    fake_server.tool_call_queue.append(lambda body: ok_result(body))
    async with await _client(fake_server) as client:
        await client.initialize()
        result = await client.call_tool("get_customer_account", {"cx_id": "CX-1"})
    assert result.outcome is Outcome.OK
    assert client._endpoint.endswith("/mcp/")


async def test_a_307_redirect_is_treated_as_a_protocol_error(fake_server: FakeServer) -> None:
    """If a redirect ever does come back anyway (a misbehaving proxy), the client
    must not follow it and must not silently drop the request body."""

    def redirect(body: dict[str, Any]) -> httpx.Response:
        return httpx.Response(307, headers={"Location": "http://testserver/mcp"})

    fake_server.tool_call_queue.append(redirect)
    async with await _client(fake_server, max_retries=5) as client:
        await client.initialize()
        result = await client.call_tool("get_customer_account", {"cx_id": "CX-1"})

    assert result.outcome is Outcome.MALFORMED_RESPONSE
    assert result.error is not None
    assert "redirected" in result.error.message
    assert len(fake_server.tool_call_queue) == 0  # not retried


async def test_list_tools(fake_server: FakeServer) -> None:
    fake_server.list_tools_result = [{"name": "get_customer_account"}]
    async with await _client(fake_server) as client:
        await client.initialize()
        tools = await client.list_tools()
    assert tools == [{"name": "get_customer_account"}]


async def test_call_tool_before_initialize_raises() -> None:
    async with MCPClient(base_url="http://testserver", token="t") as client:
        with pytest.raises(MCPClientError):
            await client.call_tool("get_customer_account", {})


async def test_initialize_failure_is_not_retried_by_the_caller(fake_server: FakeServer) -> None:
    fake_server.initialize_ok = False
    async with await _client(fake_server) as client:
        with pytest.raises(MCPHandshakeError):
            await client.initialize()
    assert len(fake_server.requests_seen) == 1
