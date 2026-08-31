# 2. The middleware authorizes the person, never the calling service

## Context

The MCP tool server calls this API on a customer's behalf. It could present its own
machine-to-machine credential and assert who the customer is, which is simpler and is
what most internal services do.

## Decision

The customer's own access token travels with the request in `Authorization`. The tool
server's credential goes in a separate header and proves only which service is calling.
A request carrying only a service credential is refused for anything touching customer
data.

## Alternatives considered

Trusting an asserted identity from a service credential. Rejected: it makes the tool
server's credential equivalent to every customer's, so one leaked secret is a total
compromise. It also makes this service's audit trail depend on another service telling
the truth.

Mutual TLS with an asserted identity header. Rejected for version one: it has the same
trust property as above, and adds certificate management.

## Consequences

Both services verify the same token, which costs one extra signature check per call and
is worth it. The Auth0 client grant for the tool server is empty, and a test asserts it
stays empty.

## Status

Accepted.
