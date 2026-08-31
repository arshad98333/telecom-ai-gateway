# Build Plan — Telecom MCP Tools Package (Project 1)

Status: approved scope, v1.0.0
Owner: Arshad
Source of truth: `project1.json` (what to build), `24x7.ai.json` (the operating SOP it must enforce),
`production-engineering-guidebook.md` (how it must be built), `release_doc.json` (how it ships).

---

## 1. What this package is, and what it deliberately is not

It **is** a security-enforcing tool gateway. It exposes exactly the seven week-one telecom tools over
MCP, and it refuses everything else. Every call passes an eight-stage validation pipeline before a
single byte reaches a backend, every accepted and rejected call lands in a tamper-evident audit log,
and every write is idempotent.

It **is not** the telecom business logic system, and it **never** touches MongoDB directly. It calls
the middleware API. That boundary is the whole point: validation, authorization and auditing stay
centralized in the service layer, and the package cannot become a second source of truth that drifts
from the first (project1.json design decisions 1 and 6).

Restricted tools (`change_service_plan`, `cancel_service`) exist in the catalogue as **declared but
non-executable**. They are visible to policy and to tests; they have no code path to a backend. A
restricted operation must not have an executable path until its approval control exists.

## 2. Architecture

```
                    voice agent / MCP client
                              |
              stdio  ────────┴──────── streamable HTTP (ASGI)
                              |
                     ┌────────▼────────┐
                     │  MCP transport  │  protocol only, no business logic
                     └────────┬────────┘
                              |
                     ┌────────▼─────────────────────────────┐
                     │  SECURITY KERNEL  (deny by default)  │
                     │  1 token   2 tenant   3 CX ID        │
                     │  4 ownership  5 role  6 permission   │
                     │  7 input schema       8 tool scope   │
                     └────────┬─────────────────────────────┘
                              |            every accept AND reject ──► audit (hash-chained)
                     ┌────────▼────────┐
                     │     DOMAIN      │  tool registry, policy, redaction, idempotency rules
                     │  no I/O at all  │  ← the part that runs in milliseconds and never flakes
                     └────────┬────────┘
                              | ports (our interfaces, our shapes)
        ┌──────────────┬──────┴───────┬──────────────┬────────────────┐
   TelecomBackend   TokenVerifier  IdempotencyStore  AuditSink      Clock/RNG
    fake | http      local | jwks    memory | redis   stdout | file  frozen in tests
```

Two rules give this its value. **Nothing skips a layer** — transport talks to the kernel, the kernel
talks to the domain, the domain talks to ports, adapters implement ports. And **every external system
sits behind an interface we defined**, with a real implementation and a fake, so the entire test
suite runs with the network off, no credentials, and no accounts.

### Why layered this way rather than "MCP handler calls the API"

The obvious shortcut — validate inside each tool handler — produces seven copies of the security
logic and seven chances to forget one. Here, a tool cannot execute without traversing the kernel,
because the registry hands back a callable that is already wrapped. Adding tool number eight inherits
every control for free. That is the difference between a demo and something a CISO signs.

## 3. Phasing (thin slice first, per guidebook Step 7)

| Phase | Delivers | Finish line |
|---|---|---|
| A | Repo skeleton, pinned toolchain, `make` targets, empty suite green | `make install && make test` passes on a clean checkout |
| B | Config + errors + structured logging + redaction + health | Empty env produces one message naming every missing variable, then exits |
| C | Frozen tool definitions for all 10 tools | Schemas are data, versioned, and diffable |
| D | Security kernel + audit | Cross-account call is denied before any backend call, and the denial is in the audit log |
| E | **Vertical slice**: `get_customer_account` end to end, fake backend, tested at every layer | One real journey works outside-in |
| F | Remaining 6 read/write tools, idempotency, retries, circuit breaker | 0 duplicate tickets under repeat calls |
| G | HTTP transport, container, CI, TestPyPI → PyPI, rollback drill | Built image starts and answers readiness; clean-env install verified |
| H | Docs, ADRs, cost model, honest gap list | A stranger is running it in 15 minutes |

Each phase lands as multiple small commits, test first, never one large dump.

## 4. Scaling design (what breaks first, and why it will not)

- **Stateless server.** All request state lives in the call; shared state lives in Redis. Horizontal
  scaling is `replicas: n` with no coordination. Sticky sessions are not required.
