# 3. Account ownership fails closed and is never taken from the caller

## Context

The agent must not read or change an account that the authenticated identity is not
entitled to. The caller supplies the CX ID in the tool arguments, which means the
request itself cannot be trusted to say whose account it is.

## Decision

For a customer, ownership is settled locally by comparing the CX ID against the token
subject. For any other role, ownership is decided by a checker backed by the service
layer. When no checker is configured, the default implementation refuses everything.

## Alternatives considered

Trusting a claim in the token that lists accessible accounts. Rejected: the list goes
stale between token issue and use, and it grows without bound for an agent.

Defaulting to allow when no checker is configured, for developer convenience.
Rejected: a missing dependency must never widen access.

## Consequences

A misconfigured deployment refuses work rather than leaking data. The customer path
costs no network call, which keeps the common case inside the latency budget.

## Status

Accepted.
