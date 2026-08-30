"""One wired application, driven the way a real client drives it."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
from fastapi import FastAPI

from telecom_middleware.api.app import build_app
from telecom_middleware.api.container import build_context
from telecom_middleware.config.settings import load_settings
from telecom_middleware.repositories.memory import MemoryStore
from telecom_middleware.security.permissions import ROLE_SCOPES, Role, Scope

SECRET = "integration-signing-secret-long-enough"
AUDIENCE = "https://api.telecom.example/v1"
NAMESPACE = "https://telecom.example/"
TENANT = "tenant-eu-1"
CUSTOMER = "CX-1234"
OTHER_CUSTOMER = "CX-5555"

BASE_ENV = {
    "TELECOM_MW_LOCAL_VERIFIER_SECRET": SECRET,
    "TELECOM_MW_JWT_AUDIENCE": AUDIENCE,
    "TELECOM_MW_LOG_LEVEL": "ERROR",
    "TELECOM_MW_CHANGE_STREAM_ENABLED": "false",
}


class SequentialIds:
    def __init__(self, prefix: str = "id") -> None:
        self._prefix = prefix
        self._n = 0

    def new_id(self) -> str:
        self._n += 1
        return f"{self._prefix}-{self._n}"


class MovableClock:
    """Real-ish time that a test can move, so token expiry maths still lines up."""

    def __init__(self) -> None:
        self._now = datetime.now(UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


def make_token(
    *,
    role: Role = Role.CUSTOMER,
    subject: str | None = None,
    cx_id: str | None = CUSTOMER,
    tenant: str = TENANT,
    permissions: list[str] | None = None,
    expires_in_s: int = 600,
    is_service: bool = False,
) -> str:
    """Mint a token the local verifier accepts, shaped the way Auth0 shapes one."""
    scopes = permissions if permissions is not None else sorted(str(s) for s in ROLE_SCOPES[role])
    claims: dict[str, Any] = {
        "sub": subject or (f"auth0|{role}-1" if not is_service else "mcp@clients"),
        "aud": AUDIENCE,
        "iss": "https://tenant.example.invalid/",
        "exp": int((datetime.now(UTC) + timedelta(seconds=expires_in_s)).timestamp()),
        "jti": f"tok-{role}",
        "permissions": scopes,
        f"{NAMESPACE}tenant_id": tenant,
        f"{NAMESPACE}role": str(role),
    }
    if role is Role.CUSTOMER and cx_id:
        claims[f"{NAMESPACE}cx_id"] = cx_id
    if is_service:
        claims["gty"] = "client-credentials"
    return jwt.encode(claims, SECRET, algorithm="HS256")


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@dataclass
class Harness:
    app: FastAPI
    store: MemoryStore
    clock: MovableClock
    context: Any

    def headers(self, **kwargs: Any) -> dict[str, str]:
        return auth(make_token(**kwargs))


@pytest.fixture
async def harness(**_: Any) -> AsyncIterator[Harness]:
    settings = load_settings(BASE_ENV)
    store = MemoryStore()
    clock = MovableClock()
    context = build_context(
        settings, store=store, clock=clock, ids=SequentialIds("id"), configure_logs=False
    )
    app = build_app(context, start_realtime=False)
    async with app.router.lifespan_context(app):
        yield Harness(app=app, store=store, clock=clock, context=context)


@pytest.fixture
async def client(harness: Harness) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=harness.app), base_url="http://testserver"
    ) as http_client:
        yield http_client


@pytest.fixture
async def seeded(harness: Harness) -> Harness:
    """The demo dataset, so tests read real records rather than inventing them."""
    from telecom_middleware.services.seed import seed_demo_data

    await seed_demo_data(harness.store, tenant_id=TENANT, clock=harness.clock)
    return harness


CUSTOMER_SCOPES = sorted(str(s) for s in ROLE_SCOPES[Role.CUSTOMER])
SUPERVISOR_SCOPES = sorted(str(s) for s in ROLE_SCOPES[Role.SUPERVISOR_APPROVER])
ALL_SCOPES = sorted(str(s) for s in Scope)
