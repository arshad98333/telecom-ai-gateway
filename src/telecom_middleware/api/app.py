"""The ASGI application: routing, error translation, and the request lifecycle.

Three things happen here that are easy to get wrong elsewhere.

**Every error becomes a problem-details response**, with a correlation identifier and
nothing internal. An unhandled exception is caught at this outermost boundary, logged
with its stack, and answered with a generic 500 - the message is dropped, because an
unexpected exception is the most likely thing to carry something that must not be shown.

**Every response carries its correlation identifier**, so a customer saying "it failed
around three o'clock" is a two-minute investigation rather than a search.

**The realtime layer starts and stops with the application**, so a replica that is
shutting down stops feeding subscribers before it stops answering requests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from telecom_middleware._version import API_VERSION, __version__
from telecom_middleware.api.context import AppContext
from telecom_middleware.api.dependencies import CORRELATION_HEADER, get_correlation_id
from telecom_middleware.api.routes import (
    admin,
    approvals,
    cases,
    customers,
    health,
    stream,
    support,
)
from telecom_middleware.domain.errors import InvalidInputError, MiddlewareError
from telecom_middleware.observability.logging import get_logger, request_context
from telecom_middleware.realtime.broker import EventBroker
from telecom_middleware.realtime.relay import OutboxRelay

logger = get_logger(__name__)

API_PREFIX = f"/api/{API_VERSION}"


def build_app(context: AppContext, *, start_realtime: bool = True) -> FastAPI:
    """Assemble the application around an already-wired context."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await context.store.start()
        relay: OutboxRelay | None = None
        if start_realtime and context.settings.change_stream_enabled:
            broker = EventBroker(max_subscribers=context.settings.sse_max_subscribers)
            context.broker = broker
            broker.start(context.store.watch())
            relay = OutboxRelay(
                context.store,
                broker,
                batch_size=context.settings.outbox_batch_size,
                interval_s=context.settings.outbox_poll_interval_s,
            )
            relay.start()
        try:
            yield
        finally:
            # Stop feeding subscribers before the store goes, so nothing reads a closed
            # connection on the way down.
            if relay is not None:
                await relay.stop()
            if context.broker is not None:
                await context.broker.stop()
            await context.store.close()
        del app

    app = FastAPI(
        title="Telecom middleware",
        version=__version__,
        description=(
            "Customer data and the approval workflow. Every call is authenticated, "
            "authorized against the caller's own account, and audited."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.state.context = context

    app.include_router(health.router)
    for router in (
        customers.router,
        support.router,
        approvals.router,
        cases.router,
        admin.router,
        stream.router,
    ):
        app.include_router(router, prefix=API_PREFIX)

    _install_error_handlers(app)
    _install_middleware(app)
    return app


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(MiddlewareError)
    async def handle_domain_error(request: Request, exc: MiddlewareError) -> JSONResponse:
        correlation_id = get_correlation_id(request)
        # Logged at warning, not error: a refusal is the system working, and using
        # error for expected conditions makes the level meaningless.
        logger.warning(
            "request_refused",
            code=str(exc.code),
            status=exc.status,
            path=request.url.path,
            reason=str(exc),
        )
        return JSONResponse(
            status_code=exc.status,
            content=exc.problem(correlation_id).to_dict(),
            headers={CORRELATION_HEADER: correlation_id},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        correlation_id = get_correlation_id(request)
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(part) for part in first.get("loc", ())) or "<request>"
        # The field name, not the value: a validation error on a passcode must not
        # echo the passcode back in the response or into the logs.
        problem = InvalidInputError(detail={"field": field}).problem(correlation_id)
        logger.warning("request_invalid", field=field, path=request.url.path)
        return JSONResponse(
            status_code=problem.status,
            content=problem.to_dict(),
            headers={CORRELATION_HEADER: correlation_id},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        correlation_id = get_correlation_id(request)
        logger.exception("request_crashed", path=request.url.path)
        del exc
        return JSONResponse(
            status_code=500,
            content={
                "type": "https://telecom.example/problems/internal_error",
                "title": "An unexpected error occurred.",
                "status": 500,
                "code": "internal_error",
                "correlation_id": correlation_id,
            },
            headers={CORRELATION_HEADER: correlation_id},
        )


def _install_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def correlate(request: Request, call_next: Any) -> Any:
        correlation_id = get_correlation_id(request)
        with request_context(correlation_id):
            response = await call_next(request)
        response.headers[CORRELATION_HEADER] = correlation_id
        # Standard protective headers. This API returns JSON only, but a browser that
        # is pointed at it should still refuse to sniff or frame the response.
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        return response
