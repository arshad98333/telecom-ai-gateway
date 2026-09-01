# Reference: health and MCP

Canonical URLs. Update this file when staging DNS changes; the console copies
from `telecom-console/src/config/endpoints.ts`.

## Staging (Azure Container Apps, UAE North)

Verified 2026-09-02: `/healthz` and `/readyz` returned `"status":"healthy"`.

| What | URL |
|------|-----|
| Base | `https://telecom-mcp-staging.calmfield-7654c7b3.uaenorth.azurecontainerapps.io` |
| Liveness | [GET /healthz](https://telecom-mcp-staging.calmfield-7654c7b3.uaenorth.azurecontainerapps.io/healthz) |
| Readiness | [GET /readyz](https://telecom-mcp-staging.calmfield-7654c7b3.uaenorth.azurecontainerapps.io/readyz) |
| MCP (trailing slash required) | [POST /mcp/](https://telecom-mcp-staging.calmfield-7654c7b3.uaenorth.azurecontainerapps.io/mcp/) |
| KPI | `https://telecom-mcp-staging.calmfield-7654c7b3.uaenorth.azurecontainerapps.io/kpi` |
| Metrics | `https://telecom-mcp-staging.calmfield-7654c7b3.uaenorth.azurecontainerapps.io/metrics` |

`POST /mcp` (no slash) returns **307**. Clients must use `/mcp/` or follow the redirect with a re-POST (PowerShell `Invoke-RestMethod` does not).

Cursor config: [mcp/cursor-mcp.staging.json](../mcp/cursor-mcp.staging.json).

## Local

| What | URL |
|------|-----|
| MCP liveness | `http://127.0.0.1:8080/healthz` |
| MCP readiness | `http://127.0.0.1:8080/readyz` |
| MCP JSON-RPC | `http://127.0.0.1:8080/mcp/` |
| MCP KPI | `http://127.0.0.1:8080/kpi` |
| Middleware liveness | `http://127.0.0.1:9000/healthz` |
| Middleware readiness | `http://127.0.0.1:9000/readyz` |
| Middleware OpenAPI | `http://127.0.0.1:9000/docs` |
| Ops console | `http://127.0.0.1:5173` |

Probe both: `make health` or `.\scripts\dev.ps1 health`.

## MCP tools (v1, frozen)

| Tool | Kind | Notes |
|------|------|-------|
| `get_customer_account` | read | `cx_id` |
| `get_active_services` | read | `cx_id` |
| `get_invoice_summary` | read | `cx_id` |
| `get_network_status` | read | `cx_id` |
| `get_order_status` | read | `cx_id` |
| `create_support_ticket` | write | idempotency key |
| `schedule_callback` | write | idempotency key |
| `request_refund_approval` | write | idempotency key; no money movement |

Accept: `application/json, text/event-stream`.

## Demo accounts

| ID | Where it exists |
|----|-----------------|
| `CX-1234` | Fake MCP backend (laptop, `TELECOM_MCP_BACKEND=fake`) |
| `CX-2001`–`CX-2012` | Middleware bulk seed (`telecom-middleware seed`) |

A 404 on `CX-2001` against fake backend is expected. Use `CX-1234` locally unless you seeded Mongo and pointed MCP at HTTP middleware.

## Headers the tool server sends to the API

`Authorization`, `X-Service-Authorization`, `X-Correlation-Id`.
