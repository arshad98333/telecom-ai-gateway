# 1. Require a MongoDB replica set, even for a single node

## Context

The service needs two things from the database: writes that commit together with their
audit record and their outbox event, and a live feed of changes for the supervisor
console. A standalone `mongod` supports neither.

## Decision

Require a replica set in every environment, including a developer's laptop, where it is
a single-node set.

## Alternatives considered

Writing the outbox without a transaction and accepting occasional divergence. Rejected:
the divergence is exactly an approval that exists with no event, or an event for an
approval that was rolled back, and both are visible to a person.

Polling for changes instead of using change streams. Rejected: polling either wastes
queries or adds latency, and it still leaves the transaction problem unsolved.

## Consequences

Local setup is one `docker compose up` rather than `docker run mongo`. In exchange the
same code path runs everywhere, and the failure mode "works locally, loses events in
staging" cannot happen.

## Status

Accepted.
