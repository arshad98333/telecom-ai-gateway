# Telecom Agentic AI Support — System Design

One system, three deployable parts, one identity provider, one database.

```
   customer (voice)        supervisor / agent          security · ops
        │                        │                          │
        ▼                        ▼                          ▼
  ┌───────────┐          ┌──────────────┐           ┌──────────────┐
  │voice agent│          │  console UI  │           │  audit tools │
  └─────┬─────┘          └──────┬───────┘           └──────┬───────┘
        │ MCP                   │ REST + SSE               │ REST
        ▼                       │                          │
  ┌─────────────────┐           │                          │
  │ telecom-mcp     │           │                          │
  │ (tool gateway)  │           │                          │
  └────────┬────────┘           │                          │
           │ HTTPS + M2M token  │ HTTPS + user token       │
           ▼                    ▼                          ▼
  ┌──────────────────────────────────────────────────────────────┐
  │            telecom-middleware  (FastAPI)                      │
  │  RBAC · tenancy · ownership · idempotency · outbox · streams  │
  └───────────────┬───────────────────────────┬──────────────────┘
                  │                           │
                  ▼                           ▼
          ┌───────────────┐          ┌────────────────┐
          │   MongoDB     │  change  │  SSE fan-out   │
          │ (replica set) │ ───────► │  per tenant    │
          └───────────────┘  streams └────────────────┘

   Auth0 issues every token: customer, agent, supervisor, security admin,
   and the machine-to-machine token telecom-mcp uses.
```

## 1. Why the middleware exists at all

The MCP package must not hold business rules or touch the database — that decision is
already recorded in `telecom-mcp/docs/decisions/0001`. The middleware is where the rules
and the data live, and it is the only writer to MongoDB. That gives one place to enforce
validation, authorization, tenancy and auditing for every consumer: the voice agent
through MCP, the supervisor console, the security tooling, and anything added later.

Two layers of authorization is not duplication. The MCP kernel answers *"may this agent
call this tool for this customer"* using the caller's token; the middleware answers
*"may this identity read or change this record"* using its own view of assignments and
approval authority. Neither trusts the other's word. The middleware would still be safe
if the MCP server were compromised, which is the property that matters.

## 2. Stakeholders, and what each one actually does

| Stakeholder | Auth0 role | Gets a token how | Reads | Writes | Realtime |
|---|---|---|---|---|---|
| Customer | `customer` | Voice session, after CX ID + 4-digit passcode | Own account, services, orders, invoices, network | Own tickets, callbacks, refund *requests* | — |
| Support agent | `support_agent` | Console login (SSO) | Assigned customers only | Tickets, callbacks, case notes on assigned customers | Their queue |
| Supervisor / approver | `supervisor_approver` | Console login (SSO) | Their team's customers and the approval queue | Approve or reject restricted actions | **Approval queue, live** |
| Security admin | `admin_security` | Console login (SSO, step-up) | Audit records, role config, retention state | Role and retention configuration | Audit stream |
| `telecom-mcp` service | M2M client | Client credentials | Nothing on its own | Nothing on its own | — |

The service account is deliberately powerless by itself. It presents the *customer's*
token on every call (token exchange), so the middleware authorizes the human, not the
robot. A compromised MCP service credential cannot read one customer record.

### The approval journey, which is where the stakeholders meet

1. Customer asks for a refund. The voice agent calls `request_refund_approval`.
2. MCP validates, deduplicates, and POSTs to the middleware with the customer's token.
3. Middleware writes an `approval_requests` document (`state: pending`) **and** an
   `outbox` event, in one transaction. Nothing has moved.
4. A change stream picks up the insert and pushes it to every supervisor watching that
   tenant, within milliseconds. No polling, no refresh button.
5. A supervisor decides. That write checks approval *authority* (a supervisor may not
   approve their own request, nor one above their limit), records the decision with the
   evidence they saw, and emits the next event.
6. The customer is told the outcome on the next turn, or by callback. The whole chain —
   request, evidence, approver, timestamp, decision — is one audit trail.

## 3. The data

MongoDB, one database, one collection per aggregate. Tenancy is the first field of
every document and the first field of every index. There is no cross-tenant query path,
because there is no query in the code that omits it: the repository layer takes the
tenant as a required argument and builds the filter itself.

| Collection | Key | Purpose | Growth |
|---|---|---|---|
| `customers` | `(tenant_id, cx_id)` | Account record, status, passcode **hash** | Bounded by customer count |
| `services` | `(tenant_id, cx_id, service_id)` | Active services and plans | ~5 per customer |
| `orders` | `(tenant_id, cx_id, order_id)` | Order state and history | Grows; capped by retention |
| `invoices` | `(tenant_id, cx_id, invoice_id)` | Billing summaries | ~12/year/customer |
| `network_status` | `(tenant_id, area_ref)` | Area incidents, shared across customers | Small, hot, cacheable |
| `agent_assignments` | `(tenant_id, agent_sub)` | Which accounts an agent may touch | Small |
| `tickets` | `(tenant_id, ticket_id)` | Support tickets | High write volume |
| `callbacks` | `(tenant_id, callback_id)` | Scheduled callbacks | Moderate |
| `approval_requests` | `(tenant_id, request_id)` | Restricted actions awaiting a human | Low volume, high value |
| `cases` | `(tenant_id, case_id)` | Voice case state, for resume after disconnect | One per call |
| `audit_records` | `(tenant_id, seq)` | Hash-chained, append only | Highest volume |
| `outbox` | `(_id)` | Events awaiting relay to consumers | Drained continuously |
| `idempotency_keys` | `(tenant_id, scope, key)` | Write deduplication, TTL 24h | Self-expiring |

