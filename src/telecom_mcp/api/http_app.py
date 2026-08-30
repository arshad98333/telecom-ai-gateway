"""The HTTP transport: MCP over streamable HTTP, plus the operational endpoints.

The bearer token is taken from the request's ``Authorization`` header and bound for the
duration of that request only, so two concurrent callers can never see each other's
identity. Liveness, readiness and metrics are separate endpoints with separate
meanings, as described in ``observability/health.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route

from telecom_mcp.api.container import Application
from telecom_mcp.api.server import TelecomMCPServer
from telecom_mcp.api.tokens import bind_request_token, reset_request_token

MCP_PATH = "/mcp"


def build_http_app(application: Application, server: TelecomMCPServer) -> Starlette:
    """Assemble the ASGI application. Import-time work is kept to nothing."""
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    manager = StreamableHTTPSessionManager(app=server.server, json_response=True, stateless=True)

    async def handle_mcp(scope: Any, receive: Any, send: Any) -> None:
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        token = bind_request_token(headers.get("authorization"))
        try:
            await manager.handle_request(scope, receive, send)
        finally:
            reset_request_token(token)

    async def liveness(_request: Request) -> Response:
        return JSONResponse(application.health.liveness().to_dict())

    async def readiness(_request: Request) -> Response:
        report = await application.health.readiness()
        return JSONResponse(report.to_dict(), status_code=report.http_status)

    async def metrics(_request: Request) -> Response:
        return PlainTextResponse(
            application.metrics.render_prometheus(),
            media_type="text/plain; version=0.0.4",
        )

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with manager.run():
            try:
                yield
            finally:
                await application.aclose()

    return Starlette(
        routes=[
            Route("/healthz", liveness, methods=["GET"]),
            Route("/readyz", readiness, methods=["GET"]),
            Route("/metrics", metrics, methods=["GET"]),
            Mount(MCP_PATH, app=handle_mcp),
        ],
        lifespan=lifespan,
    )
