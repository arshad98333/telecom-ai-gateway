# telecom-mcp-tools

MCP tool server for a telecom AI voice support agent. It exposes a fixed set of
customer support tools over the Model Context Protocol and refuses everything else:
every call is authenticated, authorized against the caller's own account, validated
against a frozen schema, rate-bounded, deduplicated if it writes, and recorded in a
tamper-evident audit trail — before it is allowed anywhere near customer data.

It is a gateway, not a business logic system. It never connects to a database; it calls
the telecom middleware API, which is what keeps validation, authorization and auditing
in one place.

## Quick start

Three commands, no credentials, no database, no account. The default configuration
serves a committed fixture dataset through the real security kernel, so what you get is
a genuine tool server with genuine authorization — refusals and all — over fake
customers.

```bash
docker run --rm -p 8080:8080 \
  -e TELECOM_MCP_HTTP_HOST=0.0.0.0 \
  -e TELECOM_MCP_LOCAL_VERIFIER_SECRET=a-development-secret-at-least-32-bytes \
  ghcr.io/arshad98333/telecom-mcp-tools:latest
```

```bash
curl -s localhost:8080/readyz
```

Or from PyPI, if you would rather not use Docker:

```bash
pip install telecom-mcp-tools[http]
export TELECOM_MCP_LOCAL_VERIFIER_SECRET=a-development-secret-at-least-32-bytes
telecom-mcp serve --transport http
```

`uvx --from 'telecom-mcp-tools[http]' telecom-mcp serve --transport http` does the same
without installing anything permanently.

`GET /healthz` is liveness, `GET /readyz` is readiness, `GET /metrics` is the Prometheus
exposition, and `POST /mcp/` is the MCP endpoint — note the trailing slash; without it
the server answers `307` and most clients will not re-POST to the redirect.

To call a tool you need a token. In the default configuration you can mint one:

```bash
export TELECOM_MCP_ACCESS_TOKEN="$(python scripts/mint_dev_token.py)"
```

## Running it against your own systems

Nothing in this package is specific to one company's tenant. Eight variables point it
at yours; everything else has a working default.

```bash
cp .env.example .env      # then edit these:

TELECOM_MCP_BACKEND=http
TELECOM_MCP_BACKEND_BASE_URL=https://your-api.internal/api/v1

TELECOM_MCP_IDENTITY_VERIFIER=jwks
TELECOM_MCP_JWKS_URL=https://your-tenant.eu.auth0.com/.well-known/jwks.json
TELECOM_MCP_JWT_ISSUER=https://your-tenant.eu.auth0.com/
TELECOM_MCP_JWT_AUDIENCE=https://your-api-identifier
TELECOM_MCP_CLAIM_NAMESPACE=https://your-company.example/

TELECOM_MCP_SERVICE_IDENTITY_SOURCE=client_credentials
TELECOM_MCP_SERVICE_CLIENT_ID=...
TELECOM_MCP_SERVICE_CLIENT_SECRET=...
```

```bash
docker compose up -d
```

Then `telecom-mcp check-config` prints the resolved settings with secrets replaced, or
exits `78` naming every problem at once.

Four things that catch people out, all of them enforced rather than documented:

- **The issuer keeps its trailing slash; the JWKS URL does not.** That asymmetry is
  exact, and getting it wrong produces `token could not be verified` with an otherwise
  perfect setup.
- **The claim namespace must match** what your identity provider's post-login action
  writes and what your backing API reads. All three must agree, and it must end with
  `/`. The three claims read are `<namespace>tenant_id`, `<namespace>role` and
  `<namespace>cx_id`.
- **Permissions are read from the `permissions` claim first**, falling back to `scope`.
  With Auth0 that means RBAC enabled on the API *and* "Add Permissions in the Access
  Token"; without the second setting a correctly-assigned role still gets nothing.
- **The token this server verifies is forwarded to your API unchanged**, so both must
  accept the same audience. An OIDC access token carries exactly one, which means one
  API registration in front of both services, not two.

Your API is expected to accept a bearer token in `Authorization`, this service's own
credential in `X-Service-Authorization`, and to expose the endpoints listed in
`docs/architecture.md`. The scope and role vocabulary lives in
`src/telecom_mcp/domain/permissions.py`; change it there and the tool contract follows.

## Deploying it

Branch-based delivery to Azure Container Apps, built once and promoted:

```
feat/*  ──PR──►  main  ──build──►  image@sha256:…  ──►  QA  ──approval──►  PROD
```

