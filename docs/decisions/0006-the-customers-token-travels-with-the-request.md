# 6. The customer's token travels with the request; the service account is powerless

## Context

The MCP service needs to authenticate itself to the middleware. The obvious way is a
service credential with the scopes its tools need — which makes that one credential able
to read every customer record in the system, and makes a compromise of the tool server a
compromise of the data.

## Decision

The MCP service account authenticates only *itself*, by client credentials. The
customer's own token travels with every call. The middleware authorizes the human, not
the robot, and refuses a customer-data call that carries only a service token.

## Alternatives considered

A broadly-scoped service credential with the tool server enforcing per-customer access.
Rejected: it puts the entire tenancy guarantee in the process most exposed to a model's
output, and leaves the middleware unable to tell a legitimate call from a coerced one.

Minting a fresh per-customer token in the tool server. Rejected: the tool server would
need signing authority, which is the same problem wearing a different hat.

## Consequences

A compromised MCP service credential cannot read one customer record. The audit trail
names the human on every entry, because the human's token was on every call.

The cost is that token lifetime becomes an operational concern in the tool server: a
call made with a token that expires mid-journey fails, and the failure has to be legible
rather than looking like a middleware fault. It also means external test runs need a
token that both hops accept — one secret, one audience, minted together (see 0011).

## Status

Accepted.
