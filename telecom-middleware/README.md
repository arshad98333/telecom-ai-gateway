# telecom-middleware

The telecom customer data and approval service, and the only writer to MongoDB. Every
call is authenticated against Auth0, authorized twice — once for the permission, once
for the specific record — and recorded in a hash-chained audit trail. Restricted actions
wait for a named human, and the supervisor watching sees them arrive live.

It holds the rules and the data. The MCP tool server in front of it holds neither; see
`../GUIDE.md` for how the pieces fit.

## Requirements

| Tool | Version | Why |
|---|---|---|
| Python | 3.12 (3.11 also supported) | pinned in `.python-version` |
| [uv](https://docs.astral.sh/uv/) | 0.12.3 or newer | dependency resolution and the lock file |
| Docker | 24 or newer | only for the container and the MongoDB-backed tests |
| MongoDB | 7.0, **as a replica set** | transactions and change streams both need one |

The default configuration needs none of the last two: it runs against an in-memory
store, with a local token verifier, no network and no Auth0 tenant.

## Install and test

```bash
make install
make test        # 374 tests, offline, no database, no credentials
make check       # exactly what CI runs: format, lint, types, tests, coverage gate
```

Working in VS Code? Open `../telecom.code-workspace` and follow
[`../GUIDE.md`](../GUIDE.md#6-make-a-change): the launch configurations,
tasks, `requests.http` and the MongoDB playgrounds are all set up.

A pass looks like `374 passed, 43 deselected`. The MongoDB-backed contract tests are *deselected* by
default, not skipped — a deselection is visible in the run header, a skip is an
invisible failure. To run them:

```bash
docker compose -f ../docker-compose.yml up -d mongo
TELECOM_MW_MONGODB_URI='mongodb://localhost:27017/?replicaSet=rs0' make test-int
```

The same contract suite runs against both stores. That is what stops the fast in-memory
implementation drifting away from the real one, which is the usual way an offline suite
becomes a comfortable lie.

## Run

```bash
export TELECOM_MW_LOCAL_VERIFIER_SECRET=dev-signing-secret-at-least-32-bytes
make dev                                   # http://127.0.0.1:9000
uv run telecom-middleware seed             # a usable dataset, one command
curl -s localhost:9000/readyz | python -m json.tool
```

Or the whole system, including the tool server and a real MongoDB replica set:

```bash
cd .. && docker compose up -d
docker compose exec middleware telecom-middleware seed
```

`GET /healthz` is liveness, `GET /readyz` is readiness, `GET /metrics` is the Prometheus
scrape endpoint — a build-identity gauge and nothing else so far, see known gaps —
`GET /docs` is the generated OpenAPI page, and `GET /api/v1/stream` is the live event
feed.

## Configuration

All variables are prefixed `TELECOM_MW_`; `.env.example` lists every one with a comment,
and a test fails if the file and the settings model ever disagree. Starting with a
missing or malformed value prints one message naming every problem and exits 78.

| Variable | Required | Default | What it does |
|---|---|---|---|
| `ENV` | no | `local` | `local`, `staging`, `production`. Production refuses the developer conveniences. |
| `STORE` | no | `memory` | `memory` needs no database; `mongodb` needs a replica set. |
| `MONGODB_URI` | when `STORE=mongodb` | — | Connection string. Held as a secret and never printed. |
| `MONGODB_DATABASE` | no | `telecom` | |
| `MONGODB_MAX_POOL_SIZE` | no | `100` | Connections per process. |
| `IDENTITY_VERIFIER` | no | `local` | `local` (HS256, development) or `jwks` (Auth0). |
| `JWKS_URL`, `JWT_ISSUER`, `JWT_AUDIENCE` | when `jwks` | — | Identity provider JWKS. Laptop default is `local`. |
| `LOCAL_VERIFIER_SECRET` | when `local` | — | At least 32 bytes. |
| `CLAIM_NAMESPACE` | no | `https://telecom.example/` | Must match the Auth0 Action. |
| `PASSCODE_MAX_ATTEMPTS` / `PASSCODE_LOCKOUT_S` | no | `5` / `900` | What makes a four-digit secret acceptable. |
| `CHANGE_STREAM_ENABLED` | no | `true` | The live feed. |
| `SSE_HEARTBEAT_S` / `SSE_MAX_SUBSCRIBERS` | no | `15` / `500` | |
| `OUTBOX_BATCH_SIZE` / `OUTBOX_POLL_INTERVAL_S` | no | `100` / `1.0` | |
| `IDEMPOTENCY_TTL_S` | no | `86400` | How long a write key is remembered. |
| `CASE_RETENTION_DAYS` / `AUDIT_RETENTION_DAYS` | no | `90` / `2555` | |

## What the endpoints are for

| Stakeholder | Endpoints |
|---|---|
| Customer (through the voice agent) | `POST /customers/{cx}/authenticate`, the five reads, `POST .../tickets`, `.../callbacks`, `.../refund-approvals`, `PUT /cases`, `POST .../cases/resume` |
| Support agent | the same, for accounts assigned to them |
| Supervisor | `GET /approvals`, `POST /approvals/{id}/decision`, `POST /assignments`, `GET /stream` |
| Security admin | `GET /audit` — and nothing that touches customer data |

## Five things worth knowing before you change it

**Tenancy is a type signature, not a convention.** Every repository method takes
`tenant_id` and builds its own filter. There is no query in this service that can omit
one.

**Permission and ownership are separate checks.** Holding `account:read` means you may
read *an* account. Which one is a second question, answered in `security/access.py`, and
every endpoint routes through it. A customer may read their own; anyone else needs an
assignment, which is a lookup and never a claim in a token.

**Every refusal returns the same wording.** "Not found" and "not yours" are
indistinguishable on purpose: a difference between them is an oracle for discovering
which customers, tickets and approvals exist.

**Writes, their audit records and their events commit together.** That is why the
deployment needs a replica set. An event cannot exist for a change that rolled back, and
a change cannot commit without its record.

**The passcode is never stored.** Only an Argon2id hash, with attempt counting done
atomically so two concurrent guesses cannot each get a sixth try.

## Troubleshooting

**Exits immediately with code 78** — configuration failed validation. The message names
every problem at once. `telecom-middleware check-config` prints the resolved settings
with secrets replaced.

**`Transaction numbers are only allowed on a replica set member`** — MongoDB is running
standalone. Start it with `--replSet rs0` and initiate the set; `docker compose` does
both.

**Everything returns 403** — either the token's `cx_id` does not match the account, or a
non-customer identity has no assignment for it. The deny-all default is deliberate: a
missing assignment must never widen access.

**`test_auth0_parity` fails saying Terraform was not found** — the Auth0 Terraform
module is no longer in this repository. Skip that test locally, or point
`TELECOM_INFRA_DIR` at an external copy if you still maintain one.

**The live feed goes quiet** — a subscriber that falls behind is dropped on purpose.
Reconnect with `Last-Event-ID` and the missed events replay from the outbox.

## Known gaps

Stated plainly, because a README describing the intended state is worse than none:

- `/metrics` exists but is nearly empty. It serves a single static
  `telecom_middleware_info` gauge, which is enough for a scrape to succeed and for a
  release to confirm the endpoint is wired, and not enough to alert on. The request
  counters and the latency histogram from the tools package have still not been ported
  across, so nothing here measures traffic, errors or duration yet.
- The load measurement runs in CI against one MongoDB node on a shared runner. It
  catches a regression; it is not a capacity model.
- Retention is configured but not enforced: nothing sweeps cases or audit records at the
  configured age yet. The TTL index exists only for idempotency keys.
- Approval requests expire by timestamp but nothing marks them expired on a schedule, so
  `expired` is currently a state only a future job will set.
- The outbox relay is at-least-once and in-process. A second replica will publish the
  same event twice; consumers must tolerate it (the sequence makes that easy), and a
  leader election or a single relay deployment is the next step.
