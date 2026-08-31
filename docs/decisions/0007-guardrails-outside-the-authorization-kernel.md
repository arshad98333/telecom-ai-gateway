# 7. Guardrails live outside the authorization kernel

Date: 2026-08-31

## Status

Accepted.

## Context

The authorization kernel answers one question in eight fixed stages: may this identity
perform this action on this account. Its stage order is a security property, it is
documented in decision 0002, and changing it is a review-board conversation.

A second, different question kept arriving in review: is this call itself sane. A
correctly authenticated customer can send a ticket body that reads as an instruction to
the model. A correctly authorized agent can raise six refund approvals on one case in
four minutes. A correctly authorized response can come back carrying a token in a field
that redaction has never heard of. None of those is an authorization failure, and none
of them can be expressed as a scope.

The obvious place to put those checks was inside the kernel, as more stages. We did not.

## Decision

Guardrails are a separate package with a separate object, run by the executor either
side of the backend call, and they never import the security package.

The pipeline takes a `GuardedCall` - the tool spec, the serialized arguments, the
tenant, the subject, the case - rather than the kernel's `AuthorizedCall`. The
projection deliberately drops the raw token.

Input checks run cheapest and highest-volume first: rate limit, size and shape, unicode
safety, injection scan, business rules, action budget. The action budget runs last
because it is the only input check that records something.

A refusal returns a decision. It does not raise, does not log and does not touch
metrics; the executor owns all three, because the executor already owns every other
outcome.

## Consequences

Good. The two layers change at different speeds without dragging each other along.
Thresholds move with traffic; the kernel does not move. A guardrail cannot log a
credential, because it is never handed one. Every refusal is one audit record written
by the same code path as every other outcome, so the numbers add up.

Bad. There are now two places to look when a call is refused. The mitigation is that
the audit record names the layer and, for a guardrail, the stage and the rule, so the
question "which control refused this" is answered by the record rather than by reading
code.

Also bad. A guardrail runs after the kernel, so a rate-limited caller has still had
their token verified. We accepted that: verifying a signature is cheap next to the work
the rate limit is protecting, and doing it the other way round would mean applying a
per-identity control before we know who the identity is.

## Alternatives considered

**More kernel stages.** Rejected. It would mean editing a frozen security control every
time a threshold moved, and the review that a security control deserves is not the
review that a threshold change deserves.

**A middleware in front of the transport.** Rejected. The guardrails need the resolved
tool spec to know whether an action is irreversible, and that only exists after the
kernel has run.
