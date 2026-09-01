# Telecom Agentic AI Support

A voice agent stack for telecom customer support: read account data, raise tickets, and
route sensitive actions to human approval. The tool server enforces authorization before
any backend call; the API holds business rules and data.

This repository contains three runnable components:

| Directory | Role | Default port |
|-----------|------|--------------|
| `telecom-mcp/` | MCP tool server (gateway) | 8080 |
| `telecom-middleware/` | REST API and data layer | 9000 |
| `telecom-mcp-client/` | Reference CLI client for the tool server | n/a |

Local development uses a **shared signing secret** and **built-in demo data**. Auth0 and
external identity providers are **not required** on a laptop. Production identity is
optional and lives under `infra/auth0/` (Terraform).

Further detail: [GUIDE.md](GUIDE.md), [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Prerequisites

| Tool | Purpose | Verify |
|------|---------|--------|
| [uv](https://docs.astral.sh/uv/) 0.12.3 or newer | Python and dependencies | `uv --version` |
| Python 3.11+ | Invoked by uv | bundled with uv |
| Docker 24+ (optional) | MongoDB and container images | `docker --version` |
| GNU Make (optional) | Convenience targets | `make --version` |

On Windows without Make, use `scripts/dev.ps1` (PowerShell) for the same workflow.

Install uv on Windows:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

---

## Step 1: Clone and install

```bash
git clone https://github.com/arshad98333/telecom-ai-gateway.git
cd telecom-ai-gateway
```

**With Make:**

```bash
make setup
```

**With PowerShell (no Make):**

```powershell
python scripts/setup.py
```

**What this does:**

1. Creates `telecom-mcp/.env` and `telecom-middleware/.env` from examples if missing.
2. Writes one shared `LOCAL_VERIFIER_SECRET` into both files (required for tokens).
3. Runs `scripts/local_env.py`, which sets:
   - `IDENTITY_VERIFIER=local` (no Auth0, no JWKS)
   - `TELECOM_MCP_BACKEND=fake` (built-in demo customers, no database)
   - `TELECOM_MW_STORE=memory` when using the API directly
4. Installs dependencies for `telecom-mcp`, `telecom-middleware`, and `telecom-mcp-client`.
5. Runs `check-config` on both services.

To keep existing Auth0 settings in `.env`, run: `python scripts/setup.py --keep-auth0`

To reset only the local profile later: `make local` or `python scripts/local_env.py`

---

## Step 2: Run on your machine (no Docker)

You need two terminals for the full HTTP path, or one terminal if you use the fake
backend only on the tool server.

### Terminal 1: tool server

```bash
make run-mcp
```

PowerShell:

```powershell
.\scripts\dev.ps1 run-mcp
```

Listens on `http://127.0.0.1:8080`. With `TELECOM_MCP_BACKEND=fake`, demo data is
embedded; the middleware does not need to run.

### Terminal 2 (optional): API

Only needed when `TELECOM_MCP_BACKEND=http` in `telecom-mcp/.env`.

```bash
make run-middleware
```

PowerShell:

```powershell
.\scripts\dev.ps1 run-middleware
```

Listens on `http://127.0.0.1:9000`.

### Terminal 3: health check

```bash
make health
```

PowerShell:

```powershell
.\scripts\dev.ps1 health
```

Both endpoints should report `"status": "healthy"`.

---

## Step 3: Mint a token and call a tool

Tokens are signed locally. Auth0 is not involved in this path.

```bash
export TELECOM_MCP_ACCESS_TOKEN="$(make -s token)"
export TELECOM_MCP_URL="http://127.0.0.1:8080"
make client-tools
make client-call
```

PowerShell:

```powershell
cd telecom-mcp
$env:TELECOM_MCP_ACCESS_TOKEN = uv run --env-file .env python scripts/mint_dev_token.py
cd ..\telecom-mcp-client
$env:TELECOM_MCP_URL = "http://127.0.0.1:8080"
uv run telecom-mcp-client list-tools
uv run telecom-mcp-client call get_customer_account --json '{\"cx_id\": \"CX-1234\"}'
```

Demo customer `CX-1234` is defined in the fake backend seed (`telecom-mcp` adapters).

---

## Step 4: Docker (three services, run separately)

`docker-compose.yml` defines three services. You can start them one at a time or together.

### Service 1: MongoDB

A single-node replica set on port 27017. Required for the API when using MongoDB storage.

```bash
make docker-mongo
# or: docker compose up -d mongo
```

PowerShell: `.\scripts\dev.ps1 docker-mongo`

**What it does:** starts `mongo:7.0` with replica set `rs0` and a named volume for data.

### Service 2: telecom-middleware (API)

Depends on healthy MongoDB. Exposes port 9000.

```bash
make docker-middleware
# or: docker compose up -d mongo middleware
```

PowerShell: `.\scripts\dev.ps1 docker-middleware`

**What it does:** builds `telecom-middleware` image, connects to MongoDB, uses local
verifier and audience `https://api.telecom.example/v1`.

Seed demo data after the API is up:

```bash
docker compose exec -T middleware telecom-middleware seed
```

### Service 3: telecom-mcp (tool server)

Depends on healthy middleware. Exposes port 8080.

```bash
make docker-mcp
# or: docker compose up -d mongo middleware tools
```

PowerShell: `.\scripts\dev.ps1 docker-mcp`

**What it does:** builds `telecom-mcp` image, calls middleware over HTTP inside the
compose network, uses the same local verifier secret as middleware.

### All three, seeded (one command)

```bash
make demo
```

PowerShell: `.\scripts\dev.ps1 demo`

Stop the stack:

```bash
make down
```

PowerShell: `.\scripts\dev.ps1 down`

---

## Why Auth0 errors happened (and how this repo avoids them)

Earlier failures (`token_invalid`, `token lifetime exceeds the permitted maximum`) came
from `.env` files pointing at Auth0 (`IDENTITY_VERIFIER=jwks`) while using local dev
tokens or long-lived Auth0 API settings.

**Local profile (default after `make setup`):**

| Setting | Value | Effect |
|---------|-------|--------|
| `IDENTITY_VERIFIER` | `local` | HS256 with shared secret |
| `TELECOM_MCP_BACKEND` | `fake` | No Auth0, no empty MongoDB |
| `JWT_AUDIENCE` | `https://api.telecom.example/v1` | Same on both services |

**Production Auth0** remains available under `infra/auth0/` for deployments that need it.
It is not part of the default laptop workflow. Use `make wire-auth0` only when Terraform
has been applied and you intend to test against a real tenant.

---

## Make targets (reference)

| Target | Description |
|--------|-------------|
| `make setup` | Install and apply local profile |
| `make local` | Re-apply local profile to existing `.env` files |
| `make run-mcp` | Start tool server on :8080 |
| `make run-middleware` | Start API on :9000 |
| `make token` | Print development bearer token |
| `make health` | GET `/readyz` on both services |
| `make client-tools` | List MCP tools |
| `make client-call` | Call `get_customer_account` for CX-1234 |
| `make docker-mongo` | Docker: MongoDB only |
| `make docker-middleware` | Docker: MongoDB + API |
| `make docker-mcp` | Docker: all three services |
| `make demo` | Docker: up, seed, print URLs |
| `make check` | Lint, types, tests (same as CI) |

---

## Architecture (short)

```
Client / voice agent
        |  Bearer token (local secret in dev)
        v
telecom-mcp :8080          verifies token, maps tools to scopes
        |  HTTP to middleware (or fake backend in dev)
        v
telecom-middleware :9000   verifies token again, enforces record access
        v
MongoDB (Docker) or memory / fake fixtures (laptop)
```

---

## Quality and CI

```bash
make check
```

Runs lint, typecheck, tests, and coverage across services. See `.github/workflows/ci.yml`.

---

## Repository map

```
telecom-mcp/           MCP tool server
telecom-middleware/    REST API
telecom-mcp-client/    CLI client
infra/auth0/           Optional production identity (Terraform)
e2e/                   End-to-end contract tests
GUIDE.md               Full operator guide
docs/decisions/        Architecture decision records
```

---

## License

MIT. See [LICENSE](LICENSE).
