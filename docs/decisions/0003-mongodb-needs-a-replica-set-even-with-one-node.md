# 3. MongoDB is deployed as a replica set, even with one node

## Context

Two things this system does are unavailable on a standalone `mongod`: multi-document
transactions, and change streams. The approval request and its outbox event must commit
together or not at all, and the supervisor's live queue is a change stream, not a poll.

A standalone mongod appears to work — it accepts connections, it serves reads, the test
suite that never opens a transaction passes — until the first write that needed to
commit atomically.

## Decision

Every deployment, including a laptop and CI, runs MongoDB as a replica set. One node is
fine; a set is not optional. `docker compose up -d mongo` initiates `rs0` in its own
healthcheck and reports healthy only once a primary has been elected.

## Alternatives considered

A standalone node locally and a set in production. Rejected: it means the two failure
modes that most need testing cannot be tested where the code is written, and it puts a
whole class of bug behind a deploy.

Application-level compensation instead of transactions — write the event, then the
document, and reconcile. Rejected: it turns every partial failure into a background job
that must itself be correct, for a guarantee the database already offers.

## Consequences

Waiting for the port is not enough anywhere: the port opens well before the set can
accept a transaction, so everything that starts MongoDB waits for a primary. The
MongoDB-backed tests need a real set, which is why they are deselected from the fast
suite and run in CI against an ephemeral one (see 0012).

## Status

Accepted.
