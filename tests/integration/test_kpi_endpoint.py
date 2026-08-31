"""The endpoint that answers 'which objective is breached' without a dashboard."""

from __future__ import annotations

import httpx
from starlette.applications import Starlette

from telecom_mcp.api.http_app import build_http_app
from telecom_mcp.api.server import TelecomMCPServer
from telecom_mcp.api.tokens import ContextTokenSource
from tests.factory import TestApplication as Harness  # 'Test' prefix confuses collection
from tests.factory import build_test_application
from tests.fakes import SequentialIds


def build_app() -> tuple[Starlette, Harness]:
    harness = build_test_application()
    server = TelecomMCPServer(
        harness.app, tokens=ContextTokenSource(), id_generator=SequentialIds("corr")
    )
    return build_http_app(harness.app, server), harness


async def client_for(app: Starlette) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def test_it_answers_on_a_service_that_has_done_nothing_yet() -> None:
    app, _ = build_app()
    async with await client_for(app) as client:
        response = await client.get("/kpi")

    assert response.status_code == 200
    payload = response.json()
    assert payload["kpis"]
    assert payload["objectives"]
    assert payload["breached"] == []


async def test_every_kpi_carries_its_meaning_not_just_its_number() -> None:
    app, _ = build_app()
    async with await client_for(app) as client:
        payload = (await client.get("/kpi")).json()

    first = payload["kpis"][0]
    assert first["question"].endswith("?")
    assert first["interpretation"]
    assert first["direction"] in {"up", "down", "neutral"}


async def test_the_families_are_listed_so_a_reader_knows_who_each_number_is_for() -> None:
    app, _ = build_app()
    async with await client_for(app) as client:
        payload = (await client.get("/kpi")).json()
    assert set(payload["families"]) == {"service", "safety", "business"}


async def test_it_reflects_work_the_service_has_actually_done() -> None:
    app, harness = build_app()
    harness.app.metrics.increment("tool_calls_total", tool="get_invoice_summary", outcome="ok")

    async with await client_for(app) as client:
        payload = (await client.get("/kpi")).json()

    calls = next(k for k in payload["kpis"] if k["key"] == "tool_calls")
    assert calls["value"] == 1


async def test_a_breached_objective_does_not_make_the_endpoint_fail() -> None:
    app, harness = build_app()
    for _ in range(200):
        harness.app.metrics.increment("tool_calls_total", tool="t", outcome="failed", code="x")

    async with await client_for(app) as client:
        response = await client.get("/kpi")

    # 200 on purpose: a probe pointed here by mistake must not restart the container.
    assert response.status_code == 200
    assert any(item["kpi"] == "success_ratio" for item in response.json()["breached"])


async def test_it_agrees_with_the_scrape_endpoint() -> None:
    app, harness = build_app()
    harness.app.metrics.increment("tool_calls_total", tool="t", outcome="ok")

    async with await client_for(app) as client:
        scraped = (await client.get("/metrics")).text
        payload = (await client.get("/kpi")).json()

    calls = next(k for k in payload["kpis"] if k["key"] == "tool_calls")
    assert 'tool_calls_total{outcome="ok",tool="t"} 1' in scraped
    assert calls["value"] == 1
