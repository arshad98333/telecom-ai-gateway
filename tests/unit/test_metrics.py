"""Metrics must be cheap, bounded and safe to expose."""

import pytest

from telecom_mcp.observability.metrics import Metrics


def test_counters_accumulate_per_label_set() -> None:
    metrics = Metrics()

    metrics.increment("tool_calls_total", tool="get_customer_account", outcome="ok")
    metrics.increment("tool_calls_total", tool="get_customer_account", outcome="ok")
    metrics.increment("tool_calls_total", tool="get_customer_account", outcome="denied")

    snapshot = metrics.snapshot()["tool_calls_total"]
    assert snapshot[(("outcome", "ok"), ("tool", "get_customer_account"))] == 2
    assert snapshot[(("outcome", "denied"), ("tool", "get_customer_account"))] == 1


def test_a_customer_identifier_can_never_become_a_label() -> None:
    # An unbounded label set is how a metrics bill becomes a surprise.
    with pytest.raises(ValueError, match="not allowed"):
        Metrics().increment("tool_calls_total", cx_id="CX-1234")


def test_histogram_buckets_are_cumulative_and_include_an_infinity_bucket() -> None:
    metrics = Metrics(buckets=(1.0, 5.0))

    for value in (0.5, 2.0, 20.0):
        metrics.observe("tool_duration_seconds", value, tool="t")

    rendered = metrics.render_prometheus()
    assert 'tool_duration_seconds_bucket{tool="t",le="1"} 1' in rendered
    assert 'tool_duration_seconds_bucket{tool="t",le="5"} 2' in rendered
    assert 'tool_duration_seconds_bucket{tool="t",le="+Inf"} 3' in rendered
    assert 'tool_duration_seconds_count{tool="t"} 3' in rendered
    assert 'tool_duration_seconds_sum{tool="t"} 22.5' in rendered


def test_gauges_replace_rather_than_accumulate() -> None:
    metrics = Metrics()

    metrics.set_gauge("in_flight_calls", 3)
    metrics.set_gauge("in_flight_calls", 1)

    assert metrics.snapshot()["in_flight_calls"][()] == 1


def test_the_registry_refuses_to_grow_without_bound() -> None:
    metrics = Metrics()

    for index in range(2500):
        metrics.increment("tool_calls_total", tool=f"tool-{index}")

    assert len(metrics.snapshot()["tool_calls_total"]) == 2000


def test_label_values_are_escaped_so_the_exposition_format_cannot_be_broken() -> None:
    metrics = Metrics()

    metrics.increment("tool_calls_total", tool='we"ird\nvalue')

    assert 'tool="we\\"ird\\nvalue"' in metrics.render_prometheus()


def test_rendering_an_empty_registry_is_valid_output() -> None:
    assert Metrics().render_prometheus() == "\n"
