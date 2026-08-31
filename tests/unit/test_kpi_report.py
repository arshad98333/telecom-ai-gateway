"""The report is a pure function of a snapshot, so it can be checked exactly."""

from __future__ import annotations

from telecom_mcp.observability.kpi import build_kpi_report
from telecom_mcp.observability.metrics import Metrics


def a_registry() -> Metrics:
    metrics = Metrics(buckets=(0.1, 0.5, 1.0, 10.0))
    for _ in range(6):
        metrics.increment("tool_calls_total", tool="get_invoice_summary", outcome="ok")
    metrics.increment("tool_calls_total", tool="get_invoice_summary", outcome="failed", code="x")
    metrics.increment("tool_calls_total", tool="get_invoice_summary", outcome="denied", code="y")
    metrics.increment("tool_calls_total", tool="create_support_ticket", outcome="ok")
    metrics.increment("tool_calls_total", tool="create_support_ticket", outcome="deduplicated")
    metrics.increment("tool_calls_total", tool="create_support_ticket", outcome="guardrail_blocked")
    metrics.increment("guardrail_decisions_total", tool="create_support_ticket", stage="injection", outcome="blocked")
    for value in (0.05, 0.2, 0.7, 30.0):
        metrics.observe("tool_duration_seconds", value, tool="get_invoice_summary")
    metrics.increment("backend_attempts_total", tool="get_invoice_summary", stage="1", value=8)
    metrics.increment("backend_attempts_total", tool="get_invoice_summary", stage="2", value=2)
    return metrics


def test_the_denominator_counts_each_call_once() -> None:
    report = build_kpi_report(a_registry())
    assert report.by_key("tool_calls").value == 11


def test_deduplicated_calls_count_as_success() -> None:
    report = build_kpi_report(a_registry())
    assert round(report.by_key("success_ratio").value, 4) == round(8 / 11, 4)


def test_a_denial_is_not_counted_as_a_failure() -> None:
    report = build_kpi_report(a_registry())
    assert round(report.by_key("failure_ratio").value, 4) == round(1 / 11, 4)
    assert round(report.by_key("authorization_denial_ratio").value, 4) == round(1 / 11, 4)


def test_latency_and_the_over_budget_count_come_from_the_histogram() -> None:
    report = build_kpi_report(a_registry())
    assert report.by_key("latency_p95_seconds").value == 10.0
    assert report.by_key("calls_over_budget").value == 1.0


def test_the_retry_ratio_ignores_first_attempts() -> None:
    report = build_kpi_report(a_registry())
    assert round(report.by_key("backend_retry_ratio").value, 4) == 0.2


def test_guardrail_blocks_are_reported_with_a_breakdown_by_stage() -> None:
    value = build_kpi_report(a_registry()).by_key("guardrail_block_ratio")
    assert value.breakdown == {"injection": 1.0}


def test_the_output_guardrail_indicator_is_zero_when_nothing_leaked() -> None:
    assert build_kpi_report(a_registry()).by_key("output_guardrail_blocks").value == 0.0


def test_business_counts_exclude_refusals_and_split_out_replays() -> None:
    value = build_kpi_report(a_registry()).by_key("tickets_created")
    assert value.value == 1.0
    assert value.breakdown == {"created": 1.0, "deduplicated": 1.0}


def test_an_empty_registry_reports_zeroes_with_a_zero_sample_size() -> None:
    report = build_kpi_report(Metrics())
    assert report.by_key("success_ratio").value == 0.0
    assert report.by_key("success_ratio").sample_size == 0
    assert report.by_key("latency_p95_seconds").value == 0.0


def test_the_report_serializes_with_everything_a_reader_needs() -> None:
    payload = build_kpi_report(a_registry()).to_dict()
    first = payload["kpis"][0]  # type: ignore[index]
    assert set(first) >= {"key", "family", "title", "unit", "direction", "value", "question"}
    assert set(payload["families"]) == {"service", "safety", "business"}  # type: ignore[arg-type]
