"""A readiness probe that lies about its dependencies is worse than no probe."""

import asyncio

from telecom_mcp.observability.health import HealthChecker, Status
from tests.fakes import FrozenClock


def _checker(timeout_s: float = 2.0) -> HealthChecker:
    return HealthChecker(version="1.0.0", clock=FrozenClock(), timeout_s=timeout_s)


async def test_liveness_never_consults_a_dependency() -> None:
    checker = _checker()

    async def exploding_probe() -> None:
        raise RuntimeError("backend is down")

    checker.register("backend", exploding_probe)

    report = checker.liveness()
    assert report.status is Status.HEALTHY
    assert report.http_status == 200


async def test_readiness_is_unhealthy_when_a_required_dependency_fails() -> None:
    checker = _checker()

    async def failing() -> None:
        raise ConnectionError("refused")

    checker.register("backend", failing)

    report = await checker.readiness()
    assert report.status is Status.UNHEALTHY
    assert report.http_status == 503
    assert report.components[0].detail == "ConnectionError"


async def test_a_failing_optional_dependency_degrades_rather_than_removes_the_replica() -> None:
    checker = _checker()

    async def ok() -> None:
        return None

    async def failing() -> None:
        raise ConnectionError("refused")

    checker.register("backend", ok)
    checker.register("metrics_sink", failing, optional=True)

    report = await checker.readiness()
    assert report.status is Status.DEGRADED
    assert report.http_status == 200


async def test_a_slow_probe_counts_as_unready_rather_than_hanging_the_endpoint() -> None:
    checker = _checker(timeout_s=0.01)

    async def slow() -> None:
        await asyncio.sleep(5)

    checker.register("backend", slow)

    report = await checker.readiness()
    assert report.status is Status.UNHEALTHY
    assert "did not answer" in report.components[0].detail


async def test_a_probe_failure_message_cannot_carry_a_credential() -> None:
    checker = _checker()

    async def leaky() -> None:
        raise RuntimeError("connect failed with api_key=super-secret-value")

    checker.register("backend", leaky)

    report = await checker.readiness()
    assert "super-secret-value" not in str(report.to_dict())


async def test_all_healthy_reports_healthy_with_every_component_listed() -> None:
    checker = _checker()

    async def ok() -> None:
        return None

    checker.register("backend", ok)
    checker.register("idempotency_store", ok)

    report = await checker.readiness()
    assert report.status is Status.HEALTHY
    assert [c.name for c in report.components] == ["backend", "idempotency_store"]
    assert report.to_dict()["version"] == "1.0.0"
