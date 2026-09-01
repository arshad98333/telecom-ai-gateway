"""A fake MCP-shaped HTTP server, built on `httpx.MockTransport` — no live telecom-mcp
instance, no network, no ASGI app. `FakeServer` is a small scripted state machine that
speaks just enough of the streamable-HTTP + JSON-RPC protocol to drive every branch in
`client.py`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest


@dataclass
class FakeServer:
    """Scripted responses, consumed one per matching call. `handlers["tools/call"]` is
    a queue: each call to `tools/call` pops the next entry (a `Handler`) and hands it
    the request; append more than one to script a "fails twice, then succeeds" test.
    """

    require_bearer: str | None = None
    initialize_ok: bool = True
    tool_call_queue: list[Callable[[dict[str, Any]], httpx.Response]] = field(default_factory=list)
    list_tools_result: list[dict[str, Any]] = field(default_factory=list)
    requests_seen: list[httpx.Request] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests_seen.append(request)
        assert request.url.path == "/mcp/", (
            f"client must always POST the trailing-slash path, got {request.url.path}"
        )
        assert "application/json" in request.headers.get("accept", "")

        if self.require_bearer is not None:
            auth = request.headers.get("authorization", "")
            if auth != f"Bearer {self.require_bearer}":
                return httpx.Response(401, json={"detail": "unauthorized"})

        body = json.loads(request.content)
        method = body.get("method")

        if method == "initialize":
            if not self.initialize_ok:
                return httpx.Response(500, json={"detail": "server not ready"})
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": "telecom-mcp-tools", "version": "1.2.0"},
                    },
                },
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/list":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"tools": self.list_tools_result},
                },
            )
        if method == "tools/call":
            if not self.tool_call_queue:
                raise AssertionError("tools/call invoked with nothing left scripted")
            make_response = self.tool_call_queue.pop(0)
            return make_response(body)

        raise AssertionError(f"unscripted method: {method}")

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def ok_result(
    body: dict[str, Any], *, content: list[dict[str, Any]] | None = None
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": body["id"],
            "result": {
                "content": content if content is not None else [{"type": "text", "text": "ok"}]
            },
        },
    )


def refused_result(body: dict[str, Any], *, text: str = "refused") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": body["id"],
            "result": {"content": [{"type": "text", "text": text}], "isError": True},
        },
    )


def jsonrpc_error(
    body: dict[str, Any], *, code: int = -32602, message: str = "bad params"
) -> httpx.Response:
    return httpx.Response(
        200,
        json={"jsonrpc": "2.0", "id": body["id"], "error": {"code": code, "message": message}},
    )


@pytest.fixture
def fake_server() -> FakeServer:
    return FakeServer()
