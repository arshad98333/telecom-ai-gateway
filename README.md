# Perito telecom support control plane

Authorized MCP tools in front of a telecom API. The tool server decides **who may call**;
the API decides **what is true**. Refunds stop at a human.

**Who:** [docs/AUDIENCE.md](docs/AUDIENCE.md)  
**How:** [docs/DEVELOPER.md](docs/DEVELOPER.md)  
**Health + MCP URLs:** [docs/REFERENCE.md](docs/REFERENCE.md)

```
Agent / Cursor / Claude
        |  Bearer (local HS256 on a laptop)
        v
telecom-mcp :8080     POST /mcp/   /healthz  /readyz
        |
        v
telecom-middleware :9000     /api/v1    (or fake backend in dev)
        |
        v
MongoDB replica set  or  in-memory / fixtures
```

| Package | Role | Port |
|---------|------|------|
| `telecom-mcp/` | Tool gateway | 8080 |
| `telecom-middleware/` | Business API | 9000 |
| `telecom-mcp-client/` | CLI | — |
| `telecom-console/` | Ops UI | 5173 |

---

## Staging (running)

| | |
|--|--|
| Health | https://telecom-mcp-staging.calmfield-7654c7b3.uaenorth.azurecontainerapps.io/healthz |
| Ready | https://telecom-mcp-staging.calmfield-7654c7b3.uaenorth.azurecontainerapps.io/readyz |
| MCP | https://telecom-mcp-staging.calmfield-7654c7b3.uaenorth.azurecontainerapps.io/mcp/ |

Cursor: [mcp/cursor-mcp.staging.json](mcp/cursor-mcp.staging.json)

---

## Laptop (no Auth0, no Docker)

```powershell
.\scripts\dev.ps1 setup
.\scripts\dev.ps1 run-mcp          # leave running
.\scripts\dev.ps1 client-demo      # other terminal
```

Linux/macOS: `make setup` then `make run-mcp`. Token: `make token`. Fake demo customer: `CX-1234`.

Prerequisites: [uv](https://docs.astral.sh/uv/) ≥ 0.12.3, Python 3.11+.

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

---

## Docker

```powershell
.\scripts\dev.ps1 demo
```

Middleware `http://127.0.0.1:9000/readyz`, MCP `http://127.0.0.1:8080/readyz`. Seeded `CX-2001`–`CX-2012`.

---

## Console

```powershell
.\scripts\dev.ps1 run-mcp
.\scripts\dev.ps1 run-middleware
.\scripts\dev.ps1 console-dev
```

http://127.0.0.1:5173 — Guide, health, audit, approvals, MCP JSON for Cursor.

---

## Quality

```bash
make check
```

Same as `.github/workflows/ci.yml`.

---

## Docs map

| File | Content |
|------|---------|
| [docs/AUDIENCE.md](docs/AUDIENCE.md) | Perito actors, end to end |
| [docs/DEVELOPER.md](docs/DEVELOPER.md) | DX path |
| [docs/REFERENCE.md](docs/REFERENCE.md) | Endpoints and tools |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Failures |
| [docs/AZURE_DEPLOY.md](docs/AZURE_DEPLOY.md) | Container Apps |
| [docs/decisions/](docs/decisions/) | ADRs |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Check / commits / branches |
| [mcp/README.md](mcp/README.md) | Agent config |

MIT. [LICENSE](LICENSE).
