"""One tracer per process, chosen from settings, shared by whoever needs it."""

from __future__ import annotations

from telecom_mcp.observability.tracing import NullTracer
from tests.factory import build_test_application


def test_tracing_off_gives_the_no_op_tracer() -> None:
    harness = build_test_application()
    assert isinstance(harness.app.tracer, NullTracer)


def test_the_executor_uses_the_application_tracer_rather_than_its_own() -> None:
    harness = build_test_application()
    assert harness.executor._tracer is harness.app.tracer  # noqa: SLF001


def test_the_tracing_configuration_describes_itself_without_the_secret() -> None:
    harness = build_test_application()
    described = harness.app.settings.tracing_config().describe()
    assert described["enabled"] is False
    assert described["connection_string"] is None


def test_a_misconfigured_exporter_fails_when_the_application_is_built() -> None:
    # Settings validation catches the missing endpoint first, which is the point: the
    # failure arrives at startup rather than on the first traced request.
    import pytest

    from telecom_mcp.domain.errors import ConfigurationError

    with pytest.raises(ConfigurationError):
        build_test_application(
            TELECOM_MCP_TRACING_ENABLED="true", TELECOM_MCP_TRACE_EXPORTER="otlp"
        )
