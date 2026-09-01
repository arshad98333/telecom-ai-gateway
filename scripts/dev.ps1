# Local development helper for Windows (PowerShell).
# Use when GNU Make is not installed. Same targets as the root Makefile.

param(
    [Parameter(Position = 0)]
    [ValidateSet(
        "setup", "local", "run-middleware", "run-mcp", "token",
        "health", "client-tools", "client-call", "docker-mongo",
        "docker-middleware", "docker-mcp", "demo", "down", "help"
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

switch ($Command) {
    "help" {
        Write-Host @"
telecom-ai-gateway local development (PowerShell)

  .\scripts\dev.ps1 setup            Install dependencies and apply local profile
  .\scripts\dev.ps1 local            Re-apply local profile (no Auth0)
  .\scripts\dev.ps1 run-middleware     Terminal 1: API on :9000
  .\scripts\dev.ps1 run-mcp            Terminal 2: tool server on :8080
  .\scripts\dev.ps1 token            Print a bearer token
  .\scripts\dev.ps1 health             Probe :9000 and :8080
  .\scripts\dev.ps1 client-tools       List MCP tools (set token first)
  .\scripts\dev.ps1 client-call        Call get_customer_account for CX-1234

Docker (three services, run separately or together):
  .\scripts\dev.ps1 docker-mongo       MongoDB only
  .\scripts\dev.ps1 docker-middleware  MongoDB + API
  .\scripts\dev.ps1 docker-mcp         MongoDB + API + tool server
  .\scripts\dev.ps1 demo               Start all, seed, print URLs
  .\scripts\dev.ps1 down               Stop Docker stack
"@
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
        Require-Tool uv
        Push-Location (Join-Path $Root "telecom-mcp")
        uv run --env-file .env python scripts/mint_dev_token.py
    }
    "health" {
        Invoke-RestMethod http://127.0.0.1:9000/readyz | ConvertTo-Json -Depth 5
        Invoke-RestMethod http://127.0.0.1:8080/readyz | ConvertTo-Json -Depth 5
    }
    "client-tools" {
        Require-Tool uv
        if (-not $env:TELECOM_MCP_ACCESS_TOKEN) {
            throw "Set TELECOM_MCP_ACCESS_TOKEN first. Run: .\scripts\dev.ps1 token"
        }
        $env:TELECOM_MCP_URL = "http://127.0.0.1:8080"
        Push-Location (Join-Path $Root "telecom-mcp-client")
        uv run telecom-mcp-client list-tools
    }
    "client-call" {
        Require-Tool uv
        if (-not $env:TELECOM_MCP_ACCESS_TOKEN) {
            throw "Set TELECOM_MCP_ACCESS_TOKEN first. Run: .\scripts\dev.ps1 token"
        }
        $env:TELECOM_MCP_URL = "http://127.0.0.1:8080"
        Push-Location (Join-Path $Root "telecom-mcp-client")
        uv run telecom-mcp-client call get_customer_account --json '{\"cx_id\": \"CX-1234\"}'
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
        Write-Host "middleware  http://localhost:9000/readyz"
        Write-Host "mcp         http://localhost:8080/readyz"
    }
    "down" {
        Require-Tool docker
        Invoke-Root docker compose down
    }
}
