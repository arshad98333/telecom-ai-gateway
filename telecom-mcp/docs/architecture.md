# Architecture

## The shape

```
             ┌─────────────────────────────────────────────────────────┐
             │  api/          transports and the composition root       │
             │  cli · server · http_app · tokens · executor · container │
             └───────────────────────────┬─────────────────────────────┘
                                         │
             ┌───────────────────────────▼─────────────────────────────┐
             │  security/     the kernel nothing bypasses               │
             │  verifier · authorization · identity · audit             │
             └───────────────────────────┬─────────────────────────────┘
                                         │
             ┌───────────────────────────▼─────────────────────────────┐
             │  domain/       rules, no input or output whatsoever      │
             │  tools · schemas · permissions · errors · ports          │
             └───────────────────────────┬─────────────────────────────┘
                                         │  interfaces we defined
             ┌───────────────────────────▼─────────────────────────────┐
             │  adapters/     everything that touches the outside world │
             │  backend (fake | http) · idempotency (memory | redis)    │
             │  reliability: retry · backoff · circuit breaker          │
             └─────────────────────────────────────────────────────────┘

             observability/  redaction · logging · metrics · health
             cuts across every layer, and redaction is not optional
```

Nothing skips a layer. The transport talks to the executor, the executor to the kernel
and the domain, the domain to ports, adapters implement ports. A file in `domain/`
importing `httpx` would be a design error, and it is visible as one.

## How a call flows

1. **Transport** receives `tools/call`, takes the bearer token from the Authorization
   header (HTTP) or the environment (stdio), and generates a correlation identifier.
2. **Kernel** runs eight stages in order: tool scope, token, tenant, CX ID, account
   ownership, role, permission, input schema. The first failure ends the call, and the
   stage that decided it is named in the audit record. Ownership fails closed.
3. **Executor** deduplicates if the tool writes: the idempotency key is reserved, and a
   repeat either replays the stored result or is told the first call is still running.
4. **Admission** takes a slot from a bounded semaphore, or sheds with a retryable error.
   Shedding beats queuing when a voice case has a five-minute budget.
5. **Reliability** runs the backend call inside a total time budget with bounded,
   jittered retries — only when the catalogue declares the operation safe to repeat —
   behind a circuit breaker.
6. **Adapter** calls the middleware API with explicit timeouts and validates the
   response against our own schema before returning it.
7. **Projection** serialises and redacts, then the result is stored against the
   idempotency key if there is one.
8. **Recording** writes exactly one hash-chained audit record and updates metrics, on
   every path including the failures.

## Why the boundaries are where they are

**`domain` and `adapters` are separated** because business rules that touch no network
can be tested in milliseconds, forever, and everything else can be swapped out. That is
also what makes the offline test suite possible.

**Every external system is behind an interface we defined**, not the vendor's shape, so
the fake and the real implementation are genuinely interchangeable and a vendor change
is one file.

**The kernel is one object, not seven handler-level checks**, because seven copies of
security logic is seven chances to forget one. Adding an eighth tool inherits every
control for free.

**The composition root is the only place implementations are chosen**, so there is no
`if settings.backend == ...` anywhere else in the codebase.

**Time, randomness and identifiers are injected ports**, which is why retry, backoff,
breaker and expiry tests assert exact values instead of sleeping.

## What scales, and what breaks first

The server is stateless; shared state is Redis. Scaling is replica count, with no
coordination and no sticky sessions. Everything is async and I/O-bound, with one shared
HTTP client per process and a bounded pool.

The first thing to break under load is the middleware API's own capacity, not ours,
which is why the breaker, the semaphore and the response-size cap exist: they turn a
dependency's bad day into fast, safe refusals rather than a queue of doomed requests.

## Cost model

Three levers, in the order they matter:

1. **Tool descriptions and schemas** are sent to the model on every turn. They are kept
   terse and enum-heavy, the listing is filtered to what the caller may use, and a test
   fails the build if the serialised catalogue drifts past its token budget.
2. **Tool results are projections**, not pass-throughs. A raw account document is
   thousands of tokens; the projection is a few hundred, and every one of them is paid
   again on each subsequent turn of the conversation.
3. **Compute** is a small, scale-to-zero-capable container with no idle worker pool.
   Redis is the only always-on dependency.

Metrics carry per-tool call counts and durations, so cost per resolved case is a number
on a dashboard rather than a surprise on an invoice. Metric labels are restricted to a
fixed low-cardinality set, because an unbounded label set is its own bill.
