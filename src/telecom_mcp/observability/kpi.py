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
from typing import Final


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
