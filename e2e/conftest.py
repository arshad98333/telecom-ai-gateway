"""One process, both services, and a real HTTP conversation between them.

The tool server talks to the middleware over HTTP through an in-process ASGI transport.
Nothing is stubbed between them: the MCP kernel authorizes, the HTTP adapter serialises
and sends, the middleware authenticates the same token again, MongoDB's in-memory stand-in
stores, and the events come back out. If the two contracts disagree, this fails.

One token is minted with both audiences, which is exactly what a real deployment does:
Auth0 issues an access token for the middleware API, and the tool server verifies the
same token before it forwards it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest

SECRET = "end-to-end-signing-secret-long-enough"
MCP_AUDIENCE = "telecom-mcp-tools"
API_AUDIENCE = "https://api.telecom.example/v1"
NAMESPACE = "https://telecom.example/"
TENANT = "tenant-eu-1"
CUSTOMER = "CX-1234"
OTHER_CUSTOMER = "CX-5555"

CUSTOMER_PERMISSIONS = [
    "account:read",
    "service:read",
    "order:read",
    "billing:read",
    "network:read",
    "ticket:read",
    "ticket:write",
    "callback:write",
    "refund:request",
    "case:read",
]
SUPERVISOR_PERMISSIONS = [*CUSTOMER_PERMISSIONS, "refund:approve", "case:write",
                         "assignment:read", "assignment:write"]


def make_token(
    *,
    role: str = "customer",
    subject: str | None = None,
    cx_id: str | None = CUSTOMER,
    permissions: list[str] | None = None,
    tenant: str = TENANT,
) -> str:
    """A token both services accept, shaped the way Auth0 shapes one."""
    claims: dict[str, Any] = {
        "sub": subject or f"auth0|{role}-1",
        # Two audiences in one token: the tool server verifies it, then forwards it to
        # the API it was also minted for. This is the real arrangement, not a shortcut.
        "aud": [MCP_AUDIENCE, API_AUDIENCE],
        "exp": int((datetime.now(UTC) + timedelta(minutes=10)).timestamp()),
        "jti": f"tok-{role}",
        "permissions": permissions or CUSTOMER_PERMISSIONS,
        "scope": " ".join(permissions or CUSTOMER_PERMISSIONS),
        f"{NAMESPACE}tenant_id": tenant,
        f"{NAMESPACE}role": role,
        f"{NAMESPACE}cx_id": cx_id or "",
        # The MCP kernel reads these two claim names; the middleware reads the
        # namespaced ones above. Both are present, as they would be in production.
        "https://telecom.example/tenant_id": tenant,
    }
    if role == "customer" and cx_id:
        claims[f"{NAMESPACE}cx_id"] = cx_id
    else:
        claims.pop(f"{NAMESPACE}cx_id", None)
    return jwt.encode(claims, SECRET, algorithm="HS256")


class SequentialIds:
    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._n = 0

    def new_id(self) -> str:
        self._n += 1
        return f"{self._prefix}-{self._n}"


class Clock:
    def __init__(self) -> None:
        self._now = datetime.now(UTC)

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._now.timestamp()

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


@dataclass
class System:
    """Both services, wired together, plus the handles a test needs."""

    mcp_server: Any
    middleware_app: Any
    store: Any
    broker: Any
    clock: Clock
    token_holder: dict[str, str]

    def act_as(self, token: str) -> None:
        """Point the tool server's stdio token source at a different identity."""
        self.token_holder["token"] = token


@pytest.fixture
async def system() -> AsyncIterator[System]:
    from telecom_mcp.adapters.http_backend import HttpTelecomBackend
    from telecom_mcp.api.container import build_application
    from telecom_mcp.api.server import TelecomMCPServer
    from telecom_mcp.config.settings import load_settings as load_mcp_settings
    from telecom_middleware.api.app import build_app
    from telecom_middleware.api.container import build_context
    from telecom_middleware.config.settings import load_settings as load_mw_settings
    from telecom_middleware.realtime.broker import EventBroker
    from telecom_middleware.repositories.memory import MemoryStore
    from telecom_middleware.services.seed import seed_demo_data

    clock = Clock()
    store = MemoryStore()

    middleware_context = build_context(
        load_mw_settings(
            {
                "TELECOM_MW_LOCAL_VERIFIER_SECRET": SECRET,
                "TELECOM_MW_JWT_AUDIENCE": API_AUDIENCE,
                "TELECOM_MW_LOG_LEVEL": "ERROR",
                "TELECOM_MW_CHANGE_STREAM_ENABLED": "false",
            }
        ),
        store=store,
        clock=clock,
        ids=SequentialIds("mw"),
        configure_logs=False,
    )
    middleware_app = build_app(middleware_context, start_realtime=False)
    broker = EventBroker()
    middleware_context.broker = broker
    # Fed from the store's own change feed, which is what the deployed service does; the
    # test only starts it by hand because the app's lifespan is running without realtime.
    broker.start(store.watch())

    async with middleware_app.router.lifespan_context(middleware_app):
        await seed_demo_data(store, tenant_id=TENANT, clock=clock)

        http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=middleware_app),
            base_url="http://middleware/api/v1",
            headers={"X-Service-Authorization": "Bearer service-credential"},
        )
        backend = HttpTelecomBackend(http)

        mcp_application = build_application(
            load_mcp_settings(
                {
                    "TELECOM_MCP_LOCAL_VERIFIER_SECRET": SECRET,
                    "TELECOM_MCP_JWT_AUDIENCE": MCP_AUDIENCE,
                    "TELECOM_MCP_LOG_LEVEL": "ERROR",
                    "TELECOM_MCP_RETRY_BASE_DELAY_S": "0.001",
                }
            ),
            backend=backend,
        )

        holder: dict[str, str] = {"token": make_token()}

        class HeldToken:
            def current_token(self) -> str:
                return holder["token"]

        server = TelecomMCPServer(
            mcp_application, tokens=HeldToken(), id_generator=SequentialIds("corr")
        )

        yield System(
            mcp_server=server,
            middleware_app=middleware_app,
            store=store,
            broker=broker,
            clock=clock,
            token_holder=holder,
        )

        await http.aclose()
        await broker.stop()
