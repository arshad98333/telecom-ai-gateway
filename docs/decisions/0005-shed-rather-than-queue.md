# 5. Shed load rather than queue it

## Context

A voice case has a five-minute budget and a single tool call has ten seconds. Under
load the server must decide what to do with a request it cannot start immediately.

## Decision

Admission is a bounded semaphore with a short wait. Past it, the request is refused
with a retryable `overloaded` error rather than queued.

## Alternatives considered

An unbounded queue. Rejected: the queue grows exactly when the dependency is slowest,
and every request in it eventually answers after the agent has already given up. The
work is done and paid for and helps nobody.

A larger concurrency ceiling. Rejected as the primary answer: it moves the failure into
the middleware API's connection pool, where we cannot see or control it.

## Consequences

Under overload the agent gets a fast, honest refusal it can act on — hand over to a
human, or try again — instead of silence. The ceiling and the wait are configurable, and
both are defaults rather than numbers derived from a load test, which is recorded in the
README's known gaps.

## Status

Accepted.
