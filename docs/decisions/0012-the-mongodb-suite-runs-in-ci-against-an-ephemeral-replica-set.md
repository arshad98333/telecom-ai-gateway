# 12. The MongoDB suite runs in CI against an ephemeral replica set

## Context

Forty-three tests exercise the MongoDB adapter — the transactional outbox, the change
stream, the TTL and unique indexes. They need a real replica set (see 0003), so they are
deselected from the fast local suite. Deselected meant they ran when someone remembered,
which over a long enough period is the same as not running, on exactly the code paths
that cannot be reasoned about without executing them.

## Decision

A CI job runs the `-m mongo` suite on every pull request and on pushes to `main` and
`development`, against a replica set created for that job and destroyed with it. The job
stands MongoDB up with the repository's own `docker compose` service, so CI and a laptop
initiate the same set the same way.

## Alternatives considered

A shared long-lived CI cluster. Rejected: it needs credentials in CI, and a test that
leaves rubbish behind poisons the next run — which is the kind of failure that gets
diagnosed as flakiness and then ignored.

`mongodb-memory-server` or a mocked driver. Rejected: the behaviour under test *is* the
real server's — transaction semantics, change stream resumption, TTL eviction. A fake
that passes proves nothing.

Adding the suite to `make check`. Rejected: it would put Docker in the path of every
local test run, and the fast suite's value is that it is fast.

## Consequences

An empty selection fails the job rather than passing it: a renamed marker or a suite that
stops being collected is caught by `--strict-markers` and by an explicit check for
pytest's exit code 5. Without that, this job would report green for doing nothing, which
is worse than not having it.

`make test-mongo` runs the same suite locally against whatever `make up` gives you. The
job publishes its JUnit and coverage XML, and dumps the last 200 lines of the mongo
container's log when it fails, because "connection refused" without the server's side of
the story is a wasted rerun.

## Status

Accepted.
