"""The indicators this service is judged on, named once, in one place.

A dashboard nobody agreed on is a dashboard nobody trusts. The catalogue below is the
agreement: what the number is, which series it comes from, which way is good, and -
the part that usually goes unwritten - what it means when it moves.

Everything here derives from the counters and histograms the executor already writes.
Nothing is measured twice. A KPI that needs its own instrumentation is a KPI that will
disagree with the metric next to it on the same screen within a month.

Three families, deliberately separated, because they answer to different people:

* **Service** - is it up, is it fast, is it failing. Read by whoever is on call.
* **Safety** - what are the controls refusing, and is that number changing. Read by
  security, and by whoever tuned a threshold last week.
* **Business** - what did the agent actually accomplish. Read by the people who paid
  for it, and the only family where a rise in a number is not automatically good.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol

from telecom_mcp.observability.metrics import HistogramSummary, LabelKey


class MetricsReader(Protocol):
    """The part of the registry a report needs. Narrow on purpose: the report reads
    numbers and must never be able to write one."""

    def snapshot(self) -> dict[str, dict[LabelKey, float]]: ...

    def histogram_snapshot(self) -> dict[str, dict[LabelKey, HistogramSummary]]: ...


class KpiFamily(StrEnum):
    SERVICE = "service"
    SAFETY = "safety"
    BUSINESS = "business"


class Direction(StrEnum):
    """Which way is good. Stated so a dashboard cannot colour a panel backwards."""

    UP = "up"
    DOWN = "down"
    #: Neither: the number is context, and it is a change in it that is interesting.
    NEUTRAL = "neutral"


class KpiUnit(StrEnum):
    COUNT = "count"
    RATIO = "ratio"
    SECONDS = "seconds"


@dataclass(frozen=True, slots=True)
class Kpi:
    """One indicator, and everything a reader needs to interpret it."""

    key: str
    family: KpiFamily
    title: str
    unit: KpiUnit
    direction: Direction
    #: The registry series it is derived from. Nothing here is measured separately.
    derived_from: tuple[str, ...]
    #: The question a person actually had when they opened the dashboard.
    question: str
    #: What it means when this moves. The sentence that is usually missing.
    interpretation: str


KPIS: Final[tuple[Kpi, ...]] = (
    # --- Service ---------------------------------------------------------------
    Kpi(
        key="tool_calls",
        family=KpiFamily.SERVICE,
        title="Tool calls",
        unit=KpiUnit.COUNT,
        direction=Direction.NEUTRAL,
        derived_from=("tool_calls_total",),
        question="How much work is this thing doing?",
        interpretation=(
            "The denominator for every ratio below. A sudden fall is an outage "
            "upstream of us and is worth more attention than a rise."
        ),
    ),
    Kpi(
        key="success_ratio",
        family=KpiFamily.SERVICE,
        title="Success ratio",
        unit=KpiUnit.RATIO,
        direction=Direction.UP,
        derived_from=("tool_calls_total",),
        question="Are calls working?",
        interpretation=(
            "Successful and deduplicated calls over all calls. Deduplicated counts as "
            "success because the caller got the answer they asked for."
        ),
    ),
    Kpi(
        key="failure_ratio",
        family=KpiFamily.SERVICE,
        title="Failure ratio",
        unit=KpiUnit.RATIO,
        direction=Direction.DOWN,
        derived_from=("tool_calls_total",),
        question="Are we breaking?",
        interpretation=(
            "Calls that were authorized and then failed. This is our fault or the "
            "middleware's; a denial or a guardrail block is not counted here, because "
            "a control working correctly is not an error."
        ),
    ),
    Kpi(
        key="latency_p95_seconds",
        family=KpiFamily.SERVICE,
        title="Latency p95",
        unit=KpiUnit.SECONDS,
        direction=Direction.DOWN,
        derived_from=("tool_duration_seconds",),
        question="Is it fast enough for a live call?",
        interpretation=(
            "A voice case has a five minute budget and an agent may need several "
            "tools inside it. p95 is the number that decides whether that is possible."
        ),
    ),
    Kpi(
        key="latency_p99_seconds",
        family=KpiFamily.SERVICE,
        title="Latency p99",
        unit=KpiUnit.SECONDS,
        direction=Direction.DOWN,
        derived_from=("tool_duration_seconds",),
        question="How bad is the tail?",
        interpretation=(
            "The customer who is already annoyed is in this bucket. Track it "
            "separately from p95: a p95 that holds while p99 doubles is a retry storm."
        ),
    ),
    Kpi(
        key="calls_over_budget",
        family=KpiFamily.SERVICE,
        title="Calls over the time budget",
        unit=KpiUnit.COUNT,
        direction=Direction.DOWN,
        derived_from=("tool_duration_seconds",),
        question="How many calls blew the ten second budget?",
        interpretation=(
            "Observations above the last histogram bucket. A quantile hides these; a "
            "count does not, and this is the one number that maps to a real customer "
            "sitting in silence."
        ),
    ),
    Kpi(
        key="shed_ratio",
        family=KpiFamily.SERVICE,
        title="Load shed ratio",
        unit=KpiUnit.RATIO,
        direction=Direction.DOWN,
        derived_from=("tool_calls_total",),
        question="Are we refusing work because we are full?",
        interpretation=(
            "Shedding is the designed behaviour under load, not a bug, but a non-zero "
            "value means capacity is the constraint and the next step is replicas."
        ),
    ),
    Kpi(
        key="deduplication_ratio",
        family=KpiFamily.SERVICE,
        title="Deduplication ratio",
        unit=KpiUnit.RATIO,
        direction=Direction.NEUTRAL,
        derived_from=("tool_calls_total",),
        question="How often is a caller repeating itself?",
        interpretation=(
            "A small number is the idempotency store doing its job. A large one is an "
            "agent retrying because it thinks we failed, which is a latency problem "
            "wearing a different hat."
        ),
    ),
    Kpi(
        key="backend_retry_ratio",
        family=KpiFamily.SERVICE,
        title="Backend retry ratio",
        unit=KpiUnit.RATIO,
        direction=Direction.DOWN,
        derived_from=("backend_attempts_total",),
        question="Is the middleware healthy?",
        interpretation=(
            "Attempts beyond the first, over all attempts. Rises before failures do, "
            "which makes it the earliest honest warning we have."
        ),
    ),
    # --- Safety ----------------------------------------------------------------
    Kpi(
        key="authorization_denial_ratio",
        family=KpiFamily.SAFETY,
        title="Authorization denial ratio",
        unit=KpiUnit.RATIO,
        direction=Direction.NEUTRAL,
        derived_from=("tool_calls_total",),
        question="How much is the kernel refusing?",
        interpretation=(
            "A steady low number is normal: agents probe tools they do not hold "
            "scopes for. A step change means a token, a role or a claim namespace "
            "changed, and it will usually have changed by accident."
        ),
    ),
    Kpi(
        key="guardrail_block_ratio",
        family=KpiFamily.SAFETY,
        title="Guardrail block ratio",
        unit=KpiUnit.RATIO,
        direction=Direction.NEUTRAL,
        derived_from=("tool_calls_total", "guardrail_decisions_total"),
        question="What are the guardrails stopping?",
        interpretation=(
            "Near zero is expected and is not a reason to relax anything. A spike in "
            "the injection stage is an attack or a bad prompt template; a spike in "
            "argument_size is usually a client bug."
        ),
    ),
    Kpi(
        key="output_guardrail_blocks",
        family=KpiFamily.SAFETY,
        title="Output guardrail blocks",
        unit=KpiUnit.COUNT,
        direction=Direction.DOWN,
        derived_from=("guardrail_decisions_total",),
        question="Did we nearly hand a secret to the model?",
        interpretation=(
            "This should be zero forever. Any non-zero value means a write happened "
            "and the response was withheld, and it is a page rather than a ticket: "
            "redaction has met a field it does not know about."
        ),
    ),
    # --- Business --------------------------------------------------------------
    Kpi(
        key="tickets_created",
        family=KpiFamily.BUSINESS,
        title="Tickets created",
        unit=KpiUnit.COUNT,
        direction=Direction.NEUTRAL,
        derived_from=("tool_calls_total",),
        question="What did the agent actually do for people?",
        interpretation=(
            "Up is not automatically good. Read it beside the callback and refund "
            "numbers: more tickets and fewer callbacks is the agent resolving things."
        ),
    ),
    Kpi(
        key="callbacks_scheduled",
        family=KpiFamily.BUSINESS,
        title="Callbacks scheduled",
        unit=KpiUnit.COUNT,
        direction=Direction.NEUTRAL,
        derived_from=("tool_calls_total",),
        question="How often does this end with a human ringing back?",
        interpretation=(
            "The closest thing to a containment measure we have. A rise means the "
            "agent is handing more away, which may be correct and is never neutral."
        ),
    ),
    Kpi(
        key="approvals_requested",
        family=KpiFamily.BUSINESS,
        title="Refund approvals requested",
        unit=KpiUnit.COUNT,
        direction=Direction.NEUTRAL,
        derived_from=("tool_calls_total",),
        question="How much money is queued behind a supervisor?",
        interpretation=(
            "Nothing has moved when this increments; it is a queue depth for humans. "
            "Watch it against the action budget: a rise in both is one case looping."
        ),
    ),
)

#: Lookup by key, built once.
KPIS_BY_KEY: Final[dict[str, Kpi]] = {kpi.key: kpi for kpi in KPIS}


def kpis_for(family: KpiFamily) -> tuple[Kpi, ...]:
    return tuple(kpi for kpi in KPIS if kpi.family is family)


# --- Computing the values -----------------------------------------------------------
#
# Everything below reads the registry and derives the catalogue above from it. It is
# deliberately a pure function of a snapshot: given the same numbers it returns the
# same report, so it can be tested without a running server and cannot itself become a
# source of drift.

#: Terminal outcomes of a tool call. One call produces exactly one of these.
TERMINAL_OUTCOMES: Final[frozenset[str]] = frozenset(
    {"ok", "deduplicated", "failed", "denied", "guardrail_blocked"}
)
#: Counted when a call is admitted late, in addition to its terminal outcome, so it is
#: an observation about pressure rather than an outcome in its own right.
PRESSURE_OUTCOMES: Final[frozenset[str]] = frozenset({"shed"})


@dataclass(frozen=True, slots=True)
class KpiValue:
    """One computed indicator."""

    kpi: Kpi
    value: float
    #: What the value was computed over. A ratio of nothing is reported as zero with a
    #: sample size of zero, so a dashboard can grey it out rather than draw 0%.
    sample_size: int
    #: Optional split, for the indicators where the total is not the interesting part.
    breakdown: dict[str, float] | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "key": self.kpi.key,
            "family": str(self.kpi.family),
            "title": self.kpi.title,
            "unit": str(self.kpi.unit),
            "direction": str(self.kpi.direction),
            "value": round(self.value, 6),
            "sample_size": self.sample_size,
            "question": self.kpi.question,
            "interpretation": self.kpi.interpretation,
        }
        if self.breakdown is not None:
            payload["breakdown"] = {name: round(v, 6) for name, v in sorted(self.breakdown.items())}
        return payload


@dataclass(frozen=True, slots=True)
class KpiReport:
    """Every indicator, computed from one consistent read of the registry."""

    values: tuple[KpiValue, ...]

    def by_key(self, key: str) -> KpiValue:
        for value in self.values:
            if value.kpi.key == key:
                return value
        raise KeyError(key)

    def to_dict(self) -> dict[str, object]:
        return {
            "kpis": [value.to_dict() for value in self.values],
            "families": {
                str(family): [v.kpi.key for v in self.values if v.kpi.family is family]
                for family in KpiFamily
            },
        }


def build_kpi_report(metrics: MetricsReader) -> KpiReport:
    """Compute every KPI from one snapshot of the registry."""
    counters = metrics.snapshot()
    histograms = metrics.histogram_snapshot()

    calls = counters.get("tool_calls_total", {})
    by_outcome = _sum_by(calls, "outcome")
    by_tool_outcome = _sum_by_pair(calls, "tool", "outcome")
    terminal = sum(count for name, count in by_outcome.items() if name in TERMINAL_OUTCOMES)

    duration = _merge_histograms(histograms.get("tool_duration_seconds", {}))
    attempts = _sum_by(counters.get("backend_attempts_total", {}), "stage")
    guardrails = counters.get("guardrail_decisions_total", {})
    by_stage = _sum_by(guardrails, "stage")

    def ratio(numerator: float) -> tuple[float, int]:
        return (numerator / terminal if terminal else 0.0, int(terminal))

    succeeded = by_outcome.get("ok", 0.0) + by_outcome.get("deduplicated", 0.0)
    blocked = sum(by_stage.values())
    total_attempts = sum(attempts.values())
    retries = sum(count for stage, count in attempts.items() if stage != "1")

    values = [
        KpiValue(KPIS_BY_KEY["tool_calls"], terminal, int(terminal), breakdown=dict(by_outcome)),
        KpiValue(KPIS_BY_KEY["success_ratio"], *ratio(succeeded)),
        KpiValue(KPIS_BY_KEY["failure_ratio"], *ratio(by_outcome.get("failed", 0.0))),
        KpiValue(
            KPIS_BY_KEY["latency_p95_seconds"],
            duration.quantile(0.95),
            duration.observations,
        ),
        KpiValue(
            KPIS_BY_KEY["latency_p99_seconds"],
            duration.quantile(0.99),
            duration.observations,
        ),
        KpiValue(
            KPIS_BY_KEY["calls_over_budget"],
            float(duration.above_last_bucket),
            duration.observations,
        ),
        KpiValue(KPIS_BY_KEY["shed_ratio"], *ratio(by_outcome.get("shed", 0.0))),
        KpiValue(KPIS_BY_KEY["deduplication_ratio"], *ratio(by_outcome.get("deduplicated", 0.0))),
        KpiValue(
            KPIS_BY_KEY["backend_retry_ratio"],
            retries / total_attempts if total_attempts else 0.0,
            int(total_attempts),
        ),
        KpiValue(KPIS_BY_KEY["authorization_denial_ratio"], *ratio(by_outcome.get("denied", 0.0))),
        KpiValue(
            KPIS_BY_KEY["guardrail_block_ratio"],
            blocked / terminal if terminal else 0.0,
            int(terminal),
            breakdown=dict(by_stage),
        ),
        KpiValue(
            KPIS_BY_KEY["output_guardrail_blocks"],
            by_stage.get("output_secret", 0.0) + by_stage.get("output_size", 0.0),
            int(blocked),
        ),
        _business(KPIS_BY_KEY["tickets_created"], by_tool_outcome, "create_support_ticket"),
        _business(KPIS_BY_KEY["callbacks_scheduled"], by_tool_outcome, "schedule_callback"),
        _business(KPIS_BY_KEY["approvals_requested"], by_tool_outcome, "request_refund_approval"),
    ]
    return KpiReport(tuple(values))


def _business(kpi: Kpi, by_tool_outcome: dict[tuple[str, str], float], tool: str) -> KpiValue:
    """Count only the calls that produced something. A refusal created no ticket."""
    completed = by_tool_outcome.get((tool, "ok"), 0.0)
    replayed = by_tool_outcome.get((tool, "deduplicated"), 0.0)
    return KpiValue(
        kpi,
        completed,
        int(completed + replayed),
        breakdown={"created": completed, "deduplicated": replayed},
    )


def _sum_by(series: dict[LabelKey, float], label: str) -> dict[str, float]:
    totals: dict[str, float] = {}
    for key, value in series.items():
        name = dict(key).get(label)
        if name is not None:
            totals[name] = totals.get(name, 0.0) + value
    return totals


def _sum_by_pair(
    series: dict[LabelKey, float], first: str, second: str
) -> dict[tuple[str, str], float]:
    totals: dict[tuple[str, str], float] = {}
    for key, value in series.items():
        labels = dict(key)
        a, b = labels.get(first), labels.get(second)
        if a is not None and b is not None:
            totals[(a, b)] = totals.get((a, b), 0.0) + value
    return totals


def _merge_histograms(series: dict[LabelKey, HistogramSummary]) -> HistogramSummary:
    """Add up every labelled series into one. Buckets are shared across the registry."""
    summaries = list(series.values())
    if not summaries:
        return HistogramSummary(buckets=(), counts=(), total=0.0, observations=0)
    buckets = summaries[0].buckets
    counts = [0] * len(summaries[0].counts)
    total = 0.0
    observations = 0
    for summary in summaries:
        for index, count in enumerate(summary.counts):
            counts[index] += count
        total += summary.total
        observations += summary.observations
    return HistogramSummary(
        buckets=buckets, counts=tuple(counts), total=total, observations=observations
    )
