# 2. Resolve the tool before verifying the token

## Context

The build order lists the pre-execution checks as token, tenant, CX ID, account
ownership, role, permission, input schema, tool scope. Taken literally, an unknown
tool name would be discovered only after a signature verification and an ownership
lookup had already been paid for.

## Decision

Run tool resolution first, then the listed stages in their listed order. Every stage
is still executed and still named in the audit record; only the position of the tool
check changes.

## Alternatives considered

Following the listed order exactly. Rejected: an unauthenticated caller could make us
do cryptographic work and a backend lookup by sending any tool name, which is a cheap
denial-of-service and a probing oracle.

Resolving the tool twice, once early for the cheap check and once late for the
contract. Rejected: two checks that can disagree are worse than one.

## Consequences

An unknown or blocked tool is refused in microseconds with no downstream work. The
audit record for that refusal names the `tool_scope` stage, so the deviation is
visible rather than implied.

## Status

Accepted.