- **Async all the way down.** Tools are I/O-bound; a single worker handles thousands of concurrent
  waits. One shared `httpx.AsyncClient` with a bounded connection pool per process, created once at
  startup — not per request, which is the classic generated-code failure.
- **Bounded everything.** Max request body, max page size, max concurrent in-flight calls per process
  (semaphore), per-identity rate limit. A caller cannot exhaust the machine, because the limits are
  refusals, not hopes.
- **JWKS cached** with background refresh and a stale-if-error window, so an Auth0 outage does not
  become our outage and we do not fetch keys per request.
- **Circuit breaker per backend route.** When the middleware API is failing, we stop calling it,
  return the safe "temporarily unavailable; no action was completed" envelope, and let it recover —
  rather than retrying a struggling dependency into a full outage.
- **Backpressure over queuing.** At capacity we shed with a retryable error and a `Retry-After`,
  because a voice call has a five-minute case budget; a queued request that answers late is worse
  than a fast refusal.
- **The first thing to break under load** is the middleware API's own connection pool. That is why
  the breaker, the semaphore and the per-identity limit exist, and why the load test in Phase G
  measures the backend, not us.

## 5. Cost engineering (deliberate, not accidental)

Cost in this system is dominated by three things, in this order: model tokens spent by the calling
agent, voice minutes, and compute. This package can only influence the first and the third, so it
does both on purpose.

1. **Small tool schemas.** Every tool description and JSON schema is sent to the model on every turn.
   Verbose schemas are a permanent tax on every conversation. Descriptions are tight, enums are used
   instead of free text, and the permission-filtered listing means a customer's model context carries
   only the tools they can actually use — not all ten. Measured and asserted by a test that fails if
   the serialized catalogue exceeds a token budget.
2. **Lean tool responses.** Responses are projected to the fields the agent needs and redacted, not
   passed through. A raw account document is thousands of tokens; the projection is a few hundred.
   This is the single largest cost lever in the whole project and it lives here.
3. **Cache reads that are safe to cache.** `get_network_status` is shared across all customers in an
   area — cache it briefly. Per-customer reads are cached only within a single case, keyed by
   identity, never across identities.
4. **Compute.** Scale-to-zero-capable container, small CPU/memory request, no idle worker pool.
   Redis is the only always-on dependency and it is small.
5. **Cost is observed.** Metrics carry per-tool call counts and response sizes so the cost per
   resolved case is a number on a dashboard, not a surprise on an invoice.

## 6. Security posture

Deny by default at every stage; least privilege; tenant isolation enforced in the data path, not just
checked in a conditional; no direct database access; secrets never logged, never in errors, never
sent to the model. The SOP's never-send list (4-digit passcode, passwords, payment secrets, tokens)
is enforced by a redaction filter that runs on every log line and every tool response, with a test
that asserts 100% redaction of the classified fields. Audit records are hash-chained so a deletion or
an edit is detectable.

## 7. Risk register (from release_doc.json, with the control that answers it)

| Risk | Control in this build |
|---|---|
| Identity failure | JWKS verification, no caller-asserted identity, hard fail closed |
| Cross-account data exposure | Ownership check stage + tenant filter in the adapter + dedicated test |
| Incorrect tool execution | Frozen schemas, validation before execution, unknown tool rejected |
| Duplicate write actions | Mandatory idempotency key, 24h retention, replay returns the original result |
| Approval bypass | Restricted tools have no executable path in v1 |
| Vendor outage | Timeouts, bounded retries, circuit breaker, safe failure envelope |
| Rollback failure | Immutable digest-pinned image, promoted not rebuilt, rollback drilled in Phase G |

## 8. Definition of done for v1

The eight pass/fail checks in `project1.json` are automated tests with those exact names; the seven
sign-off journeys are an end-to-end test; coverage is at or above 95% with security and authorization
tests gating merge; the suite passes offline, in random order, from a clean checkout; the container
builds, starts and answers readiness; and the package installs clean from TestPyPI before it ever
reaches PyPI.

## 9. Open items requiring input

- Middleware API base URL, auth scheme and the response shape for each of the seven endpoints. Until
  those arrive, the HTTP adapter is written against a documented contract and exercised against a
  recorded-fixture stub; swapping in the real one is a config change plus a contract test.
- Auth0 tenant domain, audience and the claim names carrying tenant and CX ID.
