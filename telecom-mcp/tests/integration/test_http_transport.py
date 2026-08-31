"""The HTTP transport, driven the way an operator and a client actually drive it."""

import httpx
import pytest
from starlette.applications import Starlette

from telecom_mcp.api.http_app import build_http_app
from telecom_mcp.api.server import TelecomMCPServer
from telecom_mcp.api.tokens import (
    ContextTokenSource,
    bind_request_token,
    reset_request_token,
)
from tests.factory import build_test_application
from tests.fakes import SequentialIds


def build_app() -> tuple[Starlette, object]:
    harness = build_test_application()
    server = TelecomMCPServer(
        harness.app, tokens=ContextTokenSource(), id_generator=SequentialIds("corr")
    )
    return build_http_app(harness.app, server), harness


async def client_for(app: Starlette) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def test_liveness_answers_even_when_a_dependency_is_down() -> None:
    app, harness = build_app()
    harness.backend.failures.unhealthy = True  # type: ignore[attr-defined]

    async with await client_for(app) as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


async def test_readiness_reports_unhealthy_when_the_middleware_is_down() -> None:
    app, harness = build_app()
    harness.backend.failures.unhealthy = True  # type: ignore[attr-defined]

    async with await client_for(app) as client:
        response = await client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unhealthy"
    assert body["components"][0]["name"] == "telecom_middleware"


async def test_readiness_passes_when_every_dependency_answers() -> None:
    app, _ = build_app()

    async with await client_for(app) as client:
        response = await client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


async def test_metrics_are_exposed_in_the_format_a_scraper_reads() -> None:
    app, _ = build_app()

    async with await client_for(app) as client:
        response = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")


def test_the_bearer_prefix_is_stripped_and_the_binding_is_restored() -> None:
    source = ContextTokenSource()
    assert source.current_token() == ""

    outer = bind_request_token("Bearer outer-token")
    assert source.current_token() == "outer-token"

    inner = bind_request_token("inner-token")  # a bare token is accepted too
    assert source.current_token() == "inner-token"

    reset_request_token(inner)
    assert source.current_token() == "outer-token"

    reset_request_token(outer)
    assert source.current_token() == ""


@pytest.mark.parametrize("header", [None, "", "Bearer   "])
def test_an_absent_or_empty_authorization_header_yields_no_token(header: str | None) -> None:
    token = bind_request_token(header)
    try:
        assert ContextTokenSource().current_token() == ""
    finally:
        reset_request_token(token)


async def test_an_mcp_request_over_http_reaches_the_tool_and_uses_the_request_token() -> None:
    from tests.factory import CUSTOMER, make_token

    app, _ = build_app()
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "get_customer_account", "arguments": {"cx_id": CUSTOMER}},
    }

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        response = await client.post(
            "/mcp/",
            json=payload,
            headers={
                "Authorization": f"Bearer {make_token()}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )

    assert response.status_code in (200, 202), response.text
    assert "J. Okonkwo" in response.text or response.status_code == 202


async def test_an_mcp_request_without_a_token_is_refused_rather_than_served() -> None:
    from tests.factory import CUSTOMER

    app, _ = build_app()
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "get_customer_account", "arguments": {"cx_id": CUSTOMER}},
    }

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        response = await client.post(
            "/mcp/",
            json=payload,
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )

    assert "J. Okonkwo" not in response.text
