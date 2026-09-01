"""Liveness, readiness and metrics: three endpoints, three different questions.

Liveness asks whether the process can execute code, and consults nothing external -
otherwise one database blip restarts every replica at once. Readiness asks whether this
replica can actually serve, and does consult the database, because a replica that
cannot reach MongoDB should stop receiving traffic rather than fail every request.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from fastapi.responses import PlainTextResponse

from telecom_middleware._version import __version__
from telecom_middleware.api.dependencies import AppContextDep

router = APIRouter(tags=["operations"])


@router.get("/healthz", summary="Liveness: is this process alive")
async def liveness() -> dict[str, Any]:
    return {"status": "healthy", "version": __version__}


@router.get("/readyz", summary="Readiness: can this replica serve")
async def readiness(context: AppContextDep, response: Response) -> dict[str, Any]:
    components: list[dict[str, Any]] = []
    healthy = True
    try:
        await context.store.ping()
    except Exception as exc:  # noqa: BLE001 - any failure means not ready
        healthy = False
        # The type name is enough to act on and cannot carry a connection string.
        components.append({"name": "store", "status": "unhealthy", "detail": type(exc).__name__})
    else:
        components.append({"name": "store", "status": "healthy"})

    response.status_code = 200 if healthy else 503
    return {
        "status": "healthy" if healthy else "unhealthy",
        "version": __version__,
        "components": components,
    }


@router.get("/metrics", summary="Prometheus scrape endpoint", include_in_schema=False)
async def metrics() -> PlainTextResponse:
    body = (
        "# TYPE telecom_middleware_info gauge\n"
        f'telecom_middleware_info{{version="{__version__}"}} 1\n'
    )
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4")
