"""Reading a histogram back out, without parsing our own exposition format."""

from __future__ import annotations

from telecom_mcp.observability.metrics import Metrics


def a_registry() -> Metrics:
    metrics = Metrics(buckets=(0.1, 0.5, 1.0))
    for value in (0.05, 0.05, 0.2, 0.7, 5.0):
        metrics.observe("tool_duration_seconds", value, tool="get_invoice_summary")
    return metrics


def test_the_snapshot_reports_counts_sum_and_observations() -> None:
    summary = a_registry().histogram_snapshot()["tool_duration_seconds"]
    ((_, series),) = summary.items()
    assert series.observations == 5
    assert series.counts == (2, 1, 1, 1)
    assert round(series.total, 2) == 6.0


def test_the_mean_is_the_sum_over_the_count() -> None:
    ((_, series),) = a_registry().histogram_snapshot()["tool_duration_seconds"].items()
    assert round(series.mean, 2) == 1.2


def test_an_empty_histogram_reports_zero_rather_than_dividing_by_it() -> None:
    metrics = Metrics()
    metrics.observe("d", 0.0, tool="t")
    ((_, series),) = metrics.histogram_snapshot()["d"].items()
    assert series.observations == 1
    assert Metrics().histogram_snapshot() == {}


def test_a_quantile_lands_on_the_bucket_upper_edge() -> None:
    ((_, series),) = a_registry().histogram_snapshot()["tool_duration_seconds"].items()
    assert series.quantile(0.5) == 0.5
    assert series.quantile(0.9) == 1.0


def test_observations_above_the_last_bucket_are_reported_separately() -> None:
    ((_, series),) = a_registry().histogram_snapshot()["tool_duration_seconds"].items()
    assert series.above_last_bucket == 1
    assert series.quantile(0.99) == 1.0


def test_the_snapshot_is_a_copy_and_not_a_live_view() -> None:
    metrics = a_registry()
    taken = metrics.histogram_snapshot()
    metrics.observe("tool_duration_seconds", 0.3, tool="get_invoice_summary")
    ((_, series),) = taken["tool_duration_seconds"].items()
    assert series.observations == 5