**Passcodes are never stored.** The 4-digit account passcode is stored as an Argon2id
hash with a per-customer salt, verified in constant time, rate-limited per CX ID, and
locked after repeated failures. It is never read back, never logged, never sent to a
model. A 4-digit secret is weak by construction, so the controls around it — attempt
limits, lockout, and the fact that it only ever authenticates alongside a CX ID the
caller must already know — are what make it acceptable.

**Money is stored in minor units as a 64-bit integer**, with the currency beside it.
Not a float, and not a `Decimal128` the application has to keep converting.

**Timestamps are UTC**, always, with the field named for what happened (`created_at`,
`decided_at`), never a bare `date`.

### Indexes, designed from the queries rather than added after a slow day

Every read path in the API maps to exactly one compound index, tenant first:

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

A query the code can express but no index serves is a latency incident waiting for a
busy Tuesday, so the index set is asserted by a test: every repository method declares
the index it relies on, and the test fails if the collection does not have it.

### Consistency

Writes that must not half-happen — the approval request and its outbox event, the
ticket and its audit record — run in a single transaction on the replica set. That is
the reason the deployment needs a replica set even with one node, and it is also what
makes change streams available.

## 4. Authorization: Auth0 as the source of truth

Auth0 holds identity and role assignment. The middleware holds *what a role may do* and
*which records this identity may touch*. Splitting it this way means adding a permission
is a code change with a test, not a click in a dashboard nobody reviews.

**The API and its scopes** (`infra/auth0/`, as Terraform):

```
account:read  service:read  order:read  billing:read  network:read
ticket:read   ticket:write  callback:write
refund:request         refund:approve
case:read     case:write
audit:read    config:read   config:write
assignment:read  assignment:write
```

**Roles bundle scopes.** A customer holds the read scopes plus `ticket:write`,
`callback:write` and `refund:request` — never `refund:approve`. A supervisor holds
`refund:approve` and `assignment:*`. A security admin holds `audit:read` and `config:*`
and **no customer-data scopes at all**: administering security does not mean reading
bills.

**Claims.** An Auth0 post-login Action injects `tenant_id`, `cx_id` (customers only) and
`role` as namespaced claims, from the user's `app_metadata`. The Action is in the
repository, versioned, and deployed by Terraform. Permissions arrive in the standard
`permissions` claim with RBAC enabled on the API.

**Every endpoint declares its scope in code**, as a FastAPI dependency, and a test
enumerates the routes and fails if any route is missing one. A new endpoint cannot ship
unprotected by omission.

**Ownership is separate from permission.** Holding `account:read` means you may read
*an* account; it does not say *which*. A customer may read only their own `cx_id`. An
agent may read only accounts in `agent_assignments`. A supervisor may read their team's,
and may not approve a request they raised themselves. These checks live in one module
and every endpoint routes through them.

**The MCP service account** uses client credentials only to authenticate itself as a
service; the customer's own token travels with the request. The middleware refuses a
customer-data call that carries only a service token.

## 5. Realtime

Four separate things, often lumped together and worth keeping apart.

**Live approval queue (change streams → SSE).** A single watcher per process tails a
change stream filtered to the collections and operations that matter, and fans events
out to subscribers over SSE. The resume token is persisted after each batch, so a
restart continues where it stopped rather than replaying a day or losing an hour.
Subscribers are authorized *at subscribe time and again per event*: a supervisor sees
only their tenant, and only the events their scopes permit. A dropped connection
reconnects with `Last-Event-ID` and is replayed from the outbox.

**Low-latency reads.** Every read path has its index; documents are projected in the
database, not in Python; the connection pool is sized and shared. The budget is p95
under 150 ms server-side for a single-customer read, and it is measured by a load test
in CI against a real MongoDB rather than asserted here.

**Event outbox.** Every state change writes an event in the same transaction as the
change itself, and a relay publishes it. Consumers — billing reconciliation, CRM,
analytics — read events instead of polling the database. This is also what makes SSE
replay possible after a disconnect.

**Voice case state.** The case document is updated at every turn: intent, tool calls
made, what was said back, what remains. If the caller drops, the case is marked
`interrupted` immediately. Resume requires re-authentication, and then returns the
customer to where they were rather than to the beginning — the failure the SOP calls out
by name.

## 6. What this system refuses to do

No plan changes, cancellations, contract or ownership changes execute in version one.
`refund:approve` exists and is enforced, but the executing side stays dark until the
finance integration and its reconciliation are in place. A restricted operation with an
executable path and no approval control is exactly the risk the programme was set up to
avoid.
