# Telecom Agentic AI Support

A voice agent that can answer a telecom customer's questions and raise requests on
their behalf — and cannot move money, change a contract or read someone else's account,
because the checks that stop it are in the code and are tested.

Two services:

| | |
|---|---|
| **`telecom-mcp/`** | The MCP tool server. What the voice agent calls. Enforces access; holds no business rules and never touches the database. |
| **`telecom-middleware/`** | The API. Holds the rules and the data, and is the only writer to MongoDB. |

Everything else is the design (`docs/`), the Auth0 tenant as Terraform (`infra/`), and
the suites that prove the two work together (`e2e/`, `testsprite/`).

---

## Run it

You need [uv](https://docs.astral.sh/uv/getting-started/installation/), Docker, and
`make`. Nothing else.

```bash
git clone https://github.com/arshad98333/telecom-ai-gateway.git
cd telecom-ai-gateway
make setup      # .env files, one shared dev secret, dependencies, a config check
make demo       # Mongo + both services in Docker, seeded
```

Then, in another terminal:

```bash
curl localhost:9000/readyz     # the API
curl localhost:8080/readyz     # the tool server
make down                      # stop
```

`make setup` is safe to re-run and never touches an existing `.env`.

**Everything else is in [`GUIDE.md`](GUIDE.md)** — one file, in the order you need it:
run it, understand it, change it, test it, connect a real database and a real Auth0
tenant, ship it, and what to do when you are paged.

### Without Docker

```bash
make setup
make dev        # prints the two commands, one per terminal
```

The middleware falls back to an in-memory store, so this needs no database at all.

---

## The commands

```bash
make            # every target, with a line each
make test       # both test suites
make check      # lint, types, coverage — exactly what CI runs
make test-mongo # the MongoDB suite; needs `make up` first
```

Each service also works on its own — `make -C telecom-mcp check` — and has its own
README, lock file, Dockerfile and CI.

---

## Reading it

1. **[`GUIDE.md`](GUIDE.md)** — everything, in the order you need it: run it, understand
   it, change it, ship it. The stakeholders, the data model, the RBAC matrix and how an
   approval travels from a customer's request to a supervisor's decision are in sections
   3, 4 and 10.
2. **`docs/decisions/`** — why each non-obvious choice was made, one numbered file each.
   Start with 0002 (why there are two services) and 0006 (why the service account is
   powerless on its own).
3. **`telecom-mcp/README.md`** and **`telecom-middleware/README.md`** — each service in
   its own terms.
4. **`docs/brief/`** — the specifications this was built to satisfy, unedited.

## Going further

| | |
|---|---|
| A real MongoDB cluster | [`GUIDE.md` §8](GUIDE.md#8-the-database) |
| A real Auth0 tenant | [`GUIDE.md` §9](GUIDE.md#9-identity-from-the-dev-secret-to-auth0) |
| Testing against a deployed URL | [`GUIDE.md` §14](GUIDE.md#14-testing-against-a-deployed-url) |
| Working in VS Code | [`GUIDE.md` §6](GUIDE.md#6-make-a-change) |
| Contributing | `CONTRIBUTING.md` |

MIT licensed.
