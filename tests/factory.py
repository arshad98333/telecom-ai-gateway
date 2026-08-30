"""One place to build a fully wired application from fakes, for tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from telecom_mcp.adapters.fake_backend import FailureInjection, FakeTelecomBackend
from telecom_mcp.api.container import Application, build_application
from telecom_mcp.config.settings import Settings, load_settings
from telecom_mcp.security.audit import MemorySink
from telecom_mcp.security.authorization import OwnershipChecker
from telecom_mcp.security.verifier import CX_CLAIM, ROLE_CLAIM, TENANT_CLAIM
from tests.fakes import FrozenClock, NoJitter, SequentialIds

SECRET = "test-signing-secret-long-enough-for-hs256"
AUDIENCE = "telecom-mcp-tools"
TENANT = "tenant-eu-1"
CUSTOMER = "CX-1234"

BASE_ENV = {
    "TELECOM_MCP_LOCAL_VERIFIER_SECRET": SECRET,
    "TELECOM_MCP_JWT_AUDIENCE": AUDIENCE,
    "TELECOM_MCP_RETRY_BASE_DELAY_S": "0.001",
    "TELECOM_MCP_LOG_LEVEL": "ERROR",
}


def settings(**overrides: str) -> Settings:
    return load_settings(dict(BASE_ENV, **overrides))


def make_token(
    *,
    cx_id: str = CUSTOMER,
    tenant: str = TENANT,
    role: str = "customer",
    scope: str = (
        "account:read service:read order:read billing:read network:read "
        "ticket:write callback:write refund:request"
    ),
    expires_in_s: int = 600,
) -> str:
    claims: dict[str, Any] = {
        "sub": cx_id,
        CX_CLAIM: cx_id,
        TENANT_CLAIM: tenant,
        ROLE_CLAIM: role,
        "scope": scope,
        "aud": AUDIENCE,
        "exp": int((datetime.now(UTC) + timedelta(seconds=expires_in_s)).timestamp()),
        "jti": f"tok-{cx_id}",
    }
    return jwt.encode(claims, SECRET, algorithm="HS256")


class TestApplication:
    """An application plus the fakes a test needs to reach into."""

    def __init__(
        self,
        app: Application,
        backend: FakeTelecomBackend,
        audit: MemorySink,
        clock: FrozenClock,
    ) -> None:
        self.app = app
        self.backend = backend
        self.audit = audit
        self.clock = clock

    @property
    def executor(self) -> Any:
        return self.app.executor


def build_test_application(
    *,
    failures: FailureInjection | None = None,
    ownership: OwnershipChecker | None = None,
    **setting_overrides: str,
) -> TestApplication:
    clock = FrozenClock()
    backend = FakeTelecomBackend(clock=clock, id_generator=SequentialIds("t"), failures=failures)
    audit = MemorySink()
    app = build_application(
        settings(**setting_overrides),
        clock=clock,
        id_generator=SequentialIds("id"),
        jitter=NoJitter(),
        ownership=ownership,
        backend=backend,
        audit_sink=audit,
    )
    return TestApplication(app, backend, audit, clock)
