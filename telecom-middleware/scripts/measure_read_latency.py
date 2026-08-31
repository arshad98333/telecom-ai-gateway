#!/usr/bin/env python3
"""Measure the read path and fail if it regresses.

The budget in the design is p95 under 150 ms server-side for a single-customer read.
This measures it against the configured store rather than asserting it in prose, and
CI fails when it slips - which is the only way a latency budget survives contact with
six months of changes.

It drives the ASGI application directly, so the number is the service's own cost:
routing, authorization, the query and the projection. Network time to a real client is
not included, and is not this budget.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt

NAMESPACE = "https://telecom.example/"
TENANT = "tenant-eu-1"
CUSTOMER = "CX-1234"


def token(secret: str, audience: str) -> str:
    claims: dict[str, Any] = {
        "sub": "auth0|load-test",
        "aud": audience,
        "exp": int((datetime.now(UTC) + timedelta(minutes=30)).timestamp()),
        "permissions": ["account:read", "billing:read", "service:read", "ticket:read"],
        f"{NAMESPACE}tenant_id": TENANT,
        f"{NAMESPACE}role": "customer",
        f"{NAMESPACE}cx_id": CUSTOMER,
    }
    return jwt.encode(claims, secret, algorithm="HS256")


async def run(requests: int, concurrency: int, budget_ms: float) -> int:
    from telecom_middleware.api.app import build_app
    from telecom_middleware.api.container import build_context
    from telecom_middleware.config.settings import load_settings
    from telecom_middleware.services.seed import seed_demo_data

    settings = load_settings()
    context = build_context(settings, configure_logs=False)
    app = build_app(context, start_realtime=False)

    async with app.router.lifespan_context(app):
        await seed_demo_data(context.store, tenant_id=TENANT, clock=context.clock)
        secret = (
            settings.local_verifier_secret.get_secret_value()
            if settings.local_verifier_secret
            else "unset"
        )
        audience = settings.jwt_audience or "https://api.telecom.example/v1"
        bearer = token(secret, audience)
        headers = {"Authorization": f"Bearer {bearer}"}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://measure"
        ) as client:
            path = f"/api/v1/customers/{CUSTOMER}/invoices?limit=5"

            # Warm up: the first request pays for lazy imports and the connection pool,
            # and including it would measure startup rather than the read path.
            for _ in range(20):
                await client.get(path, headers=headers)

            timings: list[float] = []
            semaphore = asyncio.Semaphore(concurrency)

            async def once() -> None:
                async with semaphore:
                    started = time.perf_counter()
                    response = await client.get(path, headers=headers)
                    timings.append((time.perf_counter() - started) * 1000)
                    if response.status_code != 200:
                        raise SystemExit(f"unexpected status {response.status_code}")

            await asyncio.gather(*(once() for _ in range(requests)))

    timings.sort()
    p50 = statistics.median(timings)
    p95 = timings[int(len(timings) * 0.95) - 1]
    p99 = timings[int(len(timings) * 0.99) - 1]
    print(  # noqa: T201
        f"requests={requests} concurrency={concurrency} "
        f"p50={p50:.1f}ms p95={p95:.1f}ms p99={p99:.1f}ms budget={budget_ms:.0f}ms"
    )

    if p95 > budget_ms:
        print(f"p95 {p95:.1f}ms exceeds the {budget_ms:.0f}ms budget", file=sys.stderr)  # noqa: T201
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--p95-budget-ms", type=float, default=150.0)
    args = parser.parse_args()
    return asyncio.run(run(args.requests, args.concurrency, args.p95_budget_ms))


if __name__ == "__main__":
    raise SystemExit(main())
