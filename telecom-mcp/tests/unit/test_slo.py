"""Objectives, and the arithmetic that turns them into a decision."""

from __future__ import annotations

import pytest

from telecom_mcp.observability.kpi import KPIS_BY_KEY, KpiReport, KpiValue
from telecom_mcp.observability.slo import (
    SLOS,
    SLOS_BY_KEY,
    Comparison,
    Severity,
    evaluate_slos,
)


def a_report(**values: tuple[float, int]) -> KpiReport:
    return KpiReport(
        tuple(KpiValue(KPIS_BY_KEY[key], value, sample) for key, (value, sample) in values.items())
    )


def full_report(**overrides: tuple[float, int]) -> KpiReport:
    defaults: dict[str, tuple[float, int]] = {
        "success_ratio": (1.0, 10_000),
        "latency_p95_seconds": (0.5, 10_000),
        "latency_p99_seconds": (1.0, 10_000),
        "calls_over_budget": (0.0, 10_000),
        "shed_ratio": (0.0, 10_000),
        "backend_retry_ratio": (0.0, 10_000),
        "output_guardrail_blocks": (0.0, 10_000),
    }
    defaults.update(overrides)
    return a_report(**defaults)


@pytest.mark.parametrize("slo", SLOS, ids=lambda slo: slo.kpi_key)
def test_every_objective_names_a_real_kpi_and_says_why(slo: object) -> None:
    assert slo.kpi is not None  # type: ignore[attr-defined]
    assert len(slo.rationale) > 40  # type: ignore[attr-defined]


def test_a_healthy_service_meets_everything() -> None:
    assert all(status.met for status in evaluate_slos(full_report()))


def test_a_breach_is_reported_against_the_right_objective() -> None:
    statuses = {s.slo.kpi_key: s for s in evaluate_slos(full_report(success_ratio=(0.98, 5_000)))}
    assert statuses["success_ratio"].met is False
    assert statuses["latency_p95_seconds"].met is True


def test_too_little_traffic_is_unknown_rather_than_met() -> None:
    statuses = {s.slo.kpi_key: s for s in evaluate_slos(full_report(success_ratio=(0.0, 3)))}
    assert statuses["success_ratio"].met is None
    assert statuses["success_ratio"].budget_remaining is None


def test_an_untouched_budget_is_one() -> None:
    statuses = {s.slo.kpi_key: s for s in evaluate_slos(full_report())}
    assert statuses["success_ratio"].budget_remaining == 1.0


def test_half_the_tolerated_failures_spends_half_the_budget() -> None:
    # The objective is 0.995, so 0.5% is the whole allowance and 0.25% is half of it.
    statuses = {
        s.slo.kpi_key: s for s in evaluate_slos(full_report(success_ratio=(0.9975, 10_000)))
    }
    assert round(statuses["success_ratio"].budget_remaining or 0, 3) == 0.5


def test_overspending_goes_negative_rather_than_clamping() -> None:
    statuses = {s.slo.kpi_key: s for s in evaluate_slos(full_report(success_ratio=(0.98, 10_000)))}
    assert (statuses["success_ratio"].budget_remaining or 0) < 0


def test_a_latency_threshold_has_no_budget_because_it_is_not_an_allowance() -> None:
    statuses = {s.slo.kpi_key: s for s in evaluate_slos(full_report())}
    assert statuses["latency_p95_seconds"].budget_remaining is None


def test_the_output_guardrail_objective_pages_and_tolerates_nothing() -> None:
    slo = SLOS_BY_KEY["output_guardrail_blocks"]
    assert slo.objective == 0.0
    assert slo.severity is Severity.PAGE
    assert slo.comparison is Comparison.AT_MOST


def test_a_status_serializes_with_the_reason_it_exists() -> None:
    payload = evaluate_slos(full_report())[0].to_dict()
    assert set(payload) >= {"kpi", "objective", "actual", "met", "severity", "rationale"}
