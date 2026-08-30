# Telecom Agentic AI Support — platform

Three repositories, one system.

| Repository | What it is |
|---|---|
| `telecom-mcp/` | The MCP tool server. Security-enforcing gateway the voice agent calls. |
| `telecom-middleware/` | The customer data and approval service. The only writer to MongoDB. |
| this one | The design, the Auth0 tenant as Terraform, and the end-to-end suite that proves the pieces work together. |

```
docs/SYSTEM-DESIGN.md   the data model, the RBAC matrix, the realtime design
infra/auth0/            the Auth0 tenant: API, scopes, roles, clients, the login Action
e2e/                    both services in one process, talking over real HTTP
```

## Start here

1. `docs/SYSTEM-DESIGN.md` — how the stakeholders, the data and the events fit together.
2. `telecom-middleware/README.md` — run the API against an in-memory store in one command.
3. `telecom-mcp/README.md` — run the tool server against it.
4. `infra/auth0/README.md` — point both at a real Auth0 tenant.

## The end-to-end suite

```bash
cd e2e
uv sync --frozen
uv run pytest
```

It runs both services in one process, with the tool server calling the middleware over
a real HTTP transport and nothing stubbed between them. A contract disagreement between
the two — a renamed field, a changed shape, a permission that no longer lines up — fails
here rather than in staging.
