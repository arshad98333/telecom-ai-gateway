# 7. Every state change writes an outbox event in the same transaction

## Context

Consumers — billing reconciliation, CRM, analytics, the supervisor's live queue — need
to know when something changed. Publishing after the write means a crash between the two
loses the event; publishing before means an event for a change that never committed.
Polling the database instead makes every consumer a source of load and of lag.

## Decision

Every state change writes an event into `outbox` in the same transaction as the change
itself. A relay drains it. A change stream on the collections that matter feeds the
supervisor's live queue over SSE, with the resume token persisted after each batch.

## Alternatives considered

Publishing to a broker inside the request. Rejected: it is a dual write, so it is either
lossy or duplicative, and it puts the broker's availability in the request path.

Consumers polling. Rejected: it trades a bounded amount of infrastructure for unbounded
read load and a lag floor nobody can lower.

## Consequences

A restart continues from the persisted resume token rather than replaying a day or
losing an hour. A dropped SSE connection reconnects with `Last-Event-ID` and is replayed
from the outbox, so the replay path and the live path are the same events.

This is the second thing that requires a replica set (see 0003). It also means the
outbox is on the write path: a failure to insert the event fails the business write,
which is the intended behaviour and needs to stay visible in the error, not swallowed.

Subscribers are authorized at subscribe time *and again per event* — a live feed is a
read path, and a scope change mid-session must take effect.

## Status

Accepted.
