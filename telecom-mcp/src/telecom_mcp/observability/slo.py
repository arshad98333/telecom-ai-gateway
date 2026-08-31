"""Service level objectives, and the error budget each one implies.

An objective without a budget is a wish. The budget is the part that makes an SLO
useful: it converts "we want 99.5% success" into a number of failed calls per window
that everyone has already agreed to tolerate, which turns the argument about whether to
ship into arithmetic.

Objectives are set here rather than in the alerting system on purpose. An alert
threshold that lives only in a dashboard is a threshold nobody can review, nobody can
test, and nobody can diff. These are constants in a file with a test that they are
internally consistent, and the alert rules in ``infra/observability`` are generated to
match rather than typed in twice.

The numbers themselves are the starting position, not a law of nature. They are set
where an experienced on-call engineer would expect them for a tool server in front of a
telecom middleware, and they should be argued down once there is a month of real
traffic to argue with.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from telecom_mcp.observability.kpi import KPIS_BY_KEY, Kpi, KpiReport


class Comparison(StrEnum):
    AT_LEAST = "at_least"
    AT_MOST = "at_most"


class Severity(StrEnum):
    """What happens when this breaches, which is the only part of an alert that
    matters at four in the morning."""

    PAGE = "page"
    TICKET = "ticket"


@dataclass(frozen=True, slots=True)
class Slo:
    """One objective on one indicator."""

    kpi_key: str
    comparison: Comparison
    objective: float
    #: The rolling window the objective is measured over, in days.
    window_days: int
    severity: Severity
    rationale: str
    #: Below this many observations the objective is not evaluated. A 100% failure rate
    #: over three calls at 4am is noise, and paging on it is how a rota stops reading
    #: pages at all.
    minimum_sample: int = 100

    @property
    def kpi(self) -> Kpi:
        return KPIS_BY_KEY[self.kpi_key]


SLOS: Final[tuple[Slo, ...]] = (
    Slo(
        kpi_key="success_ratio",
        comparison=Comparison.AT_LEAST,
        objective=0.995,
        window_days=30,
        severity=Severity.PAGE,
        rationale=(
            "Five failures in a thousand. Above that, an agent mid-call with a "
            "customer starts seeing failures often enough to change how it behaves."
        ),
    ),
    Slo(
        kpi_key="latency_p95_seconds",
        comparison=Comparison.AT_MOST,
        objective=2.0,
        window_days=30,
        severity=Severity.PAGE,
        rationale=(
            "A voice case has a five minute budget and an agent may need four tools "
            "inside it. Two seconds at p95 leaves room for the conversation."
        ),
    ),
    Slo(
        kpi_key="latency_p99_seconds",
        comparison=Comparison.AT_MOST,
        objective=8.0,
        window_days=30,
        severity=Severity.TICKET,
        rationale=(
            "Below the ten second tool timeout, so the tail is slow rather than "
            "refused. A ticket, not a page: one slow call does not lose the case."
        ),
    ),
    Slo(
        kpi_key="calls_over_budget",
        comparison=Comparison.AT_MOST,
        objective=0.0,
        window_days=1,
        severity=Severity.TICKET,
        rationale=(
            "Any call past the ten second budget is a customer sitting in silence. "
            "Rare enough to look at each one individually."
        ),
        minimum_sample=1,
    ),
    Slo(
        kpi_key="shed_ratio",
        comparison=Comparison.AT_MOST,
        objective=0.01,
        window_days=7,
        severity=Severity.TICKET,
        rationale=(
            "Shedding is designed behaviour, so it is not a page. Sustained above one "
            "percent it is a capacity decision that someone has been avoiding."
        ),
    ),
    Slo(
        kpi_key="backend_retry_ratio",
        comparison=Comparison.AT_MOST,
        objective=0.05,
        window_days=7,
        severity=Severity.TICKET,
        rationale=(
            "Rises before failures do. Watching it is how the middleware's bad day is "
            "noticed before it becomes ours."
        ),
    ),
    Slo(
        kpi_key="output_guardrail_blocks",
        comparison=Comparison.AT_MOST,
        objective=0.0,
        window_days=30,
        severity=Severity.PAGE,
        rationale=(
            "This should be zero forever. A single one means a write happened, the "
            "response was withheld, and redaction has met a field it does not know."
        ),
        minimum_sample=1,
    ),
)

SLOS_BY_KEY: Final[dict[str, Slo]] = {slo.kpi_key: slo for slo in SLOS}


@dataclass(frozen=True, slots=True)
class SloStatus:
    """Where one objective stands right now."""

    slo: Slo
    actual: float
    sample_size: int
    #: None when the sample is too small to judge, which is not the same as "met".
    met: bool | None
    #: Fraction of the tolerated badness still unspent, for the objectives where that
    #: is meaningful. 1.0 is untouched, 0.0 is exhausted, negative is overspent.
    budget_remaining: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "kpi": self.slo.kpi_key,
            "comparison": str(self.slo.comparison),
            "objective": self.slo.objective,
            "window_days": self.slo.window_days,
            "severity": str(self.slo.severity),
            "actual": round(self.actual, 6),
            "sample_size": self.sample_size,
            "met": self.met,
            "budget_remaining": (
                None if self.budget_remaining is None else round(self.budget_remaining, 6)
            ),
            "rationale": self.slo.rationale,
        }


def evaluate_slos(report: KpiReport) -> tuple[SloStatus, ...]:
    """Judge every objective against one KPI report."""
    return tuple(_evaluate(slo, report) for slo in SLOS)


def _evaluate(slo: Slo, report: KpiReport) -> SloStatus:
    value = report.by_key(slo.kpi_key)
    if value.sample_size < slo.minimum_sample:
        # Not enough traffic to say anything. Reported as unknown rather than as met,
        # because "met" on a sample of three is the kind of green that hides an outage.
        return SloStatus(
            slo=slo,
            actual=value.value,
            sample_size=value.sample_size,
            met=None,
            budget_remaining=None,
        )

    met = (
        value.value >= slo.objective
        if slo.comparison is Comparison.AT_LEAST
        else value.value <= slo.objective
    )
    return SloStatus(
        slo=slo,
        actual=value.value,
        sample_size=value.sample_size,
        met=met,
        budget_remaining=_budget_remaining(slo, value.value),
    )


def _budget_remaining(slo: Slo, actual: float) -> float | None:
    """How much of the tolerated badness is left, where that is a meaningful question.

    Only the ratio objectives have a budget. "p95 under two seconds" does not consume
    anything when it is met; it is a threshold, not an allowance, and pretending
    otherwise produces a number that looks precise and means nothing.
    """
    if slo.kpi.unit.value != "ratio":
        return None
    tolerated = 1.0 - slo.objective if slo.comparison is Comparison.AT_LEAST else slo.objective
    if tolerated <= 0:
        return None
    spent = (1.0 - actual) if slo.comparison is Comparison.AT_LEAST else actual
    return 1.0 - (spent / tolerated)
