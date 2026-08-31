<#
.SYNOPSIS
    Start the middleware and the MCP server, each in its own window, and wait until
    the whole chain reports ready.

.DESCRIPTION
    Frees ports 9000 and 8080 first: a stale process holding a port is why a restart
    can appear to succeed and still serve the old configuration.

.EXAMPLE
    .\scripts\serve.ps1
#>
[CmdletBinding()]
param(
    [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = 'Stop'

$McpRoot        = Split-Path -Parent $PSScriptRoot
$MiddlewareRoot = Join-Path (Split-Path -Parent $McpRoot) 'telecom-middleware'

if (-not (Test-Path -LiteralPath $MiddlewareRoot)) {
    throw "Expected the middleware beside this project at: $MiddlewareRoot"
}

function Stop-Listener {
    param([int]$Port)
    $owners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
              Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($processId in $owners) {
        Write-Host ("  stopping process {0} on port {1}" -f $processId, $Port) -ForegroundColor DarkGray
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }
}

function Start-Service0 {
    param([string]$Title, [string]$WorkingDirectory, [string]$Command)
    $inner = "`$host.UI.RawUI.WindowTitle = '$Title'; Set-Location -LiteralPath '$WorkingDirectory'; $Command"
    Start-Process -FilePath 'powershell.exe' `
        -ArgumentList @('-NoExit', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $inner)
}

function Wait-Endpoint {
    param([string]$Url, [string]$Name, [int]$Seconds)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-RestMethod -Uri $Url -TimeoutSec 3
            return $response
        } catch {
            Start-Sleep -Milliseconds 700
        }
    }
    throw "$Name did not come up within $Seconds seconds. Check its window for the error."
}

Write-Host 'Freeing ports 9000 and 8080' -ForegroundColor Cyan
Stop-Listener -Port 9000
Stop-Listener -Port 8080
Start-Sleep -Seconds 1

Write-Host 'Starting the middleware on :9000' -ForegroundColor Cyan
Start-Service0 -Title 'telecom-middleware :9000' -WorkingDirectory $MiddlewareRoot `
    -Command 'uv run --env-file .env telecom-middleware serve'
$null = Wait-Endpoint -Url 'http://127.0.0.1:9000/healthz' -Name 'The middleware' -Seconds $TimeoutSeconds
Write-Host '  middleware is up' -ForegroundColor Green

Write-Host 'Starting the MCP server on :8080' -ForegroundColor Cyan
Start-Service0 -Title 'telecom-mcp :8080' -WorkingDirectory $McpRoot `
    -Command 'uv run --env-file .env telecom-mcp serve --transport http'
$ready = Wait-Endpoint -Url 'http://127.0.0.1:8080/readyz' -Name 'The MCP server' -Seconds $TimeoutSeconds

Write-Host ''
Write-Host ("Chain status: {0}" -f $ready.status) -ForegroundColor Cyan
foreach ($component in $ready.components) {
    $colour = if ($component.status -eq 'healthy') { 'Green' } else { 'Red' }
    Write-Host ("  {0,-22} {1}" -f $component.name, $component.status) -ForegroundColor $colour
}

if ($ready.status -ne 'healthy') {
    Write-Host ''
    Write-Host 'The MCP server cannot reach the middleware. Check the middleware window.' -ForegroundColor Yellow
    exit 1
}

Write-Host ''
Write-Host 'Both services are running. Verify with:  .\scripts\smoke.ps1' -ForegroundColor Cyan
