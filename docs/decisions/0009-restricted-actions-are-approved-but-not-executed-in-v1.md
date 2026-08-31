# 9. Version one approves restricted actions but executes none of them

## Context

The programme exists because an agent that can move money or change a contract without a
human is the risk. The approval machinery — request, evidence, authority check, audit —
is built and enforced. The executing side needs a finance integration and its
reconciliation, and neither is ready.

## Decision

No plan change, cancellation, contract change or ownership transfer executes in version
one. `refund:approve` exists and is enforced; the executing side stays dark.

## Alternatives considered

Shipping execution behind a feature flag. Rejected: a restricted operation with an
executable path and no reconciliation is exactly the risk the programme was set up to
avoid, and a flag is one config change away from being on.

Delaying the whole approval flow until execution is ready. Rejected: the approval trail
is the valuable part and it can be proven in production without moving a penny.

## Consequences

A customer can be told the outcome of an approval that has no financial effect yet, so
the wording of that outcome matters and is part of the SOP, not the code.

Lifting this is a deliberate decision with its own record, not a quiet flag flip. What
has to be true first: the finance integration, its reconciliation, and a test that
proves a rejected approval moves nothing.

## Status

Accepted.
