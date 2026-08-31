# 2. The middleware is the only writer to MongoDB, and holds the business rules

## Context

The MCP package is the voice agent's way into the system, and it would be faster in the
short run to let it read and write the database directly. `telecom-mcp/docs/decisions/0001`
already settled that the package holds tool access and enforcement rather than telecom
business rules. This is the other half of that: where the rules and the data then live.

## Decision

The middleware owns the database and is its only writer. Validation, authorization,
tenancy and auditing are enforced there, for every consumer — the voice agent through
MCP, the supervisor console, the security tooling, and anything added later.

## Alternatives considered

A shared data-access library used by both services. Rejected: a library enforces nothing
at runtime; a second consumer that skips a call skips the rule, and the audit record
with it.

Direct database access from MCP for reads only. Rejected: "reads only" is a property of
today's code, not of the credential, and the credential is what an attacker gets.

## Consequences

Two layers of authorization, and this is not duplication. The MCP kernel answers *may
this agent call this tool for this customer* from the caller's token; the middleware
answers *may this identity read or change this record* from its own view of assignments
and approval authority. Neither trusts the other's word, so the middleware stays safe if
the MCP server is compromised — the property that actually matters.

The cost is a network hop on every read and a second place to look when a call is
refused. The refusal reasons are therefore distinct by design, so a log line says which
layer said no.

## Status

Accepted.
