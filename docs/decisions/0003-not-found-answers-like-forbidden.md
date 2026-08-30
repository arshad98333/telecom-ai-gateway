# 3. "Not found" answers exactly like "not yours"

## Context

A customer asking for an account that does not exist, and one asking for an account that
exists but is not theirs, are different situations. The obvious API returns 404 and 403.

## Decision

Both return 403 with identical wording. The distinction exists in the audit trail, where
an investigator can see it, and nowhere a caller can observe.

## Alternatives considered

Honest status codes. Rejected: the difference is an oracle. A caller can enumerate which
customer references, tickets and approval requests exist by watching which refusal they
get, and for a telecom that is a list of who is a customer.

## Consequences

Debugging is slightly harder from the outside, which is the point. The audit trail and
the logs carry the real reason, and the correlation identifier links a caller's report
to it in one search.

## Status

Accepted.
