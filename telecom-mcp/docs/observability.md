# Observability

What this service tells you about itself, where each answer comes from, and which
numbers are worth waking someone for.

## Four surfaces, four questions

| Surface | Answers | Who reads it |
|---|---|---|
| `GET /healthz` | Is the process alive? Nothing external is consulted, so one backend blip cannot restart every replica at once. | The container platform |
| `GET /readyz` | Can this instance actually serve? Middleware, idempotency store, and - when the tenant is in use - the identity provider. | The load balancer |
| `GET /metrics` | Prometheus exposition of every counter, gauge and histogram. | A scraper |
| `GET /kpi` | The indicators and the objectives, with what each one means. | A person, during an incident |

`/kpi` exists because the first question in an incident is "which objective is
breached", and answering it should not require a working dashboard, a working Grafana
and a working query. It is derived from the same registry `/metrics` renders, so the
two cannot disagree, and it returns 200 even when an objective is breached - a probe
pointed at it by mistake must not restart the container.

## The series

Four, and nothing else. Everything on every dashboard is derived from these.

| Series | Type | Labels |
|---|---|---|
| `tool_calls_total` | counter | `tool`, `outcome`, `code` |
| `tool_duration_seconds` | histogram | `tool` |
| `backend_attempts_total` | counter | `tool`, `stage` (the attempt number) |
| `guardrail_decisions_total` | counter | `tool`, `stage`, `outcome` |

Labels are restricted to an allow-list (`observability/metrics.py`). A customer
identifier can never become a label, and the registry refuses one loudly rather than
accepting it and producing a bill. The same applies to span attributes.

`outcome` takes exactly one terminal value per call - `ok`, `deduplicated`, `failed`,
`denied`, `guardrail_blocked` - plus `shed`, which is recorded *in addition* when a
call is admitted late. Shed is pressure, not an outcome, and mixing it into the
denominator is the easiest way to make every ratio on the dashboard slightly wrong.

## The indicators

Defined once in `observability/kpi.py`, in three families, each carrying the question
it answers and what it means when it moves.

**Service** - is it up, is it fast, is it failing. `tool_calls`, `success_ratio`,
`failure_ratio`, `latency_p95_seconds`, `latency_p99_seconds`, `calls_over_budget`,
`shed_ratio`, `deduplication_ratio`, `backend_retry_ratio`.

**Safety** - what the controls are refusing. `authorization_denial_ratio`,
`guardrail_block_ratio`, `output_guardrail_blocks`.

**Business** - what the agent accomplished. `tickets_created`, `callbacks_scheduled`,
`approvals_requested`. These carry no direction: more tickets is not automatically
good, and a dashboard that colours them green teaches people the wrong lesson.

Two distinctions worth keeping straight:

* A denial or a guardrail block is **not** a failure. A control working correctly is
  not an error, and counting it as one makes the failure ratio a number nobody trusts.
* `calls_over_budget` counts observations past the ten second bucket. A quantile hides
  these; a count does not, and this is the one number that maps to a real customer
  sitting in silence.

## The objectives

In `observability/slo.py`, with a rationale for each, because a threshold whose reason
is not written down gets changed by whoever is most annoyed by it.

| Indicator | Objective | Window | On breach |
|---|---|---|---|
| `success_ratio` | at least 99.5% | 30d | page |
| `latency_p95_seconds` | at most 2.0s | 30d | page |
| `output_guardrail_blocks` | 0 | 30d | page |
| `latency_p99_seconds` | at most 8.0s | 30d | ticket |
| `calls_over_budget` | 0 | 1d | ticket |
| `shed_ratio` | at most 1% | 7d | ticket |
| `backend_retry_ratio` | at most 5% | 7d | ticket |

Below a minimum sample an objective reports **unknown**, not met. Green on a sample of
three is the kind of green that hides an outage.

The ratio objectives carry an error budget: the fraction of tolerated badness still
unspent, where 1.0 is untouched and a negative number is overspent. The latency
objectives do not, because a threshold is not an allowance and pretending otherwise
produces a number that looks precise and means nothing.

## Tracing

Off by default; production refuses to start without it. One span per tool call,
`execute_tool`, opened in the same place the audit record and the counters are written
so the three cannot disagree about how a call ended.

The tool is an attribute rather than part of the span name. A name that varies per tool
splits one operation into eight in every latency view a backend offers, and none of the
eight is the number anybody wanted.

Two exporters: OTLP for a local collector, Azure Monitor for the Container Apps
deployment. Both are extras (`telecom-mcp-tools[otel]`, `[azure-monitor]`); the imports
are lazy, so a missing extra is a configuration error at startup naming the extra to
install rather than an ImportError on the first traced request.

Sampling is parent-based. Sampling each service independently produces traces with
holes in them, which are worse than no traces because they look complete.

## Dashboards and alerts

Generated, never hand-edited:

```
make observability          # regenerate
make observability-check    # fail if the committed output is stale (part of make check)
```

* `infra/observability/grafana-dashboard.json` - PromQL against `/metrics`.
* `infra/observability/queries.kql` - the same questions asked of Application Insights.
* `infra/observability/azure-workbook.json` - a workbook wrapping those queries.
* `infra/observability/alerts.bicep` - one scheduled query rule per objective.

Spans from the Azure Monitor exporter land in the `dependencies` table rather than
`requests`, because they are internal spans, and the attributes arrive as
`customDimensions`. That is the detail that costs an hour the first time.

## What never appears anywhere

No customer identifier, phone number, email, address, payment detail or token reaches a
metric label, a span attribute or a log line. Identifiers operations genuinely needs to
correlate on are replaced by a stable pseudonym, so one customer can still be followed
through a day of logs without the logs holding who they are.

The registry and the tracer both enforce this with an allow-list and both raise rather
than silently dropping, because a control that fails quietly is a control that has
already failed.
