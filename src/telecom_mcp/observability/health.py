"""Liveness and readiness, kept honestly separate.

Liveness answers "is this process alive", and is used to decide whether to restart
the container. It must not depend on anything external, or one backend blip restarts
every replica at once.

Readiness answers "can this process actually serve", and is used to decide whether to
send it traffic. It checks the dependencies that a request genuinely needs. A
readiness probe that returns healthy while its dependencies are dead is worse than no
probe, because it hides the outage.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from telecom_mcp.domain.ports import Clock

#: A readiness probe must answer fast; a slow dependency is an unready one.
PROBE_TIMEOUT_S = 2.0


class Status(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True, slots=True)
class ComponentHealth:
    name: str
    status: Status
    detail: str = ""
    duration_ms: float = 0.0
    #: A component the service can serve without, in a reduced form.
    optional: bool = False


@dataclass(frozen=True, slots=True)
class HealthReport:
    status: Status
    version: str
    components: tuple[ComponentHealth, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": str(self.status),
            "version": self.version,
            "components": [
                {
                    "name": component.name,
                    "status": str(component.status),
                    "detail": component.detail,
                    "duration_ms": round(component.duration_ms, 2),
                    "optional": component.optional,
                }
                for component in self.components
            ],
        }

    @property
    def http_status(self) -> int:
        return 200 if self.status is not Status.UNHEALTHY else 503


Probe = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class _RegisteredProbe:
    name: str
    probe: Probe
    optional: bool


class HealthChecker:
    """Runs the readiness probes. Liveness needs no probes by design."""

    def __init__(self, *, version: str, clock: Clock, timeout_s: float = PROBE_TIMEOUT_S) -> None:
        self._version = version
        self._clock = clock
        self._timeout_s = timeout_s
        self._probes: list[_RegisteredProbe] = []

    def register(self, name: str, probe: Probe, *, optional: bool = False) -> None:
        self._probes.append(_RegisteredProbe(name=name, probe=probe, optional=optional))

    def liveness(self) -> HealthReport:
        """The process is running and can execute code. Nothing external is consulted."""
        return HealthReport(status=Status.HEALTHY, version=self._version, components=())

    async def readiness(self) -> HealthReport:
        results = await asyncio.gather(*(self._run(p) for p in self._probes))
        required_failed = any(c.status is Status.UNHEALTHY and not c.optional for c in results)
        optional_failed = any(c.status is Status.UNHEALTHY and c.optional for c in results)
        status = (
            Status.UNHEALTHY
            if required_failed
            else Status.DEGRADED
            if optional_failed
            else Status.HEALTHY
        )
        return HealthReport(status=status, version=self._version, components=tuple(results))

    async def _run(self, registered: _RegisteredProbe) -> ComponentHealth:
        started = self._clock.monotonic()
        try:
            await asyncio.wait_for(registered.probe(), timeout=self._timeout_s)
        except TimeoutError:
            return ComponentHealth(
                name=registered.name,
                status=Status.UNHEALTHY,
                detail=f"probe did not answer within {self._timeout_s:g}s",
                duration_ms=(self._clock.monotonic() - started) * 1000,
                optional=registered.optional,
            )
        except Exception as exc:  # noqa: BLE001 - a probe may raise anything; none of it is fatal
            # The type name is enough to act on and cannot carry a credential.
            return ComponentHealth(
                name=registered.name,
                status=Status.UNHEALTHY,
                detail=type(exc).__name__,
                duration_ms=(self._clock.monotonic() - started) * 1000,
                optional=registered.optional,
            )
        return ComponentHealth(
            name=registered.name,
            status=Status.HEALTHY,
            duration_ms=(self._clock.monotonic() - started) * 1000,
            optional=registered.optional,
        )
