# Developer experience

Windows: `.\scripts\dev.ps1 <target>`. Linux/macOS: `make <target>`.

Do not `Activate.ps1`. `uv run` owns the environment.

## 15 minutes to a tool call

1. Install [uv](https://docs.astral.sh/uv/) 0.12.3+, Python 3.11+, Node 20+ (console only).
2. At the repo root:

```powershell
.\scripts\dev.ps1 setup
.\scripts\dev.ps1 run-mcp
```

3. New terminal:

```powershell
.\scripts\dev.ps1 client-demo
```

That mints a local token, lists tools, and calls `get_customer_account` for `CX-1234` (fake backend seed).

Optional console:

```powershell
.\scripts\dev.ps1 run-middleware   # terminal 2 — audit/approvals
.\scripts\dev.ps1 console-dev      # terminal 3 — http://127.0.0.1:5173
```

In-product steps: http://127.0.0.1:5173/guide

## Docker (API + Mongo + MCP)

```powershell
.\scripts\dev.ps1 demo
.\scripts\dev.ps1 client-demo
```

Stop: `.\scripts\dev.ps1 down`

## Connect Cursor

Local: copy [mcp/cursor-mcp.json](../mcp/cursor-mcp.json) to `.cursor/mcp.json`, replace the token with `.\scripts\dev.ps1 token`.

Staging: [mcp/cursor-mcp.staging.json](../mcp/cursor-mcp.staging.json). URLs in [REFERENCE.md](REFERENCE.md).

Restart Cursor. Confirm `telecom-mcp-tools` is connected.

## Quality gate (same as CI)

```bash
make check
```

Mongo replica-set tests: `make up` then `make test-mongo`.

## Layout

```
telecom-mcp/            tool server :8080
telecom-middleware/     REST API    :9000
telecom-mcp-client/     CLI
telecom-console/        ops UI      :5173
mcp/                    Cursor / Claude JSON
scripts/                setup, local profile, Azure deploy
docs/                   this tree
e2e/                    contract tests
testsprite/             external suite
```

## Staging

Health and MCP: [REFERENCE.md](REFERENCE.md). Deploy: [AZURE_DEPLOY.md](AZURE_DEPLOY.md).
