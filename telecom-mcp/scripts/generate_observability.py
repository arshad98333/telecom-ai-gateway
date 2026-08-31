#!/usr/bin/env python
"""Generate the dashboards and alert rules from the KPI and SLO catalogues.

Written rather than hand-maintained for one reason: a threshold that exists in two
places will eventually be two different thresholds, and the copy that is wrong is
always the one an alert fires from. `observability/slo.py` is the single source, this
turns it into the artefacts each system wants, and `make observability-check` fails the
build when the committed output no longer matches.

Outputs, all under infra/observability/:

* ``grafana-dashboard.json`` - PromQL against the /metrics scrape.
* ``queries.kql``            - the same questions asked of Application Insights.
* ``azure-workbook.json``    - a workbook wrapping those queries.
* ``alerts.bicep``           - one scheduled query rule per objective.

The two query languages are kept side by side deliberately. The Prometheus path is what
a local Grafana and any Kubernetes-shaped deployment will use; the KQL path is what the
Container Apps deployment actually has, because Application Insights is where the
on-call engineer already has a tab open.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Final

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from telecom_mcp.observability.kpi import KPIS, Direction, Kpi, KpiFamily, KpiUnit
from telecom_mcp.observability.slo import SLOS, Comparison, Severity, Slo

OUT = Path(__file__).resolve().parents[1] / "infra" / "observability"

#: Window every rate is computed over. Five minutes is short enough to notice an
#: incident and long enough that a quiet minute does not read as an outage.
WINDOW: Final = "5m"

_CALLS = f"sum(rate(tool_calls_total[{WINDOW}]))"
_TERMINAL = (
    f'sum(rate(tool_calls_total{{outcome=~"ok|deduplicated|failed|denied|guardrail_blocked"}}'
    f"[{WINDOW}]))"
)


def _outcome(outcome: str) -> str:
    return f'sum(rate(tool_calls_total{{outcome="{outcome}"}}[{WINDOW}]))'


PROMQL: Final[dict[str, str]] = {
    "tool_calls": _TERMINAL,
    "success_ratio": f"({_outcome('ok')} + {_outcome('deduplicated')}) / clamp_min({_TERMINAL}, 1)",
    "failure_ratio": f"{_outcome('failed')} / clamp_min({_TERMINAL}, 1)",
    "latency_p95_seconds": (
        f"histogram_quantile(0.95, sum by (le) (rate(tool_duration_seconds_bucket[{WINDOW}])))"
    ),
    "latency_p99_seconds": (
        f"histogram_quantile(0.99, sum by (le) (rate(tool_duration_seconds_bucket[{WINDOW}])))"
    ),
    "calls_over_budget": (
        f"sum(increase(tool_duration_seconds_count[{WINDOW}])) - "
        f'sum(increase(tool_duration_seconds_bucket{{le="10"}}[{WINDOW}]))'
    ),
    "shed_ratio": f"{_outcome('shed')} / clamp_min({_TERMINAL}, 1)",
    "deduplication_ratio": f"{_outcome('deduplicated')} / clamp_min({_TERMINAL}, 1)",
    "backend_retry_ratio": (
        f'sum(rate(backend_attempts_total{{stage!="1"}}[{WINDOW}])) / '
        f"clamp_min(sum(rate(backend_attempts_total[{WINDOW}])), 1)"
    ),
    "authorization_denial_ratio": f"{_outcome('denied')} / clamp_min({_TERMINAL}, 1)",
    "guardrail_block_ratio": (
        f"sum(rate(guardrail_decisions_total[{WINDOW}])) / clamp_min({_TERMINAL}, 1)"
    ),
    "output_guardrail_blocks": (
        f'sum(increase(guardrail_decisions_total{{stage=~"output_.*"}}[{WINDOW}]))'
    ),
    "tickets_created": (
        f'sum(increase(tool_calls_total{{tool="create_support_ticket",outcome="ok"}}[{WINDOW}]))'
    ),
    "callbacks_scheduled": (
        f'sum(increase(tool_calls_total{{tool="schedule_callback",outcome="ok"}}[{WINDOW}]))'
    ),
    "approvals_requested": (
        f'sum(increase(tool_calls_total{{tool="request_refund_approval",outcome="ok"}}[{WINDOW}]))'
    ),
}

#: Spans exported by the Azure Monitor exporter land in `dependencies`, because they are
#: internal spans rather than inbound requests. The attributes travel as
#: customDimensions, which is why every filter below reaches through it.
_KQL_BASE = """let calls = dependencies
    | where name == "execute_tool"
    | where timestamp > ago({window})
    | extend outcome = tostring(customDimensions["outcome"]),
             tool = tostring(customDimensions["tool"]),
             stage = tostring(customDimensions["stage"]);"""

KQL: Final[dict[str, str]] = {
    "tool_calls": "calls | summarize value = count()",
    "success_ratio": (
        'calls | summarize value = todouble(countif(outcome in ("ok"))) '
        "/ todouble(max_of(count(), 1))"
    ),
    "failure_ratio": (
        'calls | summarize value = todouble(countif(outcome == "failed")) '
        "/ todouble(max_of(count(), 1))"
    ),
    "latency_p95_seconds": "calls | summarize value = percentile(duration, 95) / 1000.0",
    "latency_p99_seconds": "calls | summarize value = percentile(duration, 99) / 1000.0",
    "calls_over_budget": "calls | summarize value = countif(duration > 10000)",
    "shed_ratio": (
        'calls | summarize value = todouble(countif(outcome == "shed")) '
        "/ todouble(max_of(count(), 1))"
    ),
    "deduplication_ratio": (
        'calls | summarize value = todouble(countif(outcome == "deduplicated")) '
        "/ todouble(max_of(count(), 1))"
    ),
    "backend_retry_ratio": (
        'calls | summarize value = todouble(countif(outcome == "failed")) '
        "/ todouble(max_of(count(), 1))"
    ),
    "authorization_denial_ratio": (
        'calls | summarize value = todouble(countif(outcome == "denied")) '
        "/ todouble(max_of(count(), 1))"
    ),
    "guardrail_block_ratio": (
        'calls | summarize value = todouble(countif(outcome == "guardrail_blocked")) '
        "/ todouble(max_of(count(), 1))"
    ),
    "output_guardrail_blocks": (
        'calls | where stage startswith "output_" | summarize value = count()'
    ),
    "tickets_created": (
        'calls | where tool == "create_support_ticket" and outcome == "ok" '
        "| summarize value = count()"
    ),
    "callbacks_scheduled": (
        'calls | where tool == "schedule_callback" and outcome == "ok" | summarize value = count()'
    ),
    "approvals_requested": (
        'calls | where tool == "request_refund_approval" and outcome == "ok" '
        "| summarize value = count()"
    ),
}

_UNIT_TO_GRAFANA: Final[dict[KpiUnit, str]] = {
    KpiUnit.COUNT: "short",
    KpiUnit.RATIO: "percentunit",
    KpiUnit.SECONDS: "s",
}


def _thresholds(kpi: Kpi, slo: Slo | None) -> dict[str, object]:
    """Colour a panel from its objective, and never from a guess.

    A panel with no objective gets no thresholds. Inventing one would mean a dashboard
    that goes red on a number nobody agreed to, which is how people learn to ignore red.
    """
    if slo is None or kpi.direction is Direction.NEUTRAL:
        return {"mode": "absolute", "steps": [{"color": "text", "value": None}]}
    if slo.comparison is Comparison.AT_LEAST:
        return {
            "mode": "absolute",
            "steps": [
                {"color": "red", "value": None},
                {"color": "green", "value": slo.objective},
            ],
        }
    return {
        "mode": "absolute",
        "steps": [
            {"color": "green", "value": None},
            {"color": "red", "value": slo.objective if slo.objective > 0 else 1},
        ],
    }


def _panel(index: int, kpi: Kpi, slo: Slo | None) -> dict[str, object]:
    row, column = divmod(index, 3)
    return {
        "id": index + 1,
        "type": "timeseries",
        "title": kpi.title,
        "description": f"{kpi.question}\n\n{kpi.interpretation}",
        "gridPos": {"h": 8, "w": 8, "x": column * 8, "y": row * 8},
        "fieldConfig": {
            "defaults": {
                "unit": _UNIT_TO_GRAFANA[kpi.unit],
                "thresholds": _thresholds(kpi, slo),
            },
            "overrides": [],
        },
        "targets": [{"expr": PROMQL[kpi.key], "legendFormat": kpi.title, "refId": "A"}],
    }


def build_dashboard() -> dict[str, object]:
    by_key = {slo.kpi_key: slo for slo in SLOS}
    ordered = [
        kpi
        for family in (KpiFamily.SERVICE, KpiFamily.SAFETY, KpiFamily.BUSINESS)
        for kpi in KPIS
        if kpi.family is family
    ]
    return {
        "title": "Telecom MCP tools",
        "uid": "telecom-mcp-tools",
        "tags": ["telecom", "mcp", "generated"],
        "timezone": "utc",
        "schemaVersion": 39,
        "refresh": "1m",
        "time": {"from": "now-6h", "to": "now"},
        "description": (
            "Generated by scripts/generate_observability.py from the KPI and SLO "
            "catalogues. Edit those, not this file."
        ),
        "panels": [_panel(index, kpi, by_key.get(kpi.key)) for index, kpi in enumerate(ordered)],
    }


def build_kql() -> str:
    lines = [
        "// Generated by scripts/generate_observability.py. Edit the catalogues, not this.",
        "//",
        "// Spans from the Azure Monitor exporter land in `dependencies` rather than",
        "// `requests`, because they are internal spans. Attributes arrive as",
        "// customDimensions, which is why every query reaches through it.",
        "",
    ]
    for kpi in KPIS:
        lines += [
            f"// --- {kpi.title} ({kpi.key}) " + "-" * max(0, 60 - len(kpi.title) - len(kpi.key)),
            f"// {kpi.question}",
            f"// {kpi.interpretation}",
            _KQL_BASE.format(window="1h"),
            KQL[kpi.key],
            "",
        ]
    return "\n".join(lines) + "\n"


def build_workbook() -> dict[str, object]:
    items: list[dict[str, object]] = [
        {
            "type": 1,
            "content": {
                "json": (
                    "# Telecom MCP tools\n\nGenerated from the KPI and SLO catalogues "
                    "by `scripts/generate_observability.py`. Every tile below states "
                    "the question it answers and what it means when it moves."
                )
            },
        }
    ]
    for kpi in KPIS:
        items.append(
            {
                "type": 1,
                "content": {"json": f"## {kpi.title}\n\n{kpi.question}\n\n{kpi.interpretation}"},
            }
        )
        items.append(
            {
                "type": 3,
                "content": {
                    "version": "KqlItem/1.0",
                    "query": _KQL_BASE.format(window="{TimeRange}") + "\n" + KQL[kpi.key],
                    "size": 1,
                    "title": kpi.title,
                    "queryType": 0,
                    "resourceType": "microsoft.insights/components",
                    "visualization": "tiles" if kpi.unit is KpiUnit.COUNT else "linechart",
                },
                "name": kpi.key,
            }
        )
    return {
        "version": "Notebook/1.0",
        "items": items,
        "fallbackResourceIds": [],
        "$schema": (
            "https://github.com/Microsoft/Application-Insights-Workbooks/blob/master/"
            "schema/workbook.json"
        ),
    }


def build_alerts() -> str:
    lines = [
        "// Generated by scripts/generate_observability.py from observability/slo.py.",
        "// One scheduled query rule per objective. Edit the objectives, not this file.",
        "//",
        "// Severity maps to what happens, which is the only part of an alert that",
        "// matters at four in the morning: sev1 wakes someone, sev3 makes a ticket.",
        "",
        "@description('Application Insights component the rules query.')",
        "param applicationInsightsId string",
        "",
        "@description('Action group notified when a rule fires.')",
        "param actionGroupId string",
        "",
        "@description('Environment name, used in the rule names and the alert body.')",
        "@allowed(['staging', 'production'])",
        "param environmentName string",
        "",
        "param location string = resourceGroup().location",
        "",
    ]
    for slo in SLOS:
        kpi = slo.kpi
        severity = 1 if slo.severity is Severity.PAGE else 3
        operator = "LessThan" if slo.comparison is Comparison.AT_LEAST else "GreaterThan"
        resource_name = slo.kpi_key.replace("_", "-")
        query = (_KQL_BASE.format(window="30m") + "\n" + KQL[slo.kpi_key]).replace("'", "\\'")
        lines += [
            f"// {kpi.title}: {slo.rationale}",
            f"resource alert_{slo.kpi_key} "
            "'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {",
            f"  name: 'telecom-mcp-${{environmentName}}-{resource_name}'",
            "  location: location",
            "  properties: {",
            f"    description: '{kpi.title} breached its objective. {kpi.interpretation}'",
            f"    severity: {severity}",
            "    enabled: true",
            "    scopes: [ applicationInsightsId ]",
            "    evaluationFrequency: 'PT5M'",
            "    windowSize: 'PT30M'",
            "    criteria: {",
            "      allOf: [",
            "        {",
            f"          query: '''\n{query}\n'''",
            "          timeAggregation: 'Average'",
            "          metricMeasureColumn: 'value'",
            f"          operator: '{operator}'",
            f"          threshold: {slo.objective}",
            "          failingPeriods: {",
            "            numberOfEvaluationPeriods: 2",
            "            minFailingPeriodsToAlert: 2",
            "          }",
            "        }",
            "      ]",
            "    }",
            "    autoMitigate: true",
            "    actions: { actionGroups: [ actionGroupId ] }",
            "  }",
            "}",
            "",
        ]
    return "\n".join(lines)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    missing = [kpi.key for kpi in KPIS if kpi.key not in PROMQL or kpi.key not in KQL]
    if missing:
        print(f"no query defined for: {', '.join(missing)}", file=sys.stderr)
        return 1

    (OUT / "grafana-dashboard.json").write_text(
        json.dumps(build_dashboard(), indent=2) + "\n", encoding="utf-8"
    )
    (OUT / "queries.kql").write_text(build_kql(), encoding="utf-8")
    (OUT / "azure-workbook.json").write_text(
        json.dumps(build_workbook(), indent=2) + "\n", encoding="utf-8"
    )
    (OUT / "alerts.bicep").write_text(build_alerts(), encoding="utf-8")
    print(f"wrote 4 files to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
