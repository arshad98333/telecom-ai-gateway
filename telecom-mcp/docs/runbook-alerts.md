# Runbook: the three alerts that page

Everything else makes a ticket. If you have been woken up, it is one of these.

Each section is written to be followed at four in the morning by someone who did not
write this service: what fired, what it means, what to check in order, and what to do.
The first step of every one of them is the same, so it is written once here.

## Step zero, every time

```bash
# Which objective is breached, from the service itself, without a dashboard.
curl -s https://<host>/kpi | jq '.breached'

# Is this instance actually able to serve?
curl -s https://<host>/readyz | jq '{status, components: [.components[] | {name, status, detail}]}'
```

`/kpi` answers "which objective" and `/readyz` answers "whose fault". If readiness says
`telecom_middleware` is unhealthy, you are looking at a middleware incident and the
rest of this runbook is about symptoms.

---

## 1. `success_ratio` below 99.5%

**What it means.** More than five calls in a thousand are failing after
authorization. An agent mid-call with a customer is seeing failures often enough to
change how it behaves.

**Check, in order.**

1. `curl -s https://<host>/kpi | jq '.kpis[] | select(.key=="failure_ratio")'` - is the
   failure ratio the thing that moved, or has the denial ratio moved instead? A rise in
   denials is not this alert's fault; see alert 4 in the ticket runbook.
2. `backend_retry_ratio` on the same output. If it rose first, this is the middleware
   and not us.
3. Application Insights:
   ```kusto
   dependencies
   | where name == "execute_tool" and timestamp > ago(1h)
   | extend outcome = tostring(customDimensions["outcome"]),
            code = tostring(customDimensions["code"]),
            tool = tostring(customDimensions["tool"])
   | where outcome == "failed"
   | summarize count() by code, tool
   | order by count_ desc
   ```
   One code dominating names the problem. `backend_timeout` is the middleware,
   `circuit_open` means we already stopped asking, `internal_error` is ours and there
   will be a `tool_call_crashed` log line with a stack trace.

**What to do.** If it is the middleware, the circuit breaker is already shedding and
the correct action is to escalate to that team rather than to restart anything.
Restarting replicas resets the breakers and makes it worse. If it is
`internal_error`, roll back: `docs/runbook-rollback.md`.

---

## 2. `latency_p95_seconds` above 2 seconds

**What it means.** A voice case has a five minute budget and an agent may need four
tools inside it. At p95 above two seconds that stops fitting.

**Check, in order.**

1. `calls_over_budget` on `/kpi`. If it is non-zero, calls are hitting the ten second
   timeout and customers are sitting in silence, not just waiting.
2. `shed_ratio`. Non-zero means we are at the concurrency limit and the answer is
   replicas, not tuning.
3. Which tool:
   ```kusto
   dependencies
   | where name == "execute_tool" and timestamp > ago(1h)
   | extend tool = tostring(customDimensions["tool"])
   | summarize p95 = percentile(duration, 95), calls = count() by tool
   | order by p95 desc
   ```
4. `backend_retry_ratio`. Retries multiply latency: two retries on a two second
   backend is a six second call that still succeeds, which is how p95 doubles while
   the failure ratio stays flat.

**What to do.** Scale out if shedding. Escalate to the middleware team if one tool
dominates and its backend is slow. Do not raise `TELECOM_MCP_TOOL_TIMEOUT_S` to make
the alert stop; the budget is what protects the customer from a call that will not
finish in time to be useful.

---

## 3. `output_guardrail_blocks` above zero

**This is the one that matters.** It should be zero forever.

**What it means.** A write completed and the response was withheld, because after
redaction the payload still matched a secret shape. Two things are true at once: the
customer's action happened, and the caller was not told what it produced.

**Check, in order.**

1. Which rule fired, from the audit trail rather than from a dashboard:
   ```kusto
   dependencies
   | where name == "execute_tool" and timestamp > ago(24h)
   | extend stage = tostring(customDimensions["stage"]),
            rule = tostring(customDimensions["rule"]),
            tool = tostring(customDimensions["tool"]),
            correlation = tostring(customDimensions["correlation_id"])
   | where stage startswith "output_"
   | project timestamp, tool, stage, rule, correlation
   ```
2. Find the audit records for those correlation ids. They will say
   `action_executed: true` and carry `extra.guardrail_rule`.
3. `output_size` is usually a client or a backend returning far more than a tool
   contract allows, and is a bug rather than an incident.
   `output_secret` is an incident: the backend has started returning a field that
   `observability/redaction.py` does not know about.

**What to do.**

* Do **not** switch the scan off. It is refused in production for this reason.
* Identify the field, add it to `NEVER_DISCLOSED` or `PSEUDONYMISED` in
  `observability/redaction.py`, and ship that fix on the normal path - it is a one-line
  change with a test and it does not need a hotfix process.
* Tell whoever owns the middleware that they added a field carrying a secret to a
  response, because they will have added it to more than one endpoint.
* For each affected correlation id, the customer's write happened. Whether that needs
  a human to follow up is a support decision, not an engineering one; hand the list to
  the support lead.

---

## What not to do, in any of the three

* Do not restart replicas to make a number move. It resets circuit breakers, empties
  the in-memory idempotency store on any instance still using one, and loses the
  buffered spans that would have told you what happened.
* Do not loosen a guardrail or an objective during an incident. Both are the record of
  a decision somebody made while thinking clearly, which is not the state you are in.
* Do not deploy forward to fix a regression. Roll back first
  (`docs/runbook-rollback.md`), then fix on `development` and promote normally.
