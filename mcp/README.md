# MCP agent configuration

Copy these files to connect AI agents to the local tool server.

## Prerequisites

1. Start the MCP server: `make run-mcp` or `.\scripts\dev.ps1 run-mcp`
2. Mint a token: `make token` (PowerShell: see README)
3. Replace `REPLACE_WITH_MAKE_TOKEN` in the JSON below

## Cursor

Copy `mcp/cursor-mcp.json` to your project as `.cursor/mcp.json` or merge into user MCP settings.

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

Restart Cursor after saving. Open MCP settings and confirm `telecom-mcp-tools` is connected.

## Claude Desktop

Merge `mcp/claude-desktop-config.json` into:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

## Verify end to end

```bash
make token
export TELECOM_MCP_ACCESS_TOKEN="$(make -s token)"
make client-tools
```

PowerShell:

```powershell
cd telecom-mcp-client
$env:TELECOM_MCP_URL = "http://127.0.0.1:8080"
$env:TELECOM_MCP_ACCESS_TOKEN = (cd ..\telecom-mcp; uv run --env-file .env python scripts/mint_dev_token.py)
uv run telecom-mcp-client list-tools
```

## Azure

When deployed, set the URL to your Container App hostname:

`https://<your-mcp-app>.azurecontainerapps.io/mcp/`

Use an Auth0 access token in production, or the local verifier secret only in non-production environments.
