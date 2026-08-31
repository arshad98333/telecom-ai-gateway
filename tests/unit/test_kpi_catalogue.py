"""The catalogue is a contract with whoever reads the dashboard. It must be complete."""

from __future__ import annotations

import pytest

from telecom_mcp.observability.kpi import KPIS, KPIS_BY_KEY, Direction, KpiFamily, kpis_for

KNOWN_SERIES = {
    "tool_calls_total",
    "tool_duration_seconds",
    "backend_attempts_total",
    "guardrail_decisions_total",
}


def test_every_key_is_unique() -> None:
    assert len(KPIS_BY_KEY) == len(KPIS)


@pytest.mark.parametrize("kpi", KPIS, ids=lambda kpi: kpi.key)
def test_every_kpi_derives_only_from_series_the_service_writes(kpi: object) -> None:
    assert set(kpi.derived_from) <= KNOWN_SERIES  # type: ignore[attr-defined]


@pytest.mark.parametrize("kpi", KPIS, ids=lambda kpi: kpi.key)
def test_every_kpi_says_what_it_is_for_and_what_it_means(kpi: object) -> None:
    assert kpi.question.endswith("?")  # type: ignore[attr-defined]
    assert len(kpi.interpretation) > 40  # type: ignore[attr-defined]


def test_all_three_families_are_represented() -> None:
    for family in KpiFamily:
        assert kpis_for(family), family


def test_a_ratio_that_should_go_up_is_not_also_one_that_should_go_down() -> None:
    success = KPIS_BY_KEY["success_ratio"]
    failure = KPIS_BY_KEY["failure_ratio"]
    assert success.direction is Direction.UP
    assert failure.direction is Direction.DOWN


def test_the_output_guardrail_indicator_is_the_one_that_should_never_move() -> None:
    kpi = KPIS_BY_KEY["output_guardrail_blocks"]
    assert kpi.family is KpiFamily.SAFETY
    assert kpi.direction is Direction.DOWN
    assert "zero" in kpi.interpretation


def test_business_indicators_are_not_scored_as_good_or_bad() -> None:
    for kpi in kpis_for(KpiFamily.BUSINESS):
        assert kpi.direction is Direction.NEUTRAL, kpi.key
