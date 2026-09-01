# Local development helper for Windows (PowerShell).
# Use when GNU Make is not installed. Same targets as the root Makefile.

param(
    [Parameter(Position = 0)]
    [ValidateSet(
        "setup", "local", "fresh", "run-middleware", "run-mcp", "token",
        "health", "client-tools", "client-call", "client-demo",
        "clear-ports", "docker-mongo", "docker-middleware", "docker-mcp",
        "demo", "down", "console-dev", "help"
    )]
    [string]$Command = "help"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

function Require-Tool([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name is not on PATH."
    }
}

function Invoke-Root([string[]]$Args) {
    Push-Location $Root
    try {
        & @Args
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } finally {
        Pop-Location
    }
}

function Get-DevToken {
    Require-Tool uv
    Push-Location (Join-Path $Root "telecom-mcp")
    try {
        $t = uv run --env-file .env python scripts/mint_dev_token.py
        if ($LASTEXITCODE -ne 0) { throw "mint_dev_token.py failed" }
        return $t.Trim()
    } finally {
        Pop-Location
    }
}

function Set-McpClientEnv {
    $env:TELECOM_MCP_URL = "http://127.0.0.1:8080"
    if (-not $env:TELECOM_MCP_ACCESS_TOKEN) {
        Write-Host "Minting development token..."
        $env:TELECOM_MCP_ACCESS_TOKEN = Get-DevToken
    }
}

function Invoke-McpClientCall {
    param(
        [string]$Tool,
        [hashtable]$Payload
    )
    $json = $Payload | ConvertTo-Json -Compress
    uv run telecom-mcp-client call $Tool --json $json
}

function Test-TcpPort([int]$Port) {
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Require-McpServer {
    if (Test-TcpPort 8080) { return }
    Write-Host ""
    Write-Host "ERROR: MCP tool server is not running on port 8080." -ForegroundColor Red
    Write-Host ""
    Write-Host "client-demo only CALLS the server. It does not start it."
    Write-Host "Open another terminal at the repo root and run:"
    Write-Host ""
    Write-Host "  .\scripts\dev.ps1 run-mcp" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Wait for: Uvicorn running on http://127.0.0.1:8080"
    Write-Host "Then run client-demo again."
    Write-Host ""
    Write-Host "Or use Docker for everything in one shot:"
    Write-Host "  .\scripts\dev.ps1 demo" -ForegroundColor Cyan
    Write-Host ""
    exit 1
}

switch ($Command) {
    "help" {
        Write-Host @"
telecom-ai-gateway local development (PowerShell)

  .\scripts\dev.ps1 fresh             Clear ports + apply local profile (start here)
  .\scripts\dev.ps1 setup             Install dependencies
  .\scripts\dev.ps1 demo              Docker: mongo + API + MCP + seed (easiest)
  .\scripts\dev.ps1 run-middleware    Terminal 1: API on :9000
  .\scripts\dev.ps1 run-mcp           Terminal 2: tool server on :8080
  .\scripts\dev.ps1 console-dev       Terminal 3: UI on :5173
  .\scripts\dev.ps1 client-demo       Test MCP (server must be running)
  .\scripts\dev.ps1 health            Probe :9000 and :8080
  .\scripts\dev.ps1 token             Print bearer token
  .\scripts\dev.ps1 clear-ports       Free 8080, 9000, 5173

Do NOT run Activate.ps1. Use uv run via these scripts.

Guide:  docs/DEVELOPER.md  (UI: http://localhost:5173/guide)
Health: docs/REFERENCE.md
Azure:  docs/AZURE_DEPLOY.md
"@
    }
    "fresh" {
        Write-Host "==> Clearing ports 8080, 9000, 5173"
        foreach ($port in 8080, 9000, 5173) {
            Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue |
                Select-Object -ExpandProperty OwningProcess -Unique |
                ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
        }
        Require-Tool python
        Invoke-Root python scripts/local_env.py
        Write-Host ""
        Write-Host "Fresh local profile applied. Next:" -ForegroundColor Green
        Write-Host ""
        Write-Host "  Option A (Docker, one command):" -ForegroundColor Cyan
        Write-Host "    .\scripts\dev.ps1 demo"
        Write-Host "    .\scripts\dev.ps1 client-demo"
        Write-Host ""
        Write-Host "  Option B (native, three terminals for full console):" -ForegroundColor Cyan
        Write-Host "    Terminal 1: .\scripts\dev.ps1 run-mcp        (required)"
        Write-Host "    Terminal 2: .\scripts\dev.ps1 run-middleware (audit/approvals - fixes red middleware card)"
        Write-Host "    Terminal 3: .\scripts\dev.ps1 client-demo"
        Write-Host ""
    }
    "setup" {
        Require-Tool python
        Invoke-Root python scripts/setup.py
    }
    "local" {
        Require-Tool python
        Invoke-Root python scripts/local_env.py
    }
    "run-middleware" {
        Require-Tool uv
        Push-Location (Join-Path $Root "telecom-middleware")
        uv run --env-file .env telecom-middleware serve --reload
    }
    "run-mcp" {
        Require-Tool uv
        Push-Location (Join-Path $Root "telecom-mcp")
        uv run --env-file .env telecom-mcp serve --transport http
    }
    "token" {
        Get-DevToken
    }
    "health" {
        if (Test-TcpPort 9000) {
            Invoke-RestMethod http://127.0.0.1:9000/readyz | ConvertTo-Json -Depth 5
        } else {
            Write-Host "middleware :9000 - NOT LISTENING" -ForegroundColor Yellow
        }
        if (Test-TcpPort 8080) {
            Invoke-RestMethod http://127.0.0.1:8080/readyz | ConvertTo-Json -Depth 5
        } else {
            Write-Host "mcp :8080 - NOT LISTENING" -ForegroundColor Yellow
        }
    }
    "clear-ports" {
        foreach ($port in 8080, 9000, 5173) {
            Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue |
                Select-Object -ExpandProperty OwningProcess -Unique |
                ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
        }
        Write-Host "Ports 8080, 9000, 5173 cleared."
    }
    "client-tools" {
        Require-Tool uv
        Require-McpServer
        Set-McpClientEnv
        Push-Location (Join-Path $Root "telecom-mcp-client")
        uv run telecom-mcp-client list-tools
    }
    "client-call" {
        Require-Tool uv
        Require-McpServer
        Set-McpClientEnv
        Push-Location (Join-Path $Root "telecom-mcp-client")
        uv run telecom-mcp-client call get_customer_account --json (@{ cx_id = "CX-1234" } | ConvertTo-Json -Compress)
    }
    "client-demo" {
        Require-Tool uv
        Require-McpServer
        Set-McpClientEnv
        Write-Host "TELECOM_MCP_URL=$($env:TELECOM_MCP_URL)"
        $preview = $env:TELECOM_MCP_ACCESS_TOKEN.Substring(0, [Math]::Min(24, $env:TELECOM_MCP_ACCESS_TOKEN.Length))
        Write-Host "Token (first 24 chars): ${preview}..."
        Push-Location (Join-Path $Root "telecom-mcp-client")
        Write-Host ""
        Write-Host "==> list-tools"
        uv run telecom-mcp-client list-tools
        Write-Host ""
        Write-Host "==> get_customer_account CX-1234"
        uv run telecom-mcp-client call get_customer_account --json (@{ cx_id = "CX-1234" } | ConvertTo-Json -Compress)
    }
    "docker-mongo" {
        Require-Tool docker
        Invoke-Root docker compose up -d mongo
    }
    "docker-middleware" {
        Require-Tool docker
        Invoke-Root docker compose up -d mongo middleware
    }
    "docker-mcp" {
        Require-Tool docker
        Invoke-Root docker compose up -d mongo middleware tools
    }
    "demo" {
        Require-Tool docker
        Invoke-Root docker compose up -d --wait --wait-timeout 240
        Invoke-Root docker compose exec -T middleware telecom-middleware seed
        Write-Host ""
        Write-Host "Stack is up:" -ForegroundColor Green
        Write-Host "  middleware  http://localhost:9000/readyz"
        Write-Host "  mcp         http://localhost:8080/readyz"
        Write-Host "  console     .\scripts\dev.ps1 console-dev  (optional)"
        Write-Host ""
        Write-Host "Test now:  .\scripts\dev.ps1 client-demo"
    }
    "console-dev" {
        Require-Tool npm
        Push-Location (Join-Path $Root "telecom-console")
        if (-not (Test-Path node_modules)) { npm install }
        npm run dev
    }
    "down" {
        Require-Tool docker
        Invoke-Root docker compose down
    }
}
