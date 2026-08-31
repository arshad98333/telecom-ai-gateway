# 4. Tenancy is the first field of every document and every index

## Context

Cross-tenant leakage is the failure this system cannot have. The usual defence is a
filter added at each call site and reviewed carefully, which works until the one query
where it is forgotten — and that query looks exactly like the others.

## Decision

`tenant_id` is the first field of every document and the first field of every index. The
repository layer takes the tenant as a required argument and builds the filter itself.
There is no query in the code that omits it, because there is no code path that can.

## Alternatives considered

A database per tenant. Rejected at this scale: connection and index overhead per tenant,
and migrations that have to fan out, for an isolation guarantee the compound key already
gives.

Filtering in a middleware layer above the repository. Rejected: it is still a filter
someone can route around, and it cannot make the index prefix right.

## Consequences

Every index is compound and tenant-first, so a query that omits the tenant does not
merely leak — it also cannot use an index, which makes the mistake loud. Repository
methods have a required argument that is tedious to pass and impossible to forget.

The audit chain, the idempotency keys and the TTL indexes all inherit the same shape.
Adding a collection means adding its tenant-first index in the same change; a test
asserts every repository method's declared index exists.

## Status

Accepted.