The image is pinned by digest the moment it is built, so production runs the bytes QA
ran; approving a release chooses when, not what. The gate is a GitHub Environment
reviewer on `prod`, and rollback is a revision weight.

`infra/azure/main.bicep` is the whole deployment — Container App, managed identity,
Key Vault references, probes and scale rules. `infra/azure/README.md` has the one-time
bootstrap: a shared registry, a resource group and vault per environment, and OIDC
federation so no Azure credential is ever stored in GitHub.

## Requirements for development

| Tool | Version | Why |
|---|---|---|
| Python | 3.12 (3.11 and 3.13 also supported) | pinned in `.python-version` |
| [uv](https://docs.astral.sh/uv/) | 0.12.3 or newer | dependency resolution and the lock file |
| Docker | 24 or newer | only for the image and the container smoke test |
| GNU Make | 4.3 or newer | one command per action |

```bash
git clone <this repository>
cd telecom-mcp
make install
make test
```

A pass looks like `376 passed`. The suite runs with the network disabled, with no
credentials and no `.env` file, and no test is skipped for missing configuration. It
also passes in random order — `make test` randomises it every run.

```bash
make check   # exactly what CI runs: format, lint, types, tests, coverage gate
```

## Configuration

Every variable, its default, and whether it is required. All of them are prefixed
`TELECOM_MCP_`; see `.env.example` for the same list with comments. Starting with a
missing or malformed value prints one message naming every problem and exits 78.

| Variable | Required | Default | What it does |
|---|---|---|---|
| `ENV` | no | `local` | `local`, `staging` or `production`. Production refuses the developer conveniences. |
| `LOG_LEVEL` | no | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR`. |
| `SERVICE_NAME` | no | `telecom-mcp-tools` | Appears on every log line. |
| `HTTP_HOST` / `HTTP_PORT` | no | `127.0.0.1` / `8080` | HTTP transport bind address. |
| `BACKEND` | no | `fake` | `fake` needs no network; `http` calls the middleware API. |
| `BACKEND_BASE_URL` | when `BACKEND=http` | — | Middleware API base URL. |
| `BACKEND_API_KEY` | when `BACKEND=http` | — | This service's own credential, sent as `X-Service-Authorization`. It proves which service is calling; the customer's token, in `Authorization`, is what authorizes the data. Never logged. |
| `BACKEND_CONNECT_TIMEOUT_S` | no | `2.0` | Connect timeout on every call. |
| `BACKEND_READ_TIMEOUT_S` | no | `8.0` | Read timeout on every call. |
| `BACKEND_MAX_CONNECTIONS` | no | `50` | Connection pool ceiling, per process. |
| `IDENTITY_VERIFIER` | no | `local` | `local` (HS256, development) or `jwks` (RS256, Auth0-shaped). |
| `JWKS_URL`, `JWT_ISSUER`, `JWT_AUDIENCE` | when `IDENTITY_VERIFIER=jwks` | — | Where keys come from and what a token must claim. |
| `LOCAL_VERIFIER_SECRET` | when `IDENTITY_VERIFIER=local` | — | At least 32 bytes. |
| `JWKS_CACHE_TTL_S` | no | `600` | How long signing keys are cached. |
| `CLAIM_NAMESPACE` | no | `https://telecom.example/` | Prefix on the `tenant_id`, `role` and `cx_id` claims. Must end with `/` and match your provider and your API. |
| `SERVICE_IDENTITY_SOURCE` | no | `static` | `static` sends `BACKEND_API_KEY` unchanged; `client_credentials` fetches a token and refreshes it before expiry. Production requires the latter. |
| `SERVICE_TOKEN_URL`, `SERVICE_CLIENT_ID`, `SERVICE_CLIENT_SECRET` | when `SERVICE_IDENTITY_SOURCE=client_credentials` | — | Where this service gets its own credential, and who it is. |
| `SERVICE_TOKEN_AUDIENCE` | no | `JWT_AUDIENCE` | The audience that credential is minted for. |
| `TOOL_TIMEOUT_S` | no | `10.0` | Total budget for one tool call, retries included. |
| `RETRY_ATTEMPTS` | no | `2` | Retries after the first attempt, on safe operations only. |
| `RETRY_BASE_DELAY_S` | no | `0.2` | First backoff step; doubles, capped, jittered. |
| `BREAKER_FAILURE_THRESHOLD` | no | `5` | Consecutive failures before the breaker opens. |
| `BREAKER_RESET_TIMEOUT_S` | no | `30.0` | How long it stays open before one probe. |
| `MAX_CONCURRENT_TOOL_CALLS` | no | `100` | In-flight ceiling per process; beyond it, requests are shed. |
| `IDEMPOTENCY_STORE` | no | `memory` | `memory` is single-replica only; production requires `redis`. |
| `IDEMPOTENCY_TTL_S` | no | `86400` | How long a key is remembered. |
| `REDIS_URL` | when `IDEMPOTENCY_STORE=redis` | — | Shared store for deduplication across replicas. |
| `AUDIT_SINK` / `AUDIT_FILE_PATH` | no | `stdout` / `./audit.log` | Where audit records go. |
| `ACCESS_TOKEN` | stdio only | — | Bearer token for the stdio transport. |

## Architecture

A request enters through a transport, passes the security kernel, reaches the domain,
and touches the outside world only through ports.

```
client ──► transport (stdio | HTTP) ──► security kernel ──► domain ──► ports ──► adapters
                                              │                                  fake | http
                                              └──► audit (hash-chained) + metrics + logs
```

The kernel runs eight stages in a fixed order — tool scope, token, tenant, CX ID,
account ownership, role, permission, input schema — and a tool cannot be executed
without traversing all of them, because the registry only ever hands back a wrapped
callable. Every outcome, allowed or refused, writes exactly one audit record naming
the stage that decided it.

The separation that matters most is `domain/` against `adapters/`. Business rules touch
no network, no database and no filesystem, so they are tested in milliseconds; anything
that does touch the outside world sits behind an interface we defined, with a real
implementation and a fake. `docs/architecture.md` has the longer version and
`docs/decisions/` records why each significant choice was made.

## The tools

| Tool | Scope | Risk | Idempotency key | Approval |
|---|---|---|---|---|
| `get_customer_account` | `account:read` | read | — | — |
| `get_active_services` | `service:read` | read | — | — |
| `get_order_status` | `order:read` | read | — | — |
| `get_invoice_summary` | `billing:read` | read | — | — |
| `get_network_status` | `network:read` | read | — | — |
| `create_support_ticket` | `ticket:write` | low-risk write | required | — |
| `schedule_callback` | `callback:write` | low-risk write | required | — |
| `request_refund_approval` | `refund:request` | restricted | required | supervisor |
| `change_service_plan` | `service:change` | restricted | — | **blocked in v1** |
| `cancel_service` | `service:cancel` | restricted | — | **blocked in v1** |

A blocked tool is declared so policy and the audit trail know it exists, and has no
executable path: looking it up returns nothing, and calling it by name is refused.
The listing a caller receives contains only the tools their identity may invoke.

## Troubleshooting

**`make install` fails with "Unable to find lockfile"** — you are not in the project
root. Run `make -C /path/to/telecom-mcp install`.

**The server exits immediately with code 78** — configuration failed validation. The
message names every missing or malformed variable at once; fix them all and start
again. `telecom-mcp check-config` prints the resolved settings with secrets replaced.

**Every call comes back `cross_account_denied`** — the token's CX ID does not match the
`cx_id` in the arguments. For a non-customer role you also need an ownership checker
wired in; the default refuses everything, on purpose.

**`readyz` returns 503 while `healthz` returns 200** — that is correct and it means a
dependency is down, not the process. The response body names the component.

## Known gaps

Stated plainly, because a README that describes the intended state rather than the
current one is worse than no README:

- The container base image is referenced by tag, not by digest. It must be pinned
  before the first production release — see `docs/decisions/0004-container-base.md`.
- The middleware now exists (`../telecom-middleware`) and the end-to-end suite in
  `../e2e` runs this package against it with nothing stubbed between them. What is still
  missing is a recorded response from the *real* telecom middleware in production;
  the fake is built from the shape our own service returns.
- The ownership checker for the support agent and supervisor roles still has no
  implementation in this package; the deny-all default ships. The middleware enforces
  assignments on its own side, so an agent is not over-permitted - but this package
  refuses agent calls outright rather than delegating, which is stricter than intended.
- The Redis store is exercised against a faithful in-process double, not a real Redis.
  The integration test against a containerised Redis is the next thing to add.
- Load behaviour is reasoned about, not measured. The concurrency ceiling and the
  breaker thresholds are defaults, not numbers derived from a load test.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). In short: one behaviour per change, the test
first, `make check` green before you push.

## Licence

MIT. See [LICENSE](LICENSE).
