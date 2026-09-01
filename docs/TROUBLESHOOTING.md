# Troubleshooting

## Setup

| Symptom | Fix |
|---------|-----|
| Exit code **78** | Config error: run `uv run --env-file .env telecom-mcp check-config` (or middleware). It lists every problem. |
| `.env` parse at position 0 | UTF-8 BOM. Recreate with `.\scripts\dev.ps1 setup`. |
| Mutual 401 | Different `LOCAL_VERIFIER_SECRET` in the two `.env` files. Re-run setup. |
| 401 after ~1h | Dev tokens expire. Mint again: `.\scripts\dev.ps1 token`. |
| 403 with a valid token | `cx_id` mismatch or no agent assignment. Deny-by-default. |
| `uv sync` lockfile missing | Run from a service directory or `make install` at root. |

## MCP

| Symptom | Fix |
|---------|-----|
| HTTP **307** | Use `/mcp/` (slash). |
| `tools/list` is `[]` | Missing token or role has no tool scopes. |
| `token_invalid` | Verifier mode does not match how the token was minted. Local profile: `IDENTITY_VERIFIER=local`. |
| `readyz` 503, `healthz` 200 | A dependency is down. Body names it. |
| `telecom_middleware` unhealthy | `TELECOM_MCP_BACKEND_BASE_URL` must end with `/api/v1` and middleware must be up. |
| Invoice `6300` vs `63.00` | Minor units in the API; MCP translates. |

## MongoDB

| Symptom | Fix |
|---------|-----|
| Literal `<db_password>` | Edit `.env`. |
| Timeout (Atlas) | Allow-list IP; check outbound 27017. |
| Timeout (local) | `.\scripts\dev.ps1 docker-mongo` and wait. |
| Standalone / no transactions | Something else on 27017. Stop it or point at the replica set. |

## VS Code / PowerShell

| Symptom | Fix |
|---------|-----|
| `ArgumentOutOfRangeException` / `top Actual value was -1` | PSReadLine paste bug. New terminal; `deactivate`; use `.\scripts\dev.ps1 client-demo`. |
| `(telecom-mcp-tools)` in the prompt | `deactivate`. Never Activate.ps1. |
| Hollow breakpoints | Use the launch config without uvicorn reload. |
