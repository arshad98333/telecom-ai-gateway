"""The package must be usable without the tracing extras installed."""

from __future__ import annotations

import tomllib
from pathlib import Path

from telecom_mcp.observability.tracing import NullTracer, TracingConfig, build_tracer

PYPROJECT = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))


def test_no_opentelemetry_package_is_a_hard_dependency() -> None:
    required = " ".join(PYPROJECT["project"]["dependencies"])
    assert "opentelemetry" not in required
    assert "azure-monitor" not in required


def test_both_telemetry_extras_are_declared() -> None:
    extras = PYPROJECT["project"]["optional-dependencies"]
    assert "otel" in extras
    assert "azure-monitor" in extras


def test_tracing_is_importable_and_usable_with_no_extra_installed() -> None:
    # This module is imported by the executor on every run, so it must never reach for
    # a vendor package at import time.
    tracer = build_tracer(TracingConfig())
    assert isinstance(tracer, NullTracer)
    with tracer.span("execute_tool", tool="get_invoice_summary") as span:
        span.set_attribute("outcome", "ok")
