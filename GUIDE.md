# The guide

Everything you need to work on this system, in the order you need it. Start at the top;
each step assumes the one before it worked. If you only have five minutes, do step 1 and
step 2 and stop.

This replaces the twelve separate documents that used to live in three `docs/` folders.
What is still separate, and why: `docs/decisions/` (one file per decision, immutable —
they are the *why*, this is the *how*), `docs/brief/` (the specifications this was built
to satisfy, unedited), and each service's own `README.md` (what that service is, for
someone using it standalone).

**Contents**

1. [Before you start](#1-before-you-start)
2. [Run it](#2-run-it)
3. [What you just started](#3-what-you-just-started)
4. [Your first real request](#4-your-first-real-request)
5. [Where the code lives](#5-where-the-code-lives)
6. [Make a change](#6-make-a-change)
7. [The tests](#7-the-tests)
8. [The database](#8-the-database)
9. [Identity: from the dev secret to Auth0](#9-identity-from-the-dev-secret-to-auth0)
10. [How the security model actually works](#10-how-the-security-model-actually-works)
11. [Observability](#11-observability)
12. [Ship it](#12-ship-it)
13. [When you are paged](#13-when-you-are-paged)
14. [Testing against a deployed URL](#14-testing-against-a-deployed-url)
15. [Troubleshooting](#15-troubleshooting)
16. [Reference](#16-reference)

---

## 1. Before you start

Install three things. Nothing else.

| | Why | Check it worked |
|---|---|---|
| [uv](https://docs.astral.sh/uv/getting-started/installation/) ≥ 0.12.3 | Runs Python and installs dependencies from the lock files | `uv --version` |
| [Docker](https://docs.docker.com/get-docker/) ≥ 24 | MongoDB, and running the services the way production does | `docker --version` |
| GNU Make ≥ 4.3 | Every command in this guide is a make target | `make --version` |

On Windows, install uv with:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

then close and reopen the terminal. Make comes with [Git for Windows](https://gitforwindows.org/)
(use Git Bash), or `winget install ezwinports.make`.

You do **not** need: a MongoDB install (Docker provides it), an Auth0 account (there is a
local verifier), an Azure subscription, or any credentials at all. Everything in steps 1–7
runs offline.

Python itself is not in the list — uv downloads the right version.

---

## 2. Run it

Three commands from a clean clone.

```bash
git clone https://github.com/arshad98333/telecom-ai-gateway.git
cd telecom-ai-gateway
make setup
```

`make setup` copies each service's `.env.example` to `.env` (and leaves an existing `.env`
alone), generates **one** signing secret and writes it into both, installs both services
from their lock files, and then asks each one whether its configuration actually loads.
It is safe to run again at any time. You should end with:

```
Configuration
  ok   telecom-mcp: configuration loads
  ok   telecom-middleware: configuration loads

Ready.
```

Then start everything:

```bash
make demo
```

That brings up MongoDB as a single-node replica set, both services, and loads the demo
data. When it finishes:

```bash
curl -s localhost:9000/readyz     # the API
curl -s localhost:8080/readyz     # the tool server
make down                         # stop everything
```

Both should answer `{"status": "healthy", ...}`.

### Without Docker

The middleware falls back to an in-memory store, so you can skip the database entirely:

```bash
make setup
make dev          # prints the two commands, one per terminal
```

Terminal one runs the API on `:9000`, terminal two the tool server on `:8080`. Data
disappears when you stop them, which for reading the code is a feature.

### If something went wrong

`make setup` prints the actual error from the service that refused to start. Both services
exit with code **78** on a configuration problem and name *every* problem at once rather
than the first one. Section 15 has the specific symptoms.

---

## 3. What you just started

Two services and a database. A voice agent talks to the first; only the second touches
data.

```
 voice agent
     │  Authorization: Bearer <the customer's token>
     ▼
 telecom-mcp          :8080/mcp/     verifies the token
 the tool server                     decides whether this caller may call this tool
                                     holds no business rules, never touches the database
     │  Authorization: Bearer <the same token, forwarded unchanged>
     │  X-Service-Authorization: Bearer <the tool server's own credential>
     │  X-Correlation-Id: <the same id, so one trace crosses both services>
     ▼
 telecom-middleware   :9000/api/v1   verifies the token again
 the API                             checks the permission, then checks the record
                                     holds the rules and the data; the only writer
     ▼
 MongoDB (replica set) ──change stream──► SSE, live, to every supervisor watching
```

`telecom-mcp-client/` sits where "voice agent" sits: it is a caller of `telecom-mcp`,
not a step in the path above. It exists so driving the tool server from a terminal or a
script does not need a voice stack, and so the handshake, the trailing slash and the
retry rules are solved once. See `telecom-mcp-client/README.md`.

Three things about this are deliberate and worth knowing on day one.

**One token, verified twice.** The tool server does not mint a token for the customer; it
forwards the one it was given. So both services must agree on the issuer, the audience,
the signing keys and the claim names. Getting this wrong is the single most common way to
break the system — see [section 9](#9-identity-from-the-dev-secret-to-auth0).

**The tool server's own credential is powerless.** It travels in a *separate* header and
proves only *which service* is calling. A request carrying only that credential is refused
for anything touching customer data. A stolen service credential reads nobody's account.
(`docs/decisions/0006`, `telecom-middleware/docs/decisions/0002`.)

**Two layers of authorization is not duplication.** The tool server answers *may this
agent call this tool for this customer*. The middleware answers *may this identity read or
change this record*. Neither trusts the other's word, so the middleware stays safe if the
tool server is compromised. (`docs/decisions/0002`.)

### The moving parts

| Path | What it is |
|---|---|
| `telecom-mcp/` | The tool server. Ten tools, eight of them live in v1. Port 8080. |
| `telecom-middleware/` | The API. Port 9000, everything under `/api/v1`. |
| `telecom-mcp-client/` | A reference/ops MCP client for `telecom-mcp`'s streamable-HTTP endpoint: a typed library plus a small CLI. Not in the request path — a caller, like a voice agent. |
| `infra/auth0/` | The Auth0 tenant as Terraform: the API, its scopes, four roles, the login Action. |
| `e2e/` | Both services in one process over real HTTP, nothing stubbed between them. |
| `testsprite/` | The external suite that runs from someone else's cloud against a deployed URL. |
| `docs/decisions/` | Why. One numbered file per decision. |
| `docs/brief/` | The specifications this was built to satisfy. |

### The data

One database, one collection per aggregate. `tenant_id` is the first field of every
document and the first field of every index — there is no cross-tenant query path because
the repository layer takes the tenant as a required argument and builds the filter itself.

| Collection | Key | What is in it |
|---|---|---|
| `customers` | `(tenant_id, cx_id)` | Account record, status, passcode **hash** |
| `services` | `(tenant_id, cx_id, service_id)` | Active services and plans |
| `orders` | `(tenant_id, cx_id, order_id)` | Order state and history |
| `invoices` | `(tenant_id, cx_id, invoice_id)` | Billing summaries |
| `network_status` | `(tenant_id, area_ref)` | Area incidents, shared across customers |
| `agent_assignments` | `(tenant_id, agent_sub)` | Which accounts an agent may touch |
| `tickets` | `(tenant_id, ticket_id)` | Support tickets |
| `callbacks` | `(tenant_id, callback_id)` | Scheduled callbacks |
| `approval_requests` | `(tenant_id, request_id)` | Restricted actions awaiting a human |
| `cases` | `(tenant_id, case_id)` | Voice case state, for resume after a dropped call |
| `audit_records` | `(tenant_id, seq)` | Hash-chained, append only |
| `outbox` | `(_id)` | Events awaiting relay |
| `idempotency_keys` | `(tenant_id, scope, key)` | Write deduplication, TTL 24h |

Every read path in the API maps to exactly one compound index, tenant first. The set is
asserted by a test: every repository method declares the index it relies on, and the test
fails if the collection does not have it. A query the code can express but no index serves
is a latency incident waiting for a busy Tuesday.

```
customers          {tenant_id: 1, cx_id: 1}                        unique
services           {tenant_id: 1, cx_id: 1, status: 1}
orders             {tenant_id: 1, cx_id: 1, placed_at: -1}
invoices           {tenant_id: 1, cx_id: 1, issued_on: -1}
network_status     {tenant_id: 1, area_ref: 1}                     unique
agent_assignments  {tenant_id: 1, agent_sub: 1, cx_id: 1}          unique
tickets            {tenant_id: 1, cx_id: 1, created_at: -1}
                   {tenant_id: 1, state: 1, created_at: -1}        agent queue
approval_requests  {tenant_id: 1, state: 1, created_at: 1}         supervisor queue
                   {tenant_id: 1, cx_id: 1, created_at: -1}
cases              {tenant_id: 1, case_id: 1}                      unique
                   {tenant_id: 1, cx_id: 1, status: 1}             resume lookup
audit_records      {tenant_id: 1, seq: 1}                          unique, chain order
                   {tenant_id: 1, correlation_id: 1}
idempotency_keys   {tenant_id: 1, scope: 1, key: 1}                unique
                   {expires_at: 1}                                 TTL, expireAfterSeconds 0
outbox             {status: 1, created_at: 1}                      relay scan
```

Three representation rules you will trip over if you do not know them:

- **Money is a 64-bit integer in minor units**, with the currency beside it. `total_minor: 6300` is £63.00. Never a float. The tool server converts to `"total": "63.00"` on the way out — that is `adapters/translation.py` doing its job, not a bug.
- **Timestamps are UTC**, named for what happened (`created_at`, `decided_at`), never a bare `date`.
- **Passcodes are never stored.** The four-digit passcode is an Argon2id hash with a per-customer salt, verified in constant time, rate-limited and locked out after repeated failures. Never read back, never logged, never sent to a model.

### The demo data

`make demo` (or `make seed`) loads thirteen documents:

| | |
|---|---|
| `CX-1234` | Active consumer. Two services, one dispatched order, £63.00 due. |
| `CX-5555` | Suspended business account. £410.00 overdue. |
| `APR-seed-0001` | A pending refund waiting for a supervisor. |
| agent `auth0\|agent-7` | Assigned to CX-5555 **only** — which is what makes the refusal demo work. |

**The passcode for both demo customers is `4821`.**

---

## 4. Your first real request

The API wants a token on every call. In development you mint your own — no Auth0 account,
no login.

```bash
cd telecom-mcp
make token                    # prints a JWT, valid for one hour
```

Keep it in a variable:

```bash
export TOKEN=$(make token)                      # bash
$env:TOKEN = (make token)                       # PowerShell
```

Now, with the stack running (`make demo`, or `make dev` in two terminals):

**1. Read an account.**

```bash
curl -s -H "Authorization: Bearer $TOKEN" localhost:9000/api/v1/customers/CX-1234
```

**2. Read the invoices.** Note `total_minor: 6300`, not `63.0`.

```bash
curl -s -H "Authorization: Bearer $TOKEN" localhost:9000/api/v1/customers/CX-1234/invoices
```

**3. Do the passcode check the voice agent does.**

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"cx_id":"CX-1234","passcode":"4821"}' \
  localhost:9000/api/v1/customers/CX-1234/authenticate
```

**4. Try someone else's account.** This is the interesting one.

```bash
curl -s -H "Authorization: Bearer $TOKEN" localhost:9000/api/v1/customers/CX-5555
```

You get a **403 whose wording is identical to "no such customer"**. That is deliberate:
telling the two apart would be an enumeration oracle — ask for a thousand ids and the
error messages tell you which customers exist. The distinction lives in the audit trail,
where it belongs. (`telecom-middleware/docs/decisions/0003`.)

**5. Raise a ticket, then send exactly the same request again.**

Same `ticket_id` comes back, with `deduplicated: true`, and there is still one ticket in
the database. Every write takes an idempotency key and goes through one path
(`api/idempotent.py`) so no endpoint can invent its own deduplication.

**6. Ask for a refund.**

You get `202`, `state: pending`, `money_moved: false`. Nothing has moved and nothing will
until a human decides. Now become that human:

```bash
cd telecom-mcp
export SUP=$(uv run --env-file .env python scripts/mint_dev_token.py --role supervisor_approver)

curl -s -H "Authorization: Bearer $SUP" localhost:9000/api/v1/approvals
curl -s -X POST -H "Authorization: Bearer $SUP" -H "Content-Type: application/json" \
  -d '{"decision":"approved","note":"Outage confirmed."}' \
  localhost:9000/api/v1/approvals/APR-seed-0001/decision
```

Decide it a second time and you get **409**. Approvals are decided once.

**7. Watch it happen live.** Open a third terminal *before* you send the decision:

```bash
curl -N -H "Authorization: Bearer $SUP" localhost:9000/api/v1/stream
```

The event arrives in milliseconds — a change stream, not a poll. Notice the body carries a
reference like `ref_8ab337…` rather than the customer's identifier, because it is fanned
out to every supervisor watching that tenant.

**Interactive API docs:** <http://127.0.0.1:9000/docs>.

### The whole approval journey, in six steps

This is the path worth holding in your head, because most of the system exists to make it
safe:

1. Customer asks for a refund. The voice agent calls `request_refund_approval`.
2. The tool server validates, deduplicates, and POSTs to the middleware **with the customer's token**.
3. The middleware writes the `approval_requests` document *and* an `outbox` event **in one transaction**. Nothing has moved.
4. A change stream picks up the insert and pushes it to every supervisor watching that tenant.
5. A supervisor decides. That write checks approval *authority* — a supervisor may not approve their own request, nor one above their limit — and records the decision with the evidence they saw.
6. The customer is told on the next turn or by callback. Request, evidence, approver, timestamp and decision are one audit trail.

### What v1 refuses to do

No plan change, cancellation, contract change or ownership transfer executes. `refund:approve`
exists and is enforced, but the executing side is dark until the finance integration and
its reconciliation exist. A restricted operation with an executable path and no approval
control is the exact risk this programme was set up to avoid. (`docs/decisions/0009`.)

---

## 5. Where the code lives

Both services have the same shape: `api/` at the edge, `domain/` in the middle with no
dependencies, adapters or repositories at the back.

### telecom-mcp — `src/telecom_mcp/`

| Package | What it owns |
|---|---|
| `api/` | `cli.py` entry point · `container.py` the composition root, the one place implementations are chosen from configuration · `executor.py` **the one path a tool call takes; nothing executes any other way** · `http_app.py` MCP over HTTP plus the operational endpoints · `server.py` protocol only · `tokens.py` where the bearer token comes from, per transport |
| `security/` | `authorization.py` **the security kernel** · `audit.py` the hash-chained trail · `identity.py` · `service_token.py` this service's own credential and its refresh · `verifier.py` two verifiers behind one interface, chosen by config |
| `guardrails/` | `pipeline.py` (order is the design) · `policy.py` every threshold in one frozen object · `rate_limit.py` · `limits.py` · `unicode_safety.py` · `injection.py` · `business.py` · `budget.py` · `output.py` |
| `domain/` | `tools.py` the frozen v1 catalogue · `schemas.py` the frozen contracts · `permissions.py` **scopes, roles, risk classes — edit here** · `errors.py` · `ports.py` |
| `adapters/` | `http_backend.py` the real middleware · `fake_backend.py` built from committed fixtures · `translation.py` middleware shapes → v1 contract · `idempotency.py` · `reliability.py` retry, backoff, circuit breaker |
| `observability/` | `health.py` · `metrics.py` · `kpi.py` · `slo.py` · `logging.py` · `redaction.py` · `tracing.py` |
| `config/settings.py` | All configuration, loaded and validated once |

**The tools.** Eight live, two blocked. A blocked tool has no executable path, and the
listing a caller receives contains only the tools their identity may invoke.

| Tool | Scope | Class |
|---|---|---|
| `get_customer_account` | `account:read` | read |
| `get_active_services` | `service:read` | read |
| `get_order_status` | `order:read` | read |
| `get_invoice_summary` | `billing:read` | read |
| `get_network_status` | `network:read` | read |
| `create_support_ticket` | `ticket:write` | low-risk write, idempotency key required |
| `schedule_callback` | `callback:write` | low-risk write, idempotency key required |
| `request_refund_approval` | `refund:request` | restricted, needs supervisor approval |
| `change_service_plan` | `service:change` | **blocked in v1** |
| `cancel_service` | `service:cancel` | **blocked in v1** |

### telecom-middleware — `src/telecom_middleware/`

| Package | What it owns |
|---|---|
| `api/` | `app.py` routing, error translation, request lifecycle · `asgi.py` the importable app · `container.py` · `context.py` · `dependencies.py` request-scoped identity and permission · `idempotent.py` **one write path** · `routes/` — `health`, `customers`, `support`, `approvals`, `cases`, `admin`, `stream` |
| `security/` | `verifier.py` Auth0 plus a local dev verifier · `principal.py` who is calling · `service_credential.py` which *service* is calling · `permissions.py` · `access.py` **ownership and authority — which records this identity may touch** |
| `repositories/` | `ports.py` interfaces · `memory.py` · `mongo.py` · `schema.py` collections, indexes and validators as code · `session.py` |
| `domain/` | `models.py` · `events.py` · `money.py` integer minor units · `errors.py` rendered as RFC 9457 problem details |
| `services/` | `passcode.py` hashing, verification, lockout · `recording.py` audit record and event written together · `seed.py` · `diagnostics.py` |
| `realtime/` | `broker.py` fan-out with per-event authorization · `relay.py` the outbox relay |
| `observability/` | `logging.py` · `redaction.py`, applied as a processor so no call site can forget it |

Five invariants the middleware holds and the tests enforce: tenancy is a type signature;
permission and ownership are separate checks; every refusal returns identical wording;
writes, audit records and events commit together; the passcode is never stored.

---

## 6. Make a change

The loop is short:

```bash
make check          # lint, types, tests, coverage — exactly what CI runs
```

If that passes, CI passes. It runs both services. To work on one only:

```bash
make -C telecom-mcp check
```

Faster inner loop while you are actually editing:

```bash
cd telecom-middleware
uv run pytest tests/unit -q          # a second or two
make test-fast                       # unit + contract, no containers
make format                          # fix style automatically
```

### Where to put things

| Change | Where |
|---|---|
| A new tool, or what a tool accepts | `telecom-mcp/src/telecom_mcp/domain/tools.py` and `schemas.py` — **this is a contract change, see versioning in [section 12](#12-ship-it)** |
| A scope, a role, what a role may hold | `domain/permissions.py` in whichever service — and `infra/auth0/` if the scope is new |
| A business rule, an endpoint, anything touching data | `telecom-middleware/` |
| A guardrail threshold | an environment variable; the defaults are the strict posture |
| A new endpoint | `telecom-middleware/src/telecom_middleware/api/routes/` — **it must declare its scope**, and a test enumerates the routes and fails if any does not |

A non-obvious choice gets a decision record: `make adr` prints the next number, five
headings, written while the reasoning is fresh.

### Reading the code with a debugger

The fastest way to understand the authorization model is to stop inside it.

**In VS Code**, open the *workspace file*, not the folder:

> **File → Open Workspace from File…** → `telecom.code-workspace`

You get four folders — platform, middleware, tools, end-to-end. This matters: the two
services have separate virtual environments, and opening the parent folder makes VS Code
pick one interpreter for everything and then report unresolved imports in whichever
service it did not pick. Install the recommended extensions when prompted (Python, Ruff,
Mypy, MongoDB, REST Client), then open a `.py` file in each service folder in turn and run
**Python: Select Interpreter**, choosing that folder's own `.venv`. *That one step is the
most common reason the editor looks broken while the code is fine.*

Then:

1. Open `telecom-middleware/src/telecom_middleware/security/access.py`.
2. Breakpoint on the first line of `require_account_access`.
3. Run **API: run (breakpoints work)** and send a request.
4. In **Variables**, expand `principal`: `role`, `cx_id`, `scopes` — already narrowed to
   what the role may hold, which is why a token minted with too much cannot exceed it.
5. Change the customer id to `CX-5555` and send it again. Same breakpoint, different
   outcome, and you can see the exact line that refuses it.

Two other breakpoints worth your time: `api/idempotent.py` in `idempotent_write` (send the
ticket request twice and watch the second take the replay branch), and
`services/recording.py` in `Recorder.audit` (the hash chain being extended, and the
customer reference being replaced before anything is stored).

> **Use "API: run (breakpoints work)", not "API: run with reload".** Reload restarts the
> app in a *child* process while the debugger stays attached to the parent, so breakpoints
> never hit. This costs everyone an afternoon exactly once.

`telecom-middleware/requests.http` has every endpoint in the order a real case uses them,
with the expected result in a comment. The **Mint a dev token** task writes `DEV_TOKEN`
into `.env` and `requests.http` reads it, so there is nothing to paste and nothing to
accidentally commit.

### Before you open a pull request

The change itself: one concern per commit, with its test; every non-obvious choice has a
decision record; no new public surface without a docstring saying what it refuses.

Tests: `make check` green; a new refusal path has a test that proves it refuses; a new
endpoint declares its scope and the route test passes; coverage has not dropped below the
95% floor.

Operations: if you added a metric, an objective or an alert, `make observability` has been
run and the output committed; if you changed what a tool accepts or returns, the contract
version is bumped ([section 12](#12-ship-it)); if you touched configuration, `.env.example`
carries the new variable *with the reason it exists*.

---

## 7. The tests

```bash
make test          # both services, everything that runs without a database
make check         # the above plus lint, types and the coverage gate
```

Expected: **649 passed** in `telecom-mcp` (about five seconds), **374 passed, 43
deselected** in `telecom-middleware` (about thirty). If your numbers are higher, someone
added tests; if they are lower, something is not being collected.

### Why 43 are deselected

Those tests need a real MongoDB replica set. They are **deselected, not skipped** — a
deselection is visible in the run header, a skip is an invisible failure. To run them:

```bash
make up            # a single-node replica set in Docker
make test-mongo
```

CI runs them on every pull request against a replica set it creates and throws away
(`.github/workflows/mongo.yml`). Each test gets its own database and drops it afterwards.
If they are selected without `TELECOM_MW_MONGODB_URI` set, the fixture calls `pytest.fail`
rather than skipping.

### Layout

| | `telecom-mcp` | `telecom-middleware` |
|---|---|---|
| `tests/unit/` | 46 files | 14 files |
| `tests/integration/` | 4 files | 7 files |
| `tests/contract/` | 1 file | 1 file, parameterised over both stores |
| Marker | `integration` | `mongo` |
| Coverage floor | 95% | 95% (the Mongo adapter is excluded here and covered by `make cov-mongo`) |

Both suites run in **random order** every time, treat warnings as errors, and use
`--strict-markers`. The `telecom-mcp` suite needs no database, no container, no
credentials, no `.env` and no network — and no test is skipped for missing configuration.

Two tests are worth knowing about because they fail in ways that look like someone else's
problem:

- `telecom-middleware/tests/unit/test_auth0_parity.py` reads the Terraform in `infra/auth0`
  and **fails** — does not skip — if the scopes or role bundles have drifted from the
  service's own definitions. From a standalone clone: `TELECOM_INFRA_DIR=/path/to/infra/auth0 make test`.
- `make check` in `telecom-mcp` includes `observability-check`, which regenerates the
  dashboards and alert rules and fails if the committed copies are stale. Fix with
  `make observability` and commit the result.

---

## 8. The database

Three options, in increasing order of effort. Pick the smallest one that does what you need.

| | In-memory | Local Docker | Atlas |
|---|---|---|---|
| Setup | none | ~2 minutes | ~20 minutes |
| Works offline | yes | yes | no |
| Data survives a restart | no | yes (`mongo-data` volume) | yes |
| Good for | reading the code, unit tests | everything, day to day | sharing data, staging |

**Whichever you choose, it is a replica set.** Transactions and change streams are both
replica-set features, and this system uses both — the transactional outbox and the
supervisor's live feed. A standalone `mongod` passes a connection test and then fails on
the first write that must commit atomically, which is a much worse place to find out.
(`docs/decisions/0003`.)

### In-memory

The default. `TELECOM_MW_STORE=memory`, nothing to install.

### Local Docker

```bash
make up                                    # from the repo root
cd telecom-middleware
uv run python scripts/check_mongo.py --local
```

The compose file starts `mongo:7.0` with `--replSet rs0`, and its healthcheck initiates
the replica set on first boot and waits for a primary to be elected. You should see:

```
  ok  ping answered in 3 ms
  ok  MongoDB 7.0.x
  ok  replica set 'rs0', connected to the primary
  ok  transactions and change streams available
  ok  read and write on 'telecom'
```

Then point the middleware at it — set this in `telecom-middleware/.env`:

```dotenv
TELECOM_MW_STORE=mongodb
TELECOM_MW_MONGODB_URI=mongodb://localhost:27017/?replicaSet=rs0&directConnection=false
```

and load the schema and data:

```bash
make migrate       # collections, validators, indexes
make seed          # the demo dataset
make check-store   # everything should now pass
```

`make down` stops it; `docker compose down -v` also wipes the data.

### Atlas

**Step 1 — create the cluster.** Sign in at <https://cloud.mongodb.com> → **Create** →
tier **M0** (free forever) → nearest region → name it (`cluster0` is fine; **the name
cannot be changed later**) → **Create Deployment**. Ready in under a minute. M0 is a real
three-node replica set, so transactions and change streams both work. Limits: 512 MB, 500
connections, ~100 ops/sec, and **it pauses after 30 days with no connections** — one click
resumes it.

**Step 2 — create the database user.** **Database Access** → **Add New Database User** →
Password auth → username `telecom_app` → **Autogenerate Secure Password** → **Copy it now,
Atlas will not show it again** → **Specific Privileges** → `readWrite` on database
`telecom` → **Add User**.

Not `atlasAdmin`, which the quickstart offers in one click: a credential that can drop
every database in the project should not sit in a `.env` on a laptop. `readWrite` on one
database is all this service ever does.

**Step 3 — allow your network in.** **Network Access** → **Add IP Address** → **Add
Current IP Address**. If you move around or use a VPN, `0.0.0.0/0` works and means *anyone
on the internet may attempt to authenticate* — fine for a dev cluster with a strong
generated password, **never for one holding real customer data**. Atlas lets you set an
expiry on the entry; use it. Changes take about a minute to apply.

**Step 4 — copy the connection string.** **Connect** → **Drivers** → Python. Replace
`<db_password>` **including the angle brackets**. If the password contains punctuation,
percent-encode it:

| character | write it as | | character | write it as |
|---|---|---|---|---|
| `@` | `%40` | | `/` | `%2F` |
| `:` | `%3A` | | `?` | `%3F` |
| `#` | `%23` | | `%` | `%25` |

```bash
python -c "from urllib.parse import quote_plus; print(quote_plus(input()))"
```

Easier: regenerate the password until it is letters and digits. **The failure mode that
wastes the most time here is that the authentication error blames the *username*.**

**Step 5 — point the middleware at it** in `telecom-middleware/.env`:

```dotenv
TELECOM_MW_STORE=mongodb
TELECOM_MW_MONGODB_URI=mongodb+srv://telecom_app:<encoded-password>@cluster0.ab12cde.mongodb.net/?retryWrites=true&w=majority&appName=cluster0
```

**Step 6 — apply the schema and data:**

```bash
cd telecom-middleware
make check-store     # reachable, a replica set, indexed?
make migrate
make seed
make check-store     # all PASS now
```

With only `mongosh` and no Python, the same result:

```bash
mongosh "mongodb+srv://telecom_app:<password>@cluster0.ab12cde.mongodb.net/telecom" --file scripts/seed.mongodb.js
```

Note the **`/telecom` on the end of the URI** — it selects the database the script writes
to. It creates every collection with its validator, all 30 indexes and the 13 demo
documents, and is safe to run twice. `scripts/seed.mongodb.js` is **generated** from the
same code the Python seeder uses — do not edit it by hand, regenerate with
`make export-seed`.

### `check_mongo.py`, and why it exists

```bash
cd telecom-middleware
uv run --env-file .env python scripts/check_mongo.py
```

It depends on nothing but `pymongo`, deliberately: the moment you need it is the moment the
project itself will not start. It answers four questions in order, and each one is a
failure that otherwise looks like the others:

1. **Does the hostname resolve?** For `mongodb+srv://` this is an *SRV lookup*, not an ordinary one — Atlas cluster names have no A record, so pinging the hostname fails even when the cluster is perfectly healthy.
2. **Does a server answer?**
3. **Is it a replica set?** See above.
4. **Can this credential actually write to `telecom`?** Read permission is not write permission, and Atlas hands out the read-only one by default.

The naive three-line `MongoClient` + `ping` snippet fails all three of these tests: it puts
the password in a source file, it succeeds against a standalone with no transactions and
against a read-only credential, and wrong password, missing IP entry and paused cluster all
surface as "it did not connect".

### Adding your own customer

The passcode is the one part you cannot do by hand — mongosh cannot produce an Argon2id
hash:

```bash
cd telecom-middleware
uv run telecom-middleware hash-passcode 4821
```

Paste the `$argon2id$…` value into `passcode.hash` in
`playgrounds/02-add-a-customer.mongodb.js`, adjust the other fields, and run the playground
(▶ in VS Code with the MongoDB extension). **A placeholder left in `passcode.hash` produces
an account that exists and can never authenticate** — a confusing hour, avoided by one
command.

The other two playgrounds are worth a look: `01-explore.mongodb.js` (counts, the two
customers, money in pennies, the pending approval) and `03-why-the-indexes-matter.mongodb.js`
(`explain()` on the real read paths, and the audit chain).

---

## 9. Identity: from the dev secret to Auth0

There are two ways to verify a token, and switching between them is four environment
variables. Nothing else in the system changes.

| | `local` | `jwks` |
|---|---|---|
| Algorithm | HS256, one shared secret | RS256, Auth0's published keys |
| Who mints tokens | `scripts/mint_dev_token.py` | Auth0, on a real login |
| Allowed in production | **no** — the settings validator refuses it | yes |

### The rule that breaks everything when you get it wrong

**Both services verify the same token, so both must agree.** The tool server forwards the
caller's token unchanged; the middleware verifies it again.

| telecom-mcp | telecom-middleware | Identical? |
|---|---|---|
| `TELECOM_MCP_JWT_ISSUER` | `TELECOM_MW_JWT_ISSUER` | **yes** |
| `TELECOM_MCP_JWT_AUDIENCE` | `TELECOM_MW_JWT_AUDIENCE` | **yes — one Auth0 API in front of both** |
| `TELECOM_MCP_JWKS_URL` | `TELECOM_MW_JWKS_URL` | **yes** |
| `TELECOM_MCP_LOCAL_VERIFIER_SECRET` | `TELECOM_MW_LOCAL_VERIFIER_SECRET` | **yes, in local mode** |
| claim names (compiled into `security/verifier.py`) | `TELECOM_MW_CLAIM_NAMESPACE` | **yes** |
| `TELECOM_MCP_BACKEND_BASE_URL` | `TELECOM_MW_HTTP_HOST`/`_PORT` + `/api/v1` | must point at it |
| `TELECOM_MCP_JWKS_CACHE_TTL_S` | `TELECOM_MW_JWKS_CACHE_TTL_S` | no |

Two details that are easy to get wrong and hard to spot:

- **The issuer keeps its trailing slash. The JWKS URL does not.**
  `https://your-tenant.eu.auth0.com/` and `https://your-tenant.eu.auth0.com/.well-known/jwks.json`.
- **Creating two Auth0 APIs, one per service, is the single most common way to break this.**
  An access token carries exactly one audience. Two APIs means the token verifies at one
  hop and is rejected at the other. One API, both services.

`make setup` handles the local-mode case for you — it generates one secret and writes it
into both files.

### Setting up the tenant

The Auth0 tenant is Terraform: the API and its seventeen scopes, the four roles, the two
applications, and the post-login Action that puts tenant, customer reference and role into
the token. Do not click it together in the dashboard — a test
(`test_auth0_parity.py`) will fail if what is in Auth0 drifts from what the code expects.

State is a file. By default it stays in `infra/auth0/`, which is gitignored, and that is
all a single operator needs:

```bash
cd infra/auth0
cp backend_local_override.tf.example backend_local_override.tf
```

If more than one person will run `terraform apply` against the same tenant, the state has
to live somewhere both of them can lock. Any Terraform backend does that, and `envs/*.backend`
is where the details go; the repository ships an `azurerm` block as one worked example.
Nothing else in this system needs a cloud account, and nothing in CI touches Terraform at
all.

Then apply:

```bash
cd infra/auth0
cp envs/dev.tfvars.example envs/dev.tfvars        # fill it in
cp envs/dev.backend.example envs/dev.backend      # fill it in
export TF_VAR_auth0_management_client_id=...
export TF_VAR_auth0_management_client_secret=...
terraform init -backend-config=envs/dev.backend
terraform plan  -var-file=envs/dev.tfvars
terraform apply -var-file=envs/dev.tfvars
```

No Azure subscription and only a throwaway tenant? Copy
`backend_local_override.tf.example` to `backend_local_override.tf` and `terraform init`
with no backend config; state stays in that directory, gitignored. Never for staging or
production.

### Wiring the outputs into both services

```bash
make wire-auth0              # writes the values, keeps the local verifier
make wire-auth0-activate     # writes them and switches both services onto Auth0
```

That reads `terraform output` and edits only the identity lines in each `.env`, leaving
every other line alone. The client secret comes from `TELECOM_MCP_CLIENT_SECRET` in your
environment, never from Terraform state. Doing it by hand is where the issuer loses its
trailing slash.

The four values it reads:

```bash
terraform output -raw issuer          # TELECOM_MW_JWT_ISSUER
terraform output -raw jwks_url        # TELECOM_MW_JWKS_URL
terraform output -raw api_identifier  # TELECOM_MW_JWT_AUDIENCE
terraform output -raw claim_namespace # TELECOM_MW_CLAIM_NAMESPACE
```

### Demo users, and a real token

Terraform builds the tenant's *shape*, not people. For dev and staging only:

```bash
cd infra/auth0
export AUTH0_DOMAIN=your-tenant-dev.eu.auth0.com
export AUTH0_MANAGEMENT_CLIENT_ID=... AUTH0_MANAGEMENT_CLIENT_SECRET=...
python scripts/bootstrap_users.py --tenant tenant-eu-1
python scripts/check_users.py --repair          # show what each user carries, and fix it
python scripts/get_token.py --client-id <console_client_id> --write-env
```

`get_token.py` signs you in as a real user through authorization-code + PKCE and prints a
genuine Auth0 access token; `--write-env` parks it in `telecom-mcp/.env`. It listens on
`http://localhost:5173/callback`, which Terraform has already registered.

### Things that will confuse you once

- **Claims come from `app_metadata`, never `user_metadata`.** `user_metadata` is
  user-writable, and a customer who could set their own `cx_id` could read anyone's bills.
- **A role assignment and a role claim are different things.** The claim's role comes from
  `app_metadata.role`; the permissions come from the Auth0 role assignment. A user needs
  both.
- **The post-login Action only runs on a login flow.** A client-credentials token never
  gets these claims — which is exactly why the service account is powerless.
- **RBAC changes take effect when a new token is minted**, not when you save the dashboard.
- **A permission typo is silently dropped** from the token. It shows up later as a refusal,
  not a warning.
- **Do not paste an M2M token anywhere.** The tenant issues 900-second tokens; a pasted one
  dies in a quarter of an hour. Let the server fetch its own
  (`TELECOM_MCP_SERVICE_IDENTITY_SOURCE=client_credentials`).
- **Token lifetime is capped at fifteen minutes** by default, and the variable refuses
  anything over an hour. A token above 3600s is rejected outright.

---

## 10. How the security model actually works

Two separate mechanisms, often confused. **Authorization** asks whether the caller is
*allowed*. **Guardrails** ask whether the call is *sane*. They live in different packages
and the guardrails never import the security one. (`telecom-mcp/docs/decisions/0007`.)

### The kernel: eight stages, in this order

From `security/authorization.py`, and this order is the design:

```
1  tool scope         is this a tool at all, and is it blocked in v1?
2  token              does it verify?
3  tenant             does it carry one?
4  CX ID              is a customer reference present where one is required?
5  account ownership  is this identity allowed to touch *this* account?
6  role               is the role one we know?
7  permission         does the role hold the scope this tool needs?
8  input schema       does the payload match the frozen v1 contract?
```

Tool scope runs **first** so that an unknown or blocked tool is refused in microseconds,
without cryptographic work or a backend lookup. The first failure ends the call, and the
audit record names the stage that decided it — every accept *and* every reject lands in the
hash-chained log. Ownership fails closed: the default checker refuses everything.
(`telecom-mcp/docs/decisions/0002` and `0003`.)

> An older design note listed tool scope eighth. The code puts it first, and
> `telecom-mcp/docs/decisions/0002` is the record that settles it.

**Permission is not ownership.** Holding `account:read` means you may read *an* account,
not *which*. A customer may read only their own `cx_id`. An agent may read only accounts in
`agent_assignments`. A supervisor may read their team's — and **may not approve a request
they raised themselves, nor one above their limit**. Those checks live in one module
(`security/access.py`) and every endpoint routes through it.

### The scopes and who holds them

```
account:read  service:read  order:read  billing:read  network:read
ticket:read   ticket:write  callback:write
refund:request         refund:approve
case:read     case:write
audit:read    config:read   config:write
assignment:read  assignment:write
```

| Role | Holds |
|---|---|
| `customer` | the read scopes, `ticket:write`, `callback:write`, `refund:request` — **never `refund:approve`** |
| `support_agent` | the same, for assigned accounts only |
| `supervisor_approver` | `refund:approve`, `assignment:*` |
| `admin_security` | `audit:read`, `config:*`, and **no customer-data scopes at all** — administering security does not mean reading bills |

### Guardrails

They run either side of the backend call, cheapest first:

```
authorization kernel   eight stages, deny by default
        ▼
input guardrails       rate limit → size/shape → unicode → injection
                       → business rules → action budget
        ▼
idempotency + backend
        ▼
redaction
        ▼
output guardrails      size cap → secret scan
```

The **action budget runs last** among the input checks because it is the only one that
*records* something — reserving an action for a call a later stage would refuse burns a
customer's allowance on a request that never happened.

| Stage | Refuses |
|---|---|
| rate limit | more than N calls a minute from one tenant and subject; continuous refill, not a fixed window |
| size and shape | arguments past the byte budget, strings past their limit, structures deep or wide enough to be a cost attack, C0 control characters (how a second fabricated line gets written into a log) |
| unicode | text that renders as one thing and contains another — zero-width and bidirectional characters, Latin mixed with Cyrillic or Greek, stacked combining marks |
| injection | free text shaped like an instruction to the model rather than a sentence from a customer |
| business rules | values the frozen schema cannot judge because it has no clock — a callback in the past or beyond the horizon, a refund above the ceiling |
| action budget | more than N irreversible actions on one case in a rolling window; reads never spend |
| output size | a response past the byte cap |
| output secret | a response still matching a secret shape *after* redaction — bearer tokens, JWTs, private keys, connection strings, AWS keys, API keys, card numbers (Luhn-checked first) |

**Every refusal looks identical to the caller:** *"The request was refused by a safety
control."* Two different messages for two different controls would tell whoever is probing
which one they tripped, and therefore which one to work around.

**To an operator** each refusal writes one audit record carrying `outcome` (`not_executed`
for an input refusal, `failure` for an output one), `action_executed` — the field that
matters at three in the morning — plus the stage and rule, and a `failure_reason` built
from rule names and counts and **never** from the input that caused it.

Every threshold is an environment variable with its reason in `.env.example`. The defaults
are the strict posture, so setting nothing gives you the strict posture and loosening a
control shows up in a diff. Three cannot be loosened in production and the service refuses
to start if they are: `TELECOM_MCP_GUARDRAILS_ENABLED`,
`TELECOM_MCP_GUARDRAIL_INJECTION_SCAN`, `TELECOM_MCP_GUARDRAIL_OUTPUT_SECRET_SCAN`.

**The injection scan is a filter, not a proof.** It catches the well-known shapes cheaply.
Nothing downstream is allowed to relax because it ran.

---

## 11. Observability

Four endpoints, four different questions, four different readers.

| | Answers | Read by |
|---|---|---|
| `GET /healthz` | Is the process alive? Consults nothing external, so one backend blip cannot restart every replica at once. | The container platform |
| `GET /readyz` | Can *this instance* serve? Checks the middleware, the idempotency store and, when in use, the identity provider. | The load balancer |
| `GET /metrics` | Prometheus exposition of every counter, gauge and histogram. | A scraper |
| `GET /kpi` | Which objective is breached, and what each one means. | **A person, during an incident** |

`/kpi` exists because the first question in an incident is "which objective is breached",
and answering it should not need a working dashboard, a working Grafana and a working
query. It is derived from the same registry `/metrics` renders, so the two cannot disagree,
and it returns **200 even when an objective is breached** — a probe pointed at it by
mistake must not restart the container.

### Four series, and nothing else

Everything on every dashboard derives from these:

| Series | Type | Labels |
|---|---|---|
| `tool_calls_total` | counter | `tool`, `outcome`, `code` |
| `tool_duration_seconds` | histogram | `tool` |
| `backend_attempts_total` | counter | `tool`, `stage` (the attempt number) |
| `guardrail_decisions_total` | counter | `tool`, `stage`, `outcome` |

Labels come from an allow-list. A customer identifier can never become one: the registry
**raises** rather than accepting it and producing a bill. Rule names are deliberately not
labels either — a label whose value set grows with the code is how a series count explodes
three months after anyone remembers why.

`outcome` takes exactly one terminal value per call — `ok`, `deduplicated`, `failed`,
`denied`, `guardrail_blocked` — **plus `shed`, recorded in addition** when a call is
admitted late. Shed is pressure, not an outcome; mixing it into the denominator is the
easiest way to make every ratio on the dashboard slightly wrong.

### The indicators and what they are for

**Service:** `tool_calls`, `success_ratio`, `failure_ratio`, `latency_p95_seconds`,
`latency_p99_seconds`, `calls_over_budget`, `shed_ratio`, `deduplication_ratio`,
`backend_retry_ratio`.
**Safety:** `authorization_denial_ratio`, `guardrail_block_ratio`, `output_guardrail_blocks`.
**Business:** `tickets_created`, `callbacks_scheduled`, `approvals_requested` — these carry
no direction. More tickets is not automatically good, and a dashboard that colours them
green teaches people the wrong lesson.

Two distinctions worth keeping straight: **a denial or a guardrail block is not a failure**
(a control working correctly is not an error, and counting it as one makes the failure
ratio a number nobody trusts), and **`calls_over_budget` counts observations past the ten
second bucket** — a quantile hides those; a count does not. It is the one number that maps
to a real customer sitting in silence.

### The objectives

| Indicator | Objective | Window | On breach |
|---|---|---|---|
| `success_ratio` | ≥ 99.5% | 30d | **page** |
| `latency_p95_seconds` | ≤ 2.0s | 30d | **page** |
| `output_guardrail_blocks` | 0 | 30d | **page** |
| `latency_p99_seconds` | ≤ 8.0s | 30d | ticket |
| `calls_over_budget` | 0 | 1d | ticket |
| `shed_ratio` | ≤ 1% | 7d | ticket |
| `backend_retry_ratio` | ≤ 5% | 7d | ticket |

Below a minimum sample an objective reports **unknown**, not met — green on a sample of
three is the kind of green that hides an outage. Ratio objectives carry an error budget;
latency objectives do not, because a threshold is not an allowance.

### Dashboards and alerts are generated

```bash
make observability          # regenerate from the catalogues
make observability-check    # fail if the committed output is stale (part of make check)
```

That writes `infra/observability/grafana-dashboard.json` (PromQL),
`queries.kql` and `azure-workbook.json` (Application Insights), and `alerts.bicep` — one
scheduled query rule per objective. **Never hand-edit them.**

**The detail that costs an hour the first time:** spans from the Azure Monitor exporter
land in the `dependencies` table, not `requests`, because they are internal spans, and
their attributes arrive as `customDimensions`. Every KQL query below unpacks them that way.

Tracing is off by default and **production refuses to start without it**. One span per tool
call, named `execute_tool`, opened where the audit record and the counters are written so
the three cannot disagree. The tool is an *attribute*, not part of the span name — a name
that varies per tool splits one operation into eight in every latency view. Sampling is
parent-based; sampling each service independently produces traces with holes, which are
worse than none because they look complete.

**Nothing identifying reaches a metric label, a span attribute or a log line** — no
customer id, phone number, email, address, payment detail or token. What operations genuinely
needs to correlate on is replaced by a stable pseudonym, so one customer can be followed
through a day of logs without the logs holding who they are. The registry and the tracer
both enforce this and both raise rather than silently dropping, because a control that
fails quietly has already failed.

---

## 12. Ship it

### The branches

```
development ──PR──► staging ──PR──► production ──tag──► release
     ▲                                    │
   all work lands here                    └── main tracks this, so a visitor
                                              sees what is actually running
```

One working branch, on purpose. Per-person branches make sense when the merge order
between people is the hard problem; here it is not, and the cost is a promotion graph
nobody can read at 3am.

Two rules CI enforces, and they are not advisory:

- **The only promotions are `development → staging` and `staging → production`.** Anything else fails the `promotion` job.
- **A promotion must be a fast-forward.** If `staging` has commits `development` does not, you get an error, because a promotion that needs a merge commit ships something staging never ran. Fix with `git checkout development && git merge --ff-only origin/staging`, push, reopen.

Squash merges are **off** deliberately: squashing on the way up gives production a sha
staging never built an image for, and the production deploy refuses it.

### The day-to-day loop

```bash
git switch development && git pull
# ... work, with make check passing ...
git push origin development

gh pr create --base staging --head development      # promote to staging
gh pr create --base production --head staging       # promote to production
git push origin production:main                     # keep main level with production
```

`gh` is the GitHub CLI. If it is not installed — `gh : The term 'gh' is not recognized` —
either install it (`winget install --id GitHub.cli` on Windows, then `gh auth login`) or
open the pull requests in the browser; nothing here requires the CLI. Plain `git push`
works either way, because the credential manager already has your GitHub login.

### First push, if the remote is empty

```bash
git remote add origin https://github.com/arshad98333/telecom-ai-gateway.git
git push -u origin development
git push origin main staging production
```

Before making it public:

```bash
git ls-files | grep -iE '(^|/)\.env($|\.)|\.tfvars$' | grep -v '\.example$'   # must print nothing
git status --short                                                            # must be clean
```

The `security` job scans the whole history for secrets on every push, but a public
repository is public immediately — check before, not after.

### What CI runs

`.github/workflows/ci.yml` at the repository root, on all four branches. **Only workflows
in the root run** — each service keeps its own `.github/` for standalone use, and those do
not execute from here.

| Job | What it does |
|---|---|
| `check` | each service's own `make check`, on Python 3.11 and 3.12, plus `uv lock --check` |
| `quickstart` | `make setup` from a clean clone, **twice** — this is what keeps section 2 honest |
| `contract` | the `e2e/` suite: both services in one process over real HTTP |
| `security` | dependency audit, gitleaks over the whole history, bandit |
| `container` | builds both images and proves each answers readiness |
| `mongo` (separate workflow) | the 43 Mongo tests against an ephemeral replica set |
| `release` (separate workflow, on `production` or manual dispatch) | publishes signed GHCR images for the MCP server and middleware |

### Deploying

There is no cloud account in this pipeline. GitHub Actions builds, tests and publishes
container images; what runs those images is your business, and the image digest is the
artifact.

```bash
docker pull ghcr.io/arshad98333/telecom-mcp-tools:production
docker run --rm -p 8080:8080 \
  -e TELECOM_MCP_LOCAL_VERIFIER_SECRET=a-development-secret-at-least-32-bytes \
  ghcr.io/arshad98333/telecom-mcp-tools:production
```

Anything that can run a container can run this: a VM with `docker compose`, a managed
container service, a Kubernetes cluster. The root `docker-compose.yml` is a working example
of the whole system, and `telecom-mcp/infra/azure/` holds a Bicep template for one specific
provider, unused unless you choose it.

**Promote by digest, never rebuild.** The image CI built from `production` is the image
that runs; rebuilding for production ships something no test ever saw.

```bash
docker pull ghcr.io/arshad98333/telecom-mcp-tools@sha256:<digest>
```

`ghcr.io` is public for a public repository, so pulling needs no credential. For a private
one, `docker login ghcr.io` with a token holding `read:packages`.

Configuration is environment variables ([section 16](#16-reference)), and the service
refuses to start on a bad set rather than starting badly. Whatever runs it should read
`/readyz` before sending traffic and `/healthz` to decide on a restart.

### Publishing Container Images

The production artifact is a container image. The root release workflow publishes both
services to GHCR:

- `ghcr.io/arshad98333/telecom-mcp-tools`
- `ghcr.io/arshad98333/telecom-middleware`

The workflow lives at `.github/workflows/release.yml`, in the repository root, and runs
on pushes to `production` or manual dispatch. It builds each image, boots it locally,
checks `/readyz`, `/healthz` and `/metrics`, then publishes multi-architecture images
with SBOM and signed provenance.

Promote the digest that passed:

```bash
docker pull ghcr.io/arshad98333/telecom-mcp-tools@sha256:<digest>
docker pull ghcr.io/arshad98333/telecom-middleware@sha256:<digest>
```

If release fails, fix forward on `development`, promote through the normal branches, and
run release again. Do not publish Python packages from this repository and do not rebuild
outside CI for production.

#### Version numbers

Semantic versioning, and **the tool contract is the thing being versioned**. A change to
what a tool accepts or returns is a **major** bump *and* a `TOOL_CONTRACT_VERSION` bump —
agents are built against that shape and cannot negotiate it. A new optional argument, a new
tool, or a changed error path is a **minor**. Fixes are patches.

#### What consumers get

```bash
docker run --rm -p 8080:8080 \
  -e TELECOM_MCP_LOCAL_VERIFIER_SECRET=a-development-secret-at-least-32-bytes \
  ghcr.io/arshad98333/telecom-mcp-tools:production
```

### Rolling back

Roll back **immediately, without waiting for a root cause**, if: task accuracy drops below
95%, authorization accuracy below 99%, or the critical error rate above 1%; any
authorization failure let a call reach data it should not have; a critical security finding
lands in the running version; there is material financial impact including duplicated
writes; or readiness is flapping or the breaker has been open more than five minutes
against a healthy middleware.

Rolling back is not an admission of anything. It is the cheap, reversible action.

1. **Announce** in the incident channel: version, symptom, who is running it.
2. **Find the last known-good digest** — on the previous release's deployment record or in the release workflow logs.
3. **Redeploy that digest.** Not a tag. There is no build step in a rollback, which is what makes it fast.
4. **Watch for five minutes.** `/readyz` healthy on every replica, and `tool_calls_total{outcome="failed"}` no longer climbing.
5. **Verify one real journey** — `get_customer_account` for a seeded identity — then confirm its audit record exists.
6. **Check the chain:** `telecom-mcp verify-audit <log>` must report it intact. A break means records were lost during the incident, which is its own incident.
7. **Freeze the branch** until the cause is understood.

Finding the previous digest:

```bash
docker image inspect ghcr.io/arshad98333/telecom-mcp-tools:1.1.0 --format '{{index .RepoDigests 0}}'
```

Each release run prints the image digest. Redeploy that digest with whatever starts your
containers, then work through the seven steps above.

Then fix forward on `development` and promote. **Never build a hotfix straight for
production**: the artifact that runs there is the one a tag built and the tests passed
against, and there is no other way to get one.

**What a rollback does not undo.** Tickets, callbacks and refund requests the bad version
created still exist. List them from the audit trail by correlation window and hand them to
Customer Operations; **do not delete them** — the customer may have been told they exist.
And if the middleware migrated, **its rollback runs first**.

Rehearse it once per release cycle in staging by rolling back a healthy release and rolling
forward again. A rollback nobody has performed is not a rollback.

---

## 13. When you are paged

Three alerts page. Everything else makes a ticket. On-call acknowledges within 15 minutes
and mitigates within 1 hour, escalating to the Engineering Incident Manager. Anything
touching authorization or data exposure goes to IT Security immediately; any customer impact
goes to Customer Operations.

### Step zero, every time

```bash
curl -s https://<host>/kpi | jq '.breached'
curl -s https://<host>/readyz | jq '{status, components: [.components[] | {name, status, detail}]}'
```

`/kpi` says *which objective*. `/readyz` says *whose fault*. If readiness reports
`telecom_middleware` unhealthy, you are looking at a middleware incident and everything
below is a symptom.

### Alert 1 — `success_ratio` below 99.5%

More than five calls in a thousand are failing *after* authorization. An agent mid-call
with a customer is seeing failures often enough to change how it behaves.

1. On `/kpi`, did `failure_ratio` move, or `authorization_denial_ratio`? A rise in denials is not this alert's fault.
2. Check `backend_retry_ratio`. If it rose first, this is the middleware, not us.
3. Then:

```kusto
dependencies
| where name == "execute_tool" and timestamp > ago(1h)
| extend outcome = tostring(customDimensions["outcome"]),
         code = tostring(customDimensions["code"]),
         tool = tostring(customDimensions["tool"])
| where outcome == "failed"
| summarize count() by code, tool
| order by count_ desc
```

One code dominating names the problem. `backend_timeout` is the middleware. `circuit_open`
means we already stopped asking. `internal_error` is ours, and a `tool_call_crashed` log
line with a stack trace exists.

**If it is the middleware, escalate — do not restart anything.** The breaker is already
shedding correctly, and restarting replicas resets the breakers and makes it worse. If it
is `internal_error`, roll back.

### Alert 2 — `latency_p95_seconds` above 2 seconds

A voice case has a five-minute budget and an agent may need four tools inside it. Above two
seconds at p95 that stops fitting.

1. `calls_over_budget` non-zero means calls are hitting the ten-second timeout and customers are sitting in silence, not merely waiting.
2. `shed_ratio` non-zero means we are at the concurrency limit and the answer is **replicas, not tuning**.
3. Which tool:

```kusto
dependencies
| where name == "execute_tool" and timestamp > ago(1h)
| extend tool = tostring(customDimensions["tool"])
| summarize p95 = percentile(duration, 95), calls = count() by tool
| order by p95 desc
```

4. `backend_retry_ratio` — retries multiply latency. Two retries on a two-second backend is a six-second call that still succeeds, which is how p95 doubles while the failure ratio stays flat.

**Do not raise `TELECOM_MCP_TOOL_TIMEOUT_S` to make the alert stop.** The budget is what
protects the customer from a call that will not finish in time to be useful.

### Alert 3 — `output_guardrail_blocks` above zero

**This is the one that matters. It should be zero forever.**

A write completed and the response was withheld, because after redaction the payload still
matched a secret shape. Two things are true at once: **the customer's action happened, and
the caller was not told what it produced.**

```kusto
dependencies
| where name == "execute_tool" and timestamp > ago(24h)
| extend stage = tostring(customDimensions["stage"]),
         rule = tostring(customDimensions["rule"]),
         tool = tostring(customDimensions["tool"]),
         correlation = tostring(customDimensions["correlation_id"])
| where stage startswith "output_"
| project timestamp, tool, stage, rule, correlation
```

Find the audit records for those correlation ids — they say `action_executed: true` and
carry `extra.guardrail_rule`.

`output_size` is usually a client or a backend returning far more than the contract allows —
a bug, not an incident. **`output_secret` is an incident**: the backend has started
returning a field that `observability/redaction.py` does not know about.

- **Do not switch the scan off.** It is refused in production for this reason.
- Identify the field, add it to `NEVER_DISCLOSED` or `PSEUDONYMISED` in `observability/redaction.py`, and ship on the normal path. It is a one-line change with a test; it does not need a hotfix process.
- Tell whoever owns the middleware that they added a field carrying a secret to a response, because they will have added it to more than one endpoint.
- For each affected correlation id the customer's write happened. Whether that needs human follow-up is a support decision — hand the list to the support lead.

### In any of the three, do not

- **Restart replicas to make a number move.** It resets circuit breakers, empties the in-memory idempotency store on any instance still using one, and loses the buffered spans that would have told you what happened.
- **Loosen a guardrail or an objective during an incident.** Both record a decision somebody made while thinking clearly, which is not the state you are in.
- **Deploy forward over a regression.** Roll back, then fix on `development` and promote.

---

## 14. Testing against a deployed URL

`e2e/` proves the two services agree with each other. `testsprite/` proves the deployed
thing works from outside — eighteen backend tests run from TestSprite's own cloud.

Because they run from someone else's cloud, the target must be publicly reachable https.
The CLI rejects `localhost` and private addresses. Either deploy to staging, or tunnel:

```bash
cloudflared tunnel --url http://localhost:8080     # the tool server
cloudflared tunnel --url http://localhost:9000     # the middleware
```

Then, before spending a single credit:

```bash
make testable        # both services in the external-test profile, one token, preflight
```

That layers `testsprite/profile/*.env` over each service's `.env`, changing only the
settings that stop an outside runner getting in, mints one token both hops accept, and
preflights the pair. Every setting it changes is refused in production by the settings
validator. If preflight fails, stop — a paid run would only rediscover for money what it
just told you for free.

```bash
cd testsprite
python run_testsprite.py preflight     # is the CLI installed and authenticated?
python run_testsprite.py setup --mcp-url https://… --middleware-url https://…
python run_testsprite.py credentials   # a bearer token per project, prompted, never written to a file
python run_testsprite.py create        # upload the 18 tests
python run_testsprite.py smoke         # three, not eighteen
python run_testsprite.py all           # only when you mean it, knowing the credit cost
```

Two projects, not one: the services have different URLs and different token audiences, and
a TestSprite project holds one of each.

Three rules the test files must follow, because the runner is not pytest: each file calls
its own test functions at the end (the runner does not collect `test_*`), no credential is
ever hardcoded (TestSprite injects `__AUTH_HEADERS__`), and only stdlib, `requests`,
`pytest`, `numpy` and `scipy` are available — no `pyjwt`, so tests base64-decode the
injected token's payload without verifying it.

**The target comes from `TARGET_URL`**, not from a literal in the file. The one exception is
the upload: TestSprite's V3 sandbox rejects an uploaded file whose base URL is not a
literal, so `make stamp MCP_URL=… MIDDLEWARE_URL=…` resolves it into `build/` for that
upload only and never edits a source. (`docs/decisions/0011`.)

Dry-run locally first, for free:

```bash
make validate
```

> Both previous failed runs were configuration, not product. Six middleware tests were
> blocked with `401 service_not_recognised` because the API wants both `Authorization` and
> `X-Service-Authorization` and the runner can only send the first. Ten tool-server tests
> failed with `token_invalid` and an empty catalogue because the services were running with
> `IDENTITY_VERIFIER=jwks` while the project credential was an HS256 development token.
> `make testable` exists so that combination cannot be reached by accident.

---

## 15. Troubleshooting

### Setup and configuration

| Symptom | Cause | Fix |
|---|---|---|
| A service exits with code **78** | Configuration. It names *every* problem at once, not the first | Read the list; `uv run --env-file .env telecom-middleware check-config` prints the resolved settings with secrets replaced |
| `Failed to parse environment file .env at position 0` | The file starts with a UTF-8 byte order mark | Rewrite it without one (`make setup` and `make wire-auth0` already do) |
| Both services reject each other's tokens | The two `LOCAL_VERIFIER_SECRET` values differ | `make setup` writes one secret into both |
| Every request returns 401 after a while | Development tokens last one hour by design | Mint another |
| Everything returns 403 with a valid token | The token's `cx_id` does not match the account, or a non-customer role has no assignment for it — **deny-by-default working** | Mint a token for the right customer, or add an `agent_assignments` entry |
| `make check` fails on `observability-check` | An objective changed without regenerating | `make observability`, commit the result |
| `uv sync` says "Unable to find lockfile" | You are not in a service directory | `make -C telecom-mcp install`, or `make install` from the root |

### Tokens and Auth0

| Symptom | Cause | Fix |
|---|---|---|
| `token could not be verified` | The issuer is missing its trailing slash, or the audience differs between the two services | Add the slash; make the two audiences identical |
| `token carries no tenant` | The post-login Action did not run, or `app_metadata.tenant_id` is unset. **Actions only run on a login flow — a client-credentials token never gets these claims** | Set `app_metadata.tenant_id`; confirm the Action is deployed and attached to the Login flow |
| `token carries an unknown role` | The Auth0 role name is not one of `customer`, `support_agent`, `supervisor_approver`, `admin_security` | Rename it to match the enum exactly |
| `token lifetime exceeds the permitted maximum` | The API's token expiration is above 3600s | Set it to 3600 or less on the API's Settings tab |
| `token header is malformed` | An opaque token: the login did not request the API audience | Make the login request `audience=https://api.telecom.example/v1` |
| `forbidden`, with the role clearly assigned | "Add Permissions in the Access Token" is off, so the token carries no `permissions` array | Turn it on in the API's RBAC settings, then **mint a new token** |
| `cross_account_denied` for the customer's own account | `app_metadata.cx_id` is a display name, not the reference | Set the real reference, e.g. `CX-1234` |
| `tools/list` returns `[]` | No token, or a verified identity holding no scope for any tool | Supply a token; check the role's permissions |
| `tools/list` returns `token_invalid` | The token did not verify | Check `TELECOM_MCP_IDENTITY_VERIFIER` matches how it was minted, and that the audience is one of its `aud` values |
| `service_credential_missing` | Nothing arrived in `X-Service-Authorization` — either the caller is not the tool server, or `TELECOM_MCP_BACKEND_API_KEY` is empty | Set the key; for external test runners use `testsprite/profile/` |
| `service_not_recognised` | Shared-secret mode: the two secrets differ. JWKS mode: the M2M token expired, or its client is not in `TELECOM_MW_SERVICE_ALLOWED_CLIENT_IDS` | Align the secret, or add the client id |

### Connecting to MongoDB

| Symptom | Cause | Fix |
|---|---|---|
| `The connection string still contains the literal <db_password> placeholder` | `.env` never edited | Replace it, **angle brackets included** |
| `Authentication was refused` / `bad auth` | Nine times in ten: unencoded `@ : / ? # %` in the password. Otherwise the user is in a different Atlas project, or lacks `readWrite` on `telecom` | Percent-encode, or use **Autogenerate Secure Password**. **Note the error blames the *username*** |
| `No server answered within the timeout` (Atlas) | In order of likelihood: your IP is not allow-listed (takes ~1 min to apply); the password is wrong; **your network blocks outbound 27017** | Add the IP; fix the password; test on a phone hotspot to confirm the firewall, then ask for the port |
| `No server answered within the timeout` (local) | The container is not up yet | `make up`, wait ten seconds |
| `The SRV record does not resolve` | A VPN or public resolver dropping SRV queries | In Atlas **Connect → Drivers**, switch the driver version to **3.4 or earlier** to get the long-form `mongodb://host1,host2,host3/...` string |
| `Connected, but this is a standalone mongod` / `Transaction numbers are only allowed on a replica set member` | Something else is on 27017 — usually a previously installed MongoDB service | Stop it, or point at the replica set |
| `The credential cannot write to 'telecom'` | Atlas issues read-only by default | **Database Access → Edit → Specific Privileges →** `readWrite` on `telecom` |
| `SSL: CERTIFICATE_VERIFY_FAILED` | A TLS-inspecting corporate proxy, or stale roots | `pip install --upgrade certifi`; if it persists, add `&tlsCAFile=` pointing at the certifi bundle |
| `not authorized on admin to execute command` | **Expected on a free cluster** — M0 blocks the `admin` database | Ignore it; nothing here needs it |
| Worked last month, times out today | A free M0 pauses after 30 days with no connections | Open Atlas and click **Resume** |

### Running the services

| Symptom | Cause | Fix |
|---|---|---|
| `307` from `POST /mcp` | The trailing slash is missing | Use `/mcp/`. PowerShell will not re-POST to the redirect |
| `readyz` reports `telecom_middleware` unhealthy | The tool server cannot reach the API | Check `TELECOM_MCP_BACKEND_BASE_URL` **ends with `/api/v1`** and the middleware is up on 9000 |
| `readyz` reports `identity_provider` unhealthy | The Auth0 tenant is unreachable | **Degraded, not unready** — the service serves on cached keys. Check the tenant |
| `/readyz` is 503 while `/healthz` is 200 | Correct behaviour: a dependency is down | The body names the component |
| The service refuses to start with "unsafe production configuration" | A guardrail or tracing switch is off with `ENV=production` | **That is the point.** Turn it back on rather than turning the check off |
| The invoice total looks wrong — `6300` vs `63.00` | The middleware returns minor units; the tool converts | Not a bug; `adapters/translation.py` |

### CI and deployment

| Symptom | Cause | Fix |
|---|---|---|
| The release image smoke step fails | The image built but did not serve `/readyz`, `/healthz` and `/metrics` | Read the container logs in that step, fix forward on `development`, promote again |
| `ci` fails on "Promotion path" | A PR from the wrong branch | Only `development → staging` and `staging → production` |
| `ci` fails on "Not a fast-forward" | The target has commits the source does not | `git checkout development && git merge --ff-only origin/staging`, push, reopen |
| A bad image reached production | Production deployed a digest that passed but behaved badly in its environment | Roll back to the last known-good digest, then fix forward |
| A branch push shows multiple red canceled runs | Older CI used `cancel-in-progress` and GitHub colored superseded runs red | Current workflows keep every run's final result instead |

### VS Code

| Symptom | Cause | Fix |
|---|---|---|
| Imports underlined but the code runs | The wrong interpreter for that folder | Re-run **Python: Select Interpreter** *with a file from that folder open* |
| Breakpoints are hollow and never hit | You launched **API: run with reload** — reload runs in a child process | Use **API: run (breakpoints work)** |
| `requests.http` sends `Bearer {{$dotenv DEV_TOKEN}}` literally | The REST Client extension is missing, or `.env` has no `DEV_TOKEN=` line | Install it; run the **Mint a dev token** task |
| The test explorer finds nothing | pytest is not in that folder's `.venv` | Run **Install dependencies** first |
| Two formatters fight on save | Another formatter is installed | **Format Document With… → Configure Default** → Ruff |
| `43 deselected` in the output | Expected — the Mongo tests need a replica set | See [section 7](#7-the-tests) |

---

## 16. Reference

### Commands

From the repository root:

| | |
|---|---|
| `make` | every target, one line each |
| `make setup` | `.env` files, one shared secret, dependencies, a config check |
| `make demo` | the whole stack in Docker, seeded |
| `make dev` | prints the two commands to run the services locally |
| `make up` / `down` / `logs` | just the local stack |
| `make test` / `test-fast` / `test-mongo` | tests |
| `make check` | lint, types, coverage — what CI runs |
| `make seed` | load the demo dataset |
| `make wire-auth0` / `wire-auth0-activate` | Terraform outputs into both `.env` files |
| `make testable` / `validate` / `stamp` | external testing |
| `make adr` | the next decision-record number |

Console commands, in each service directory:

| `telecom-mcp` | |
|---|---|
| `serve --transport {stdio,http}` | run it |
| `check-config` | validate and exit; prints resolved settings, secrets replaced |
| `verify-audit <path>` | verify a JSON-lines audit log's hash chain; `-` reads stdin |

| `telecom-middleware` | |
|---|---|
| `serve [--reload]` | run it |
| `check-config` | validate and exit |
| `migrate` | collections, validators, indexes |
| `verify-schema` | report declared indexes the database is missing |
| `check-store` | is the configured database usable |
| `hash-passcode <passcode>` | an Argon2id hash; runs before configuration loads |
| `seed [--tenant tenant-eu-1]` | the demo dataset |

Exit codes: `0` fine, `78` configuration error, `1` schema incomplete, bad passcode,
unusable store, or a broken audit chain.

### Ports and endpoints

| | |
|---|---|
| `:8080` | the tool server. `POST /mcp/` (**trailing slash mandatory**), `/healthz`, `/readyz`, `/metrics`, `/kpi` |
| `:9000` | the API, everything under `/api/v1`. `/healthz`, `/readyz`, `/docs`, `/api/v1/stream` |
| `:27017` | MongoDB |
| Accept header for MCP over HTTP | `application/json, text/event-stream` |
| Headers the tool server sends onward | `Authorization`, `X-Service-Authorization`, `X-Correlation-Id` |

### The environment variables that matter

Everything is prefixed `TELECOM_MCP_` or `TELECOM_MW_`, and each service's `.env.example`
lists every variable with a comment explaining it. A middleware test fails if the example
and the settings model disagree.

**telecom-mcp** — `ENV`, `IDENTITY_VERIFIER` (`local`|`jwks`), `JWKS_URL`, `JWT_ISSUER`,
`JWT_AUDIENCE`, `JWKS_CACHE_TTL_S`, `LOCAL_VERIFIER_SECRET`, `BACKEND` (`fake`|`http`),
`BACKEND_BASE_URL` (**must end `/api/v1`**), `BACKEND_API_KEY`, `SERVICE_IDENTITY_SOURCE`
(`static`|`client_credentials`), `SERVICE_TOKEN_URL`, `SERVICE_CLIENT_ID`,
`SERVICE_CLIENT_SECRET`, `SERVICE_TOKEN_AUDIENCE`, `IDEMPOTENCY_STORE` (`memory`|`redis`),
`HTTP_HOST`, `HTTP_PORT`, `TOOL_TIMEOUT_S`, `MAX_CONCURRENT_TOOL_CALLS`, `AUDIT_SINK`,
`GUARDRAILS_ENABLED`, `GUARDRAIL_INJECTION_SCAN`, `GUARDRAIL_OUTPUT_SECRET_SCAN` plus one
per threshold.

**telecom-middleware** — `ENV`, `STORE` (`memory`|`mongodb`), `MONGODB_URI`,
`MONGODB_DATABASE`, `IDENTITY_VERIFIER`, `JWKS_URL`, `JWT_ISSUER`, `JWT_AUDIENCE`,
`CLAIM_NAMESPACE`, `JWKS_CACHE_TTL_S`, `LOCAL_VERIFIER_SECRET`, `SERVICE_AUTH`
(`jwks`|`shared_secret`|`unchecked`), `SERVICE_ALLOWED_CLIENT_IDS`,
`SERVICE_SHARED_SECRET`, `HTTP_HOST`, `HTTP_PORT`, `CHANGE_STREAM_ENABLED`,
`PASSCODE_MAX_ATTEMPTS`, `PASSCODE_LOCKOUT_S`, `CASE_RETENTION_DAYS`,
`AUDIT_RETENTION_DAYS`.

Development defaults are the safe ones: the fake backend, the local verifier, in-memory
stores. `ENV=production` makes the settings validator refuse every one of them, along with
`SERVICE_AUTH=unchecked` and `SERVICE_IDENTITY_SOURCE=static`.

### Error codes

| Code | Meaning |
|---|---|
| `forbidden` | The caller lacks the scope this tool needs |
| `cross_account_denied` | The `cx_id` requested is not this identity's own |
| `token_invalid` | The token did not verify |
| `unauthenticated` | A service credential arrived with no user token |
| `service_credential_missing` | Nothing in `X-Service-Authorization` |
| `service_not_recognised` | The middleware does not accept the calling service |
| `guardrail_blocked` | Any guardrail refusal |
| `backend_timeout` | The middleware did not answer in budget |
| `circuit_open` | The breaker is open; we already stopped asking |
| `internal_error` | Ours. A `tool_call_crashed` log line with a stack trace exists |
| `not_executed` | Audit outcome for an **input** guardrail refusal |
| `failure` | Audit outcome for an **output** guardrail refusal — the write happened |

### Where to read next

| | |
|---|---|
| `docs/decisions/` | Why the system is shaped this way. Start with 0002 (two services), 0003 (the replica set) and 0006 (the powerless service account) |
| `telecom-mcp/docs/decisions/` | The tool server's own seven decisions — the stage order, deny-by-default ownership, load shedding, guardrail placement |
| `telecom-middleware/docs/decisions/` | Three: the replica set, authorizing the person not the service, and why "not found" answers exactly like "not yours" |
| `docs/brief/` | The specifications this was built to satisfy, unedited |
| `CONTRIBUTING.md` | Where changes go, how commits are written, the branch model |
| each service's `README.md` | That service on its own terms, for someone using it standalone |
