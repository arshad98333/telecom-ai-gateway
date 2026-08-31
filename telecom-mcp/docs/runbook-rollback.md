# Runbook: rolling back a release

Owner: Engineering Release Manager. On-call: AI Platform On-Call Engineer
(acknowledge within 15 minutes, mitigate within 1 hour).

## When to roll back

Roll back immediately, without waiting for a root cause, on any of these:

- a release blocker is breached: task accuracy below 95%, authorization accuracy below
  99%, or critical error rate above 1%;
- any authorization failure that let a call reach data it should not have;
- a critical security finding in the running version;
- material financial impact, including duplicated writes;
- production instability: readiness flapping, or the breaker open for more than five
  minutes against a healthy middleware API.

Rolling back is not an admission of anything. It is the cheap, reversible action.

## What "roll back" means here

The same artifact that was tested is promoted between environments, never rebuilt, so
rolling back is redeploying the previous immutable image digest. There is no build
step in a rollback, which is what makes it fast and predictable.

## Steps

1. **Announce.** Post in the incident channel: version being rolled back, symptom,
   who is running it.
2. **Find the last known-good digest.** It is recorded on the previous release's
   deployment record and in the `base-image-digest` and `distribution` build
   artifacts for that tag.
3. **Redeploy that digest.** The deployment is the same command with the previous
   digest. Do not deploy a tag.
4. **Watch readiness and the error rate** for five minutes. `GET /readyz` on every
   replica must report `healthy`, and `tool_calls_total{outcome="failed"}` must stop
   climbing.
5. **Verify one real journey.** Call `get_customer_account` for a seeded test identity
   and confirm a normal answer, then confirm the audit record for it exists.
6. **Check the audit chain.** `telecom-mcp verify-audit <log>` must report the chain
   intact. A break means records were lost during the incident, which is itself an
   incident.
7. **Freeze the branch.** No further releases until the cause is understood.

## What a rollback does not undo

Writes that already happened. Tickets, callbacks and refund approval requests created
by the bad version still exist. List them from the audit trail by correlation window
and hand them to Customer Operations; do not delete them, because the customer may
have been told they exist.

Schema migrations. This package holds no database, so there is nothing to reverse
here; if the middleware API migrated, its own rollback procedure applies and must run
first.

## Rehearsal

This runbook is exercised once per release cycle in staging, by rolling back a healthy
release and rolling forward again. A rollback nobody has performed is not a rollback.
