"""The composition root chooses implementations from configuration, and only there."""

import pytest

from telecom_mcp.adapters.fake_backend import FakeTelecomBackend
from telecom_mcp.adapters.http_backend import HttpTelecomBackend
from telecom_mcp.adapters.idempotency import MemoryIdempotencyStore, RedisIdempotencyStore
from telecom_mcp.api.container import Application, build_application
from telecom_mcp.security.verifier import JwksVerifier, LocalVerifier
from tests.factory import settings
from tests.fakes import FrozenClock, NoJitter, SequentialIds

PRODUCTION = {
    "TELECOM_MCP_ENV": "production",
    "TELECOM_MCP_BACKEND": "http",
    "TELECOM_MCP_BACKEND_BASE_URL": "https://middleware.example.invalid/api/v1",
    "TELECOM_MCP_BACKEND_API_KEY": "dummy-key",
    "TELECOM_MCP_IDENTITY_VERIFIER": "jwks",
    "TELECOM_MCP_JWKS_URL": "https://tenant.example.invalid/.well-known/jwks.json",
    "TELECOM_MCP_JWT_ISSUER": "https://tenant.example.invalid/",
    "TELECOM_MCP_IDEMPOTENCY_STORE": "redis",
    "TELECOM_MCP_REDIS_URL": "redis://localhost:6379/0",
}


def build(**overrides: str) -> Application:
    return build_application(
        settings(**overrides),
        clock=FrozenClock(),
        id_generator=SequentialIds("id"),
        jitter=NoJitter(),
    )


def test_the_local_defaults_wire_the_fake_backend_and_the_memory_store() -> None:
    app = build()

    assert isinstance(app.backend, FakeTelecomBackend)
    assert isinstance(app.idempotency, MemoryIdempotencyStore)
    assert isinstance(app.executor.authorizer.verifier, LocalVerifier)


async def test_the_production_settings_wire_the_real_adapters() -> None:
    pytest.importorskip("redis")
    app = build(**PRODUCTION)

    try:
        assert isinstance(app.backend, HttpTelecomBackend)
        assert isinstance(app.idempotency, RedisIdempotencyStore)
        assert isinstance(app.executor.authorizer.verifier, JwksVerifier)
    finally:
        await app.aclose()


def test_health_registers_a_probe_for_every_dependency() -> None:
    app = build()

    assert [probe.name for probe in app.health._probes] == [
        "telecom_middleware",
        "idempotency_store",
    ]


async def test_closing_the_application_closes_the_http_client() -> None:
    pytest.importorskip("redis")
    app = build(**PRODUCTION)

    await app.aclose()

    assert isinstance(app.backend, HttpTelecomBackend)


async def test_closing_an_application_without_a_closable_backend_is_harmless() -> None:
    app = build()

    await app.aclose()
