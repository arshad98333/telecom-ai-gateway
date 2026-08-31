"""Distributed tracing, behind an interface this package owns.

Two things are deliberate here.

*The dependency is optional and late.* OpenTelemetry is imported inside the factory,
never at module import time. A developer running the fake backend on a laptop should
not need a collector, an exporter or a hundred megabytes of packages, and the test
suite should not depend on the version of a vendor SDK. If tracing is asked for and the
extra is not installed, that is a configuration error at startup with a message that
says which extra to install - not an ImportError halfway through the first request.

*Nothing outside this module knows what OpenTelemetry is.* Callers see ``Tracer`` and
``Span``, both defined here. That is what makes the no-op version free rather than a
mock, and it is what will make replacing the vendor a one-file change rather than a
grep across the codebase.

Attributes are held to the same standard as metric labels: a span may carry the tool
name, the correlation id and the case id, and it may not carry a customer reference, an
argument value or a token. A trace backend is a third-party system with its own
retention, and "we only sent it to the tracing vendor" is not a defence anybody has
ever successfully made.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Protocol

from telecom_mcp.domain.errors import ConfigurationError

#: Attributes a span is allowed to carry. Same reasoning as ALLOWED_LABELS in metrics,
#: with room for the two identifiers that make a trace worth having.
ALLOWED_SPAN_ATTRIBUTES: Final[frozenset[str]] = frozenset(
    {
        "tool",
        "outcome",
        "code",
        "stage",
        "rule",
        "backend",
        "attempt",
        "deduplicated",
        "correlation_id",
        "case_id",
        "contract_version",
    }
)


class Exporter(StrEnum):
    NONE = "none"
    OTLP = "otlp"
    AZURE_MONITOR = "azure_monitor"


@dataclass(frozen=True, slots=True)
class TracingConfig:
    """Everything the factory needs, and nothing that is a secret except one."""

    enabled: bool = False
    exporter: Exporter = Exporter.NONE
    service_name: str = "telecom-mcp-tools"
    service_version: str = "0.0.0"
    environment: str = "local"
    #: OTLP endpoint, when the exporter is otlp.
    endpoint: str | None = None
    #: Application Insights connection string, when the exporter is azure_monitor. It
    #: contains the instrumentation key, so it is a secret and is never described,
    #: logged or put in a span.
    connection_string: str | None = None
    #: Head sampling ratio. 1.0 traces everything, which is right until it is not.
    sample_ratio: float = 1.0

    def describe(self) -> dict[str, object]:
        """A loggable view. The connection string is reported as present or absent."""
        return {
            "enabled": self.enabled,
            "exporter": str(self.exporter),
            "service_name": self.service_name,
            "service_version": self.service_version,
            "environment": self.environment,
            "endpoint": self.endpoint,
            "connection_string": "***present***" if self.connection_string else None,
            "sample_ratio": self.sample_ratio,
        }


class Span(Protocol):
    """The part of a span this codebase uses."""

    def set_attribute(self, key: str, value: str | int | float | bool) -> None: ...

    def record_failure(self, code: str) -> None: ...


class Tracer(Protocol):
    """Starts spans. The context manager always ends the span, including on error."""

    def span(self, name: str, **attributes: str | int | float | bool) -> Any: ...


# --- The no-op implementation -------------------------------------------------------


class _NullSpan:
    """Free. Not a mock: there is no recording, so nothing accumulates."""

    __slots__ = ()

    def set_attribute(self, key: str, value: str | int | float | bool) -> None:
        return None

    def record_failure(self, code: str) -> None:
        return None


_NULL_SPAN: Final = _NullSpan()


class NullTracer:
    """What runs when tracing is off, which is the default and most of the time."""

    __slots__ = ()

    @contextmanager
    def span(self, name: str, **attributes: str | int | float | bool) -> Iterator[Span]:
        del name, attributes
        yield _NULL_SPAN


# --- The real implementation --------------------------------------------------------


class OtelSpan:
    """Adapts an OpenTelemetry span to the two methods this codebase needs."""

    __slots__ = ("_span",)

    def __init__(self, span: Any) -> None:
        self._span = span

    def set_attribute(self, key: str, value: str | int | float | bool) -> None:
        if key not in ALLOWED_SPAN_ATTRIBUTES:
            raise ValueError(
                f"span attribute {key!r} is not allowed; permitted attributes are "
                f"{sorted(ALLOWED_SPAN_ATTRIBUTES)}. A trace backend is a third-party "
                "system and customer data does not go into one."
            )
        self._span.set_attribute(key, value)

    def record_failure(self, code: str) -> None:
        """Mark the span failed by code, without the exception message.

        The message is dropped for the same reason the error envelope drops it: an
        unexpected exception is the most likely thing to be carrying something that
        must not leave the process.
        """
        from opentelemetry.trace import Status, StatusCode

        self._span.set_status(Status(StatusCode.ERROR, code))
        self._span.set_attribute("code", code)


class OtelTracer:
    """A thin wrapper. All the configuration happened in the factory."""

    __slots__ = ("_tracer",)

    def __init__(self, tracer: Any) -> None:
        self._tracer = tracer

    @contextmanager
    def span(self, name: str, **attributes: str | int | float | bool) -> Iterator[Span]:
        with self._tracer.start_as_current_span(name) as raw:
            wrapped = OtelSpan(raw)
            for key, value in attributes.items():
                wrapped.set_attribute(key, value)
            yield wrapped


def build_tracer(config: TracingConfig) -> Tracer:
    """Return a configured tracer, or the no-op one.

    Raises:
        ConfigurationError: when tracing is asked for and cannot be provided. Failing
            at startup is the point: a deployment that believes it is being traced and
            is not is worse than one that knows it is not.
    """
    if not config.enabled or config.exporter is Exporter.NONE:
        return NullTracer()

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
    except ImportError as exc:  # pragma: no cover - exercised by the packaging tests
        raise ConfigurationError(
            "tracing is enabled but the OpenTelemetry SDK is not installed; "
            "install telecom-mcp-tools[otel]",
            operation="build_tracer",
        ) from exc

    resource = Resource.create(
        {
            "service.name": config.service_name,
            "service.version": config.service_version,
            "deployment.environment": config.environment,
        }
    )
    provider = TracerProvider(
        resource=resource,
        # Parent-based, so a sampled incoming request stays sampled all the way down.
        # Sampling each service independently produces traces with holes in them,
        # which are worse than no traces because they look complete.
        sampler=ParentBased(TraceIdRatioBased(config.sample_ratio)),
    )
    provider.add_span_processor(BatchSpanProcessor(_build_exporter(config)))
    trace.set_tracer_provider(provider)
    return OtelTracer(trace.get_tracer(config.service_name, config.service_version))


def _build_exporter(config: TracingConfig) -> Any:
    if config.exporter is Exporter.OTLP:
        if not config.endpoint:
            raise ConfigurationError(
                "exporter=otlp requires an endpoint", operation="build_tracer"
            )
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        except ImportError as exc:  # pragma: no cover
            raise ConfigurationError(
                "the OTLP exporter is not installed; install telecom-mcp-tools[otel]",
                operation="build_tracer",
            ) from exc
        return OTLPSpanExporter(endpoint=config.endpoint)

    if config.exporter is Exporter.AZURE_MONITOR:
        if not config.connection_string:
            raise ConfigurationError(
                "exporter=azure_monitor requires an Application Insights connection "
                "string; it is read from Key Vault in a deployment and from "
                "APPLICATIONINSIGHTS_CONNECTION_STRING locally",
                operation="build_tracer",
            )
        try:
            from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
        except ImportError as exc:  # pragma: no cover
            raise ConfigurationError(
                "the Azure Monitor exporter is not installed; "
                "install telecom-mcp-tools[azure-monitor]",
                operation="build_tracer",
            ) from exc
        # The connection string carries the instrumentation key, which is why it is a
        # SecretStr everywhere above this line and is never logged or described.
        return AzureMonitorTraceExporter(connection_string=config.connection_string)

    raise ConfigurationError(
        f"unsupported trace exporter {config.exporter}", operation="build_tracer"
    )
