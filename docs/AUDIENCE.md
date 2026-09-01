# Who this works for (Perito)

This stack is Perito’s **telecom customer-support control plane**: an AI agent
calls eight MCP tools; the tool server authorizes the caller; the API owns
data and money rules.

It is not a generic chatbot demo. It is not a CRM. Customers never talk to
MCP. An agent (human or model) acts **as** a customer identity.

## End-to-end paths

| Actor | Job | Surface | Success |
|-------|-----|---------|---------|
| Perito CX agent | Account, bill, outage, ticket, callback | Ops console + MCP client | Reads return owned data; writes are idempotent |
| Perito supervisor | Refunds | Console Approvals | Request exists; no money moves until a human says yes |
| Perito platform engineer | Run, health, deploy | CLI, `/healthz`, `/readyz`, Azure | Ready means dependencies answered |
| Perito AI engineer | Cursor / Claude tools | `POST /mcp/` | Catalogue is the frozen eight tools |
| Customer (`CX-*`) | Indirect | Voice or agent session | Token `cx_id` matches the record; cross-account is 403 |

## What v1 does not do

- Change plan, cancel service, or move money
- List tickets over MCP (create only)
- Require Auth0 on a laptop (local HS256 + fake backend)

## Ownership

| Change | Package |
|--------|---------|
| Tool contract, authz order, guardrails | `telecom-mcp/` |
| Data, tenancy, approvals, audit chain | `telecom-middleware/` |
| CLI against the tool server | `telecom-mcp-client/` |
| Ops UI | `telecom-console/` |
