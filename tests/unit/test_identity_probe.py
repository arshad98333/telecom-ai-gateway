"""Readiness must say something true about the identity provider, or say nothing."""

from __future__ import annotations

from telecom_mcp.observability.health import Status
from tests.factory import build_test_application

JWKS_SETTINGS = {
    "TELECOM_MCP_IDENTITY_VERIFIER": "jwks",
    "TELECOM_MCP_JWKS_URL": "https://tenant.example/.well-known/jwks.json",
    "TELECOM_MCP_JWT_ISSUER": "https://tenant.example/",
    "TELECOM_MCP_JWT_AUDIENCE": "https://api.example/v1",
}


async def test_the_local_verifier_registers_no_identity_probe() -> None:
    harness = build_test_application()
    report = await harness.app.health.readiness()
    assert "identity_provider" not in {component.name for component in report.components}


async def test_the_jwks_verifier_registers_one() -> None:
    harness = build_test_application(**JWKS_SETTINGS)
    report = await harness.app.health.readiness()
    assert "identity_provider" in {component.name for component in report.components}


async def test_an_unreachable_tenant_makes_the_service_degraded_not_unready() -> None:
    harness = build_test_application(**JWKS_SETTINGS)
    report = await harness.app.health.readiness()

    component = next(c for c in report.components if c.name == "identity_provider")
    assert component.optional is True
    assert component.status is Status.UNHEALTHY
    # Degraded, so the replica keeps serving on its cached keys rather than being
    # pulled out of rotation over an outage it is built to ride out.
    assert report.status is Status.DEGRADED
    assert report.http_status == 200
