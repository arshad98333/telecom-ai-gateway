<div align="center">

# Telecom Agentic AI Support

**A voice agent that can answer a telecom customer's questions and raise requests on their behalf —
and cannot move money, change a contract, or read someone else's account.**

Not because it was told not to. Because eight authorization stages, two independent services
and a hash-chained audit trail make it impossible, and there are 1,023 tests that prove it.

<br>

[![ci](https://img.shields.io/github/actions/workflow/status/arshad98333/telecom-ai-gateway/ci.yml?branch=main&label=ci&logo=githubactions&logoColor=white&style=for-the-badge)](https://github.com/arshad98333/telecom-ai-gateway/actions/workflows/ci.yml)
[![mongo](https://img.shields.io/github/actions/workflow/status/arshad98333/telecom-ai-gateway/mongo.yml?branch=main&label=mongo%20suite&logo=mongodb&logoColor=white&style=for-the-badge)](https://github.com/arshad98333/telecom-ai-gateway/actions/workflows/mongo.yml)
[![tests](https://img.shields.io/badge/tests-1023%20passing-2ea44f?style=for-the-badge&logo=pytest&logoColor=white)](#quality-bar)
[![coverage](https://img.shields.io/badge/coverage-%E2%89%A595%25-2ea44f?style=for-the-badge)](#quality-bar)
[![license](https://img.shields.io/badge/license-MIT-blue?style=for-the-badge)](LICENSE)

[![python](https://img.shields.io/badge/python-3.11%20|%203.12%20|%203.13-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![uv](https://img.shields.io/badge/deps-uv%20locked-DE5FE9?style=flat-square&logo=uv&logoColor=white)](https://docs.astral.sh/uv/)
[![ruff](https://img.shields.io/badge/lint-ruff-D7FF64?style=flat-square&logo=ruff&logoColor=black)](https://docs.astral.sh/ruff/)
[![mypy](https://img.shields.io/badge/types-mypy%20strict-1F5082?style=flat-square)](https://mypy-lang.org/)
[![mcp](https://img.shields.io/badge/protocol-MCP-000000?style=flat-square)](https://modelcontextprotocol.io/)
[![fastapi](https://img.shields.io/badge/api-FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![mongodb](https://img.shields.io/badge/store-MongoDB%20replica%20set-47A248?style=flat-square&logo=mongodb&logoColor=white)](https://www.mongodb.com/)
[![auth0](https://img.shields.io/badge/identity-Auth0-EB5424?style=flat-square&logo=auth0&logoColor=white)](https://auth0.com/)
[![docker](https://img.shields.io/badge/run-Docker%20Compose-2496ED?style=flat-square&logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![terraform](https://img.shields.io/badge/tenant-Terraform-7B42BC?style=flat-square&logo=terraform&logoColor=white)](infra/auth0)

<br>

**[📖 The Guide](GUIDE.md)** · **[🧭 Decisions](docs/decisions/)** · **[🤝 Contributing](CONTRIBUTING.md)** · **[📋 The brief](docs/brief/)**

</div>

---

## ⚡ Run it

```bash
git clone https://github.com/arshad98333/telecom-ai-gateway.git
cd telecom-ai-gateway
make setup      # .env files, one shared secret, dependencies, a config check
make demo       # Mongo + both services in Docker, seeded
```

```bash
curl -s localhost:9000/readyz     # the API
curl -s localhost:8080/readyz     # the tool server
```

That is the whole setup. No database to install, no Auth0 account, no credentials — the
development defaults are a local verifier and a seeded replica set in Docker. `make setup`
is safe to re-run and never touches an existing `.env`.

> **Prerequisites:** [uv](https://docs.astral.sh/uv/getting-started/installation/), Docker, and `make`. Nothing else — uv fetches Python itself.

<br>

<div align="center">

### 👉 **Everything else lives in [`GUIDE.md`](GUIDE.md)** 👈

*One file, in the order you need it: run it → understand it → change it → test it →
connect a real database and a real Auth0 tenant → ship it → what to do when you are paged.*

</div>

---

## 🏗 How it fits together

```mermaid
flowchart TB
    A["🎙 Voice agent"]
    B["🛡 telecom-mcp<br/><code>:8080</code><br/><i>the tool server</i>"]
    C["⚙️ telecom-middleware<br/><code>:9000</code><br/><i>the API</i>"]
    D[("🍃 MongoDB<br/>replica set")]
    E["👔 Supervisor console"]

    A -->|"Bearer: the customer's token"| B
    B -->|"the same token, forwarded unchanged<br/>+ X-Service-Authorization<br/>+ X-Correlation-Id"| C
    C -->|"transactions + change streams"| D
    D -.->|"change stream → SSE, live"| E
    E -->|"approve / reject"| C

    style A fill:#1f2937,stroke:#4b5563,color:#f9fafb
    style B fill:#0b3d2e,stroke:#10b981,color:#ecfdf5
    style C fill:#1e3a5f,stroke:#3b82f6,color:#eff6ff
    style D fill:#14532d,stroke:#47A248,color:#f0fdf4
    style E fill:#3b1f4e,stroke:#a855f7,color:#faf5ff
```

Three properties do the work, and all three are deliberate:

| | |
|---|---|
| 🎫 **One token, verified twice** | The tool server never mints a token for the customer — it forwards the one it was given, and the middleware verifies it again. Both hops must agree on issuer, audience, keys and claims. |
| 🕳 **The service account is powerless** | The tool server's own credential travels in a *separate* header and proves only *which service* is calling. A request carrying only that credential is refused for anything touching customer data. **A stolen service credential reads nobody's account.** |
| 🧱 **Two layers, neither trusting the other** | The tool server answers *may this agent call this tool for this customer*. The middleware answers *may this identity read or change this record*. The middleware stays safe even if the tool server is fully compromised. |

---

## 🔐 The security model, in one screen

Every tool call passes eight stages, in this order, deny by default. The first failure ends
the call and the audit record names the stage that decided it.

```
 1  tool scope        ─ is this a tool at all, and is it blocked in v1?
 2  token             ─ does it verify?
 3  tenant            ─ does it carry one?
 4  CX ID             ─ is a customer reference present where one is required?
 5  account ownership ─ may this identity touch *this* account?      ← fails closed
 6  role              ─ is the role one we know?
 7  permission        ─ does the role hold the scope this tool needs?
 8  input schema      ─ does the payload match the frozen v1 contract?
```

Tool scope runs first so an unknown or blocked tool is refused in microseconds, with no
cryptography and no backend lookup. Then guardrails run either side of the backend call —
rate limit → size and shape → unicode → injection → business rules → action budget, and on
the way back a size cap and a secret scan.

| | |
|---|---|
| 🙈 **Refusals are indistinguishable** | "Not found" answers *exactly* like "not yours". Telling them apart would be an enumeration oracle; the distinction lives in the audit trail. |
| 🔗 **Every outcome is recorded** | Accept *and* reject, in a hash-chained log. `telecom-mcp verify-audit` proves the chain is intact. |
| 🔑 **Passcodes are never stored** | Argon2id with a per-customer salt, constant-time verification, rate-limited, locked out. Never read back, never logged, never sent to a model. |
| 💷 **Money is an integer** | 64-bit minor units with the currency beside it. Never a float. |
| 🧊 **v1 approves but never executes** | `refund:approve` is enforced; the executing side stays dark until the finance reconciliation exists. |

---

## 🧪 Quality bar

| | telecom-mcp | telecom-middleware |
|---|---|---|
| Tests | **649** passing, ~5s | **374** passing + 43 against a real replica set |
| Coverage floor | **95%**, enforced in CI | **95%**, enforced in CI |
| Types | `mypy` strict | `mypy` strict |
| Lint & format | `ruff` | `ruff` |
| Dependencies | `uv.lock`, `--frozen`, audited every push | `uv.lock`, `--frozen`, audited every push |
| Offline | ✅ no database, no credentials, no network | ✅ falls back to an in-memory store |

```bash
make check       # lint + types + tests + coverage, both services — exactly what CI runs
```

CI runs that on Python 3.11 **and** 3.12, plus the e2e contract suite, gitleaks over the
whole history, bandit, both container smokes, the 43 Mongo tests against an ephemeral
replica set — and `make setup` from a clean clone, twice, so the quickstart above cannot
quietly stop being true.

A `v*.*.*` tag on `production` publishes `telecom-mcp-tools` to PyPI and GHCR, through
TestPyPI first, with a human approval before the real index —
[how to cut one](GUIDE.md#publishing-telecom-mcp-tools-to-pypi).

---

## 🗺 The map

```
├── 📖 GUIDE.md              everything, in the order you need it
├── 🛡 telecom-mcp/          the MCP tool server — 10 tools, 8 live in v1
├── ⚙️ telecom-middleware/   the API and the only writer to MongoDB
├── 🔑 infra/auth0/          the Auth0 tenant as Terraform: API, scopes, roles, login Action
├── 🔗 e2e/                  both services in one process over real HTTP, nothing stubbed
├── ☁️ testsprite/           the external suite that runs from a cloud against a deployed URL
└── 📚 docs/
    ├── decisions/          why it is the way it is — one numbered file, immutable
    └── brief/              the specifications this was built to satisfy, unedited
```

The two services are **git subtrees** with their full history — `git log -- telecom-mcp` is
still the real story of that service, and each still builds, tests and releases on its own.

---

## 🧰 Every command

| | |
|---|---|
| `make` | every target, one line each |
| `make setup` | first run: env files, one shared secret, deps, a config check |
| `make demo` / `make down` | the whole stack in Docker, seeded / stop it |
| `make dev` | the two commands to run the services on your machine |
| `make test` · `make test-fast` · `make test-mongo` | tests |
| `make check` | what CI runs |
| `make wire-auth0` | Terraform outputs into both `.env` files, correctly |
| `make testable` · `make validate` | external testing, without wasting credits |
| `make adr` | the next decision-record number |

---

<div align="center">

**MIT licensed** · Built to the standard in [`docs/production-engineering-guidebook.md`](docs/production-engineering-guidebook.md)

*Every non-obvious choice in this codebase can be traced to a written reason.*

</div>
