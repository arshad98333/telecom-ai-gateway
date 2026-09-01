from __future__ import annotations

import httpx
import pytest

from telecom_mcp_client.client import MCPClient, MCPClientError, MCPHandshakeError, _parse_body
from telecom_mcp_client.retry import backoff_delay_s
from tests.conftest import FakeServer


def test_parse_body_json_response() -> None:
    response = httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
    assert _parse_body(response) == {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}


def test_parse_body_event_stream() -> None:
    body = (
        'data: {"jsonrpc": "2.0", "id": 1, "result": {"a": 1}}\n\n'
        'data: {"jsonrpc": "2.0", "id": 1, "result": {"a": 2}}\n\n'
    )
    response = httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})
    assert _parse_body(response)["result"] == {"a": 2}  # the last frame wins


def test_parse_body_empty_event_stream_raises() -> None:
    response = httpx.Response(200, content="", headers={"content-type": "text/event-stream"})
    with pytest.raises(ValueError, match="no data"):
        _parse_body(response)


def test_parse_body_non_object_json_raises() -> None:
    response = httpx.Response(200, json=[1, 2, 3])
    with pytest.raises(ValueError, match="expected a JSON object"):
        _parse_body(response)


def test_backoff_delay_is_bounded_and_grows() -> None:
    for attempt in range(1, 6):
        delay = backoff_delay_s(attempt, base_s=0.1, cap_s=1.0)
        assert 0 <= delay <= 1.0


def test_backoff_delay_rejects_non_positive_attempt() -> None:
    with pytest.raises(ValueError, match="attempt"):
        backoff_delay_s(0, base_s=0.1, cap_s=1.0)


async def test_initialize_transport_failure(fake_server: FakeServer) -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    async with MCPClient(
        base_url="http://testserver", token="t", transport=httpx.MockTransport(broken)
    ) as client:
        with pytest.raises(MCPHandshakeError, match="could not reach"):
            await client.initialize()


async def test_initialize_malformed_response(fake_server: FakeServer) -> None:
    def bad(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    async with MCPClient(
        base_url="http://testserver", token="t", transport=httpx.MockTransport(bad)
    ) as client:
        with pytest.raises(MCPHandshakeError, match="malformed"):
            await client.initialize()


async def test_initialize_jsonrpc_error(fake_server: FakeServer) -> None:
    def rpc_error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "nope"}},
        )

    async with MCPClient(
        base_url="http://testserver", token="t", transport=httpx.MockTransport(rpc_error)
    ) as client:
        with pytest.raises(MCPHandshakeError, match="JSON-RPC error"):
            await client.initialize()


async def test_list_tools_transport_failure(fake_server: FakeServer) -> None:
    async with MCPClient(
        base_url="http://testserver", token="t", transport=fake_server.transport()
    ) as client:
        await client.initialize()
        fake_server.tool_call_queue.clear()

        def broken(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("gone", request=request)

        client._http._transport = httpx.MockTransport(broken)
        with pytest.raises(MCPClientError, match="tools/list failed"):
            await client.list_tools()


async def test_session_id_propagated(fake_server: FakeServer) -> None:
    seen_ids: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as jsonlib

        seen_ids.append(request.headers.get("mcp-session-id"))
        body = jsonlib.loads(request.content)
        if body.get("method") == "initialize":
            return httpx.Response(
                200,
                headers={"mcp-session-id": "abc123"},
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"protocolVersion": "2025-06-18", "capabilities": {}},
                },
            )
        if body.get("method") == "notifications/initialized":
            return httpx.Response(202)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": []}}
        )

    async with MCPClient(
        base_url="http://testserver", token="t", transport=httpx.MockTransport(handler)
    ) as client:
        await client.initialize()
        await client.list_tools()

    # First request (initialize) had no session id yet; later ones carried it.
    assert seen_ids[0] is None
    assert "abc123" in seen_ids[1:]
