# MCP agent configuration

## Local

1. `.\scripts\dev.ps1 run-mcp` (or `make run-mcp`)
2. `.\scripts\dev.ps1 token`
3. Copy [cursor-mcp.json](cursor-mcp.json) to `.cursor/mcp.json` and replace `REPLACE_WITH_MAKE_TOKEN`

```json
{
  "mcpServers": {
    "telecom-mcp-tools": {
      "url": "http://127.0.0.1:8080/mcp/",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN"
      }
    }
  }
}
```

Health: `http://127.0.0.1:8080/healthz` · Ready: `http://127.0.0.1:8080/readyz`

Claude Desktop: merge [claude-desktop-config.json](claude-desktop-config.json) into the Claude config file.

## Staging

URLs: [docs/REFERENCE.md](../docs/REFERENCE.md)

Copy [cursor-mcp.staging.json](cursor-mcp.staging.json). Current host:

- Health: https://telecom-mcp-staging.calmfield-7654c7b3.uaenorth.azurecontainerapps.io/healthz
- MCP: https://telecom-mcp-staging.calmfield-7654c7b3.uaenorth.azurecontainerapps.io/mcp/

Always POST `/mcp/` (trailing slash).

## Verify

```powershell
.\scripts\dev.ps1 client-demo
```
