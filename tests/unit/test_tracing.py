"""Tracing is off by default, free when off, and loud when it cannot do what it says."""

from __future__ import annotations

import pytest

from telecom_mcp.domain.errors import ConfigurationError
from telecom_mcp.observability.tracing import (
    ALLOWED_SPAN_ATTRIBUTES,
    Exporter,
    NullTracer,
    OtelSpan,
    TracingConfig,
    build_tracer,
)


def test_the_default_configuration_produces_a_no_op_tracer() -> None:
    assert isinstance(build_tracer(TracingConfig()), NullTracer)


def test_an_enabled_configuration_with_no_exporter_is_still_a_no_op() -> None:
    config = TracingConfig(enabled=True, exporter=Exporter.NONE)
    assert isinstance(build_tracer(config), NullTracer)


def test_the_no_op_span_accepts_everything_and_records_nothing() -> None:
    tracer = NullTracer()
    with tracer.span("execute_tool", tool="get_invoice_summary") as span:
        span.set_attribute("outcome", "ok")
        span.record_failure("backend_timeout")


def test_the_no_op_tracer_is_a_context_manager_that_always_exits() -> None:
    tracer = NullTracer()
    with pytest.raises(RuntimeError), tracer.span("execute_tool"):
        raise RuntimeError("boom")


def test_otlp_without_an_endpoint_fails_at_startup() -> None:
    config = TracingConfig(enabled=True, exporter=Exporter.OTLP)
    with pytest.raises(ConfigurationError, match="endpoint"):
        build_tracer(config)


def test_an_attribute_outside_the_allow_list_is_refused() -> None:
    class Recorder:
        def __init__(self) -> None:
            self.attributes: dict[str, object] = {}

        def set_attribute(self, key: str, value: object) -> None:
            self.attributes[key] = value

    span = OtelSpan(Recorder())
    span.set_attribute("tool", "get_invoice_summary")
    with pytest.raises(ValueError, match="not allowed"):
        span.set_attribute("cx_id", "CX-1001")


def test_no_identifying_attribute_is_on_the_allow_list() -> None:
    forbidden = {"cx_id", "subject", "token", "msisdn", "email", "arguments", "amount"}
    assert not forbidden & ALLOWED_SPAN_ATTRIBUTES


def test_the_correlation_and_case_identifiers_are_allowed_because_a_trace_needs_them() -> None:
    assert {"correlation_id", "case_id"} <= ALLOWED_SPAN_ATTRIBUTES


def test_azure_monitor_without_a_connection_string_fails_at_startup() -> None:
    config = TracingConfig(enabled=True, exporter=Exporter.AZURE_MONITOR)
    with pytest.raises(ConfigurationError, match="connection string"):
        build_tracer(config)


def test_the_error_says_where_the_connection_string_comes_from() -> None:
    config = TracingConfig(enabled=True, exporter=Exporter.AZURE_MONITOR)
    with pytest.raises(ConfigurationError) as caught:
        build_tracer(config)
    assert "Key Vault" in str(caught.value)
    assert "APPLICATIONINSIGHTS_CONNECTION_STRING" in str(caught.value)


def test_the_description_never_reveals_the_connection_string() -> None:
    config = TracingConfig(
        enabled=True,
        exporter=Exporter.AZURE_MONITOR,
        connection_string="InstrumentationKey=00000000-1111-2222-3333-444444444444",
    )
    described = config.describe()
    assert described["connection_string"] == "***present***"
    assert "InstrumentationKey" not in str(described)


def test_the_description_reports_absence_as_absence() -> None:
    assert TracingConfig().describe()["connection_string"] is None
