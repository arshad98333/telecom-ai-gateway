# 8. Money in minor units, timestamps in UTC, passcodes only as hashes

## Context

Three representation choices cause a disproportionate share of production incidents, and
all three are cheap to get right once and expensive to change later.

## Decision

Money is a 64-bit integer in minor units with the currency stored beside it. Timestamps
are UTC and named for what happened — `created_at`, `decided_at` — never a bare `date`.
The 4-digit account passcode is stored as an Argon2id hash with a per-customer salt,
verified in constant time, rate-limited per CX ID, and locked after repeated failures.

## Alternatives considered

Floats for money. Rejected for the usual reason, which is that they are wrong.
`Decimal128` was rejected too: correct, but it means a conversion at every boundary, and
a conversion someone will skip.

Local timestamps, or naive ones with a documented convention. Rejected: the convention
survives exactly as long as the person who wrote it.

Storing the passcode reversibly so it can be read back for support flows. Rejected
outright: it is never read back, never logged, and never sent to a model.

## Consequences

A 4-digit secret is weak by construction. What makes it acceptable is the controls
around it — attempt limits, lockout, and the fact that it only ever authenticates
alongside a CX ID the caller must already know — not the hash. Those controls are part
of the decision, not an implementation detail of it.

Money arithmetic is integer arithmetic, so a division needs an explicit rounding rule at
the point it happens.

## Status

Accepted.
