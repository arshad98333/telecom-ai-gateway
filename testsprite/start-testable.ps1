<#
.SYNOPSIS
    Bring both services up in the external-test profile, mint a matching token, and
    prove the credential works before a single TestSprite credit is spent.

.DESCRIPTION
    The two reports that prompted this script both failed for the same reason and
    neither said so: the services were running with `IDENTITY_VERIFIER=jwks` and
    `SERVICE_AUTH=jwks`, while the runner held an HS256 development token and could
    only send one header. Every call died at the door. Sixteen of eighteen tests came
    back as product defects that were nothing of the kind.

    This makes that combination impossible to reach by accident. It layers
    testsprite/profile/*.env over each service's own .env - changing only the settings
    that stop an outside runner getting in - starts both, mints one token that both
    hops accept, and runs preflight.py against the pair. If preflight fails, stop:
    a paid run started now would only rediscover what it just told you for free.

    The relaxed settings are development-only by construction. The settings validator
    refuses them when ENV=production, and the app logs a warning every start while
    they are live on a non-loopback interface.

.PARAMETER PublicMcpUrl
    The https URL your tunnel gives the tool server. Optional: given, preflight runs
    against the public URL as well as the local one, which is what actually catches a
    tunnel pointed at the wrong port.

.EXAMPLE
    .\start-testable.ps1
    .\start-testable.ps1 -PublicMcpUrl https://a.trycloudflare.com -PublicMiddlewareUrl https://b.trycloudflare.com
#>
[CmdletBinding()]
param(
    [string]$PublicMcpUrl,
    [string]$PublicMiddlewareUrl,
    [int]$TokenMinutes = 90,
    [string]$CxId = 'CX-1234',
    [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = 'Stop'

$Root           = Split-Path -Parent $PSScriptRoot
$McpRoot        = Join-Path $Root 'telecom-mcp'
$MiddlewareRoot = Join-Path $Root 'telecom-middleware'
$ProfileDir     = Join-Path $PSScriptRoot 'profile'

foreach ($path in @($McpRoot, $MiddlewareRoot, $ProfileDir)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing: $path" }
}
foreach ($name in @('.env')) {
    foreach ($service in @($McpRoot, $MiddlewareRoot)) {
        if (-not (Test-Path -LiteralPath (Join-Path $service $name))) {
            throw "No $name in $service. Copy $name.example and fill it in first."
        }
    }
}

function Get-EnvValue {
    param([string]$File, [string]$Key)
    foreach ($line in Get-Content -LiteralPath $File) {
        if ($line -match "^\s*$([regex]::Escape($Key))\s*=\s*(.*)$") { return $Matches[1].Trim() }
    }
    return $null
}

function Stop-Listener {
    param([int]$Port)
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object {
            Write-Host ("  stopping process {0} on port {1}" -f $_, $Port) -ForegroundColor DarkGray
            Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
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
        try { return Invoke-RestMethod -Uri $Url -TimeoutSec 3 } catch { Start-Sleep -Milliseconds 700 }
    }
    throw "$Name did not come up within $Seconds seconds. Read its window - the settings validator names every problem at once."
}

# --- the two secrets that have to agree ---------------------------------------------
# One token is verified at both hops, so one secret signs it. When these drift the
# symptom is `token_invalid` from whichever service you happen to call first, which
# reads as a bug in that service and is not.
$mcpSecret = Get-EnvValue -File (Join-Path $McpRoot '.env')        -Key 'TELECOM_MCP_LOCAL_VERIFIER_SECRET'
$mwSecret  = Get-EnvValue -File (Join-Path $MiddlewareRoot '.env') -Key 'TELECOM_MW_LOCAL_VERIFIER_SECRET'

if (-not $mcpSecret) { throw 'TELECOM_MCP_LOCAL_VERIFIER_SECRET is not set in telecom-mcp/.env (needs 32+ bytes).' }
if ($mcpSecret -ne $mwSecret) {
    throw @'
The two local verifier secrets differ. One token is verified at both hops, so they must
be byte-identical. Copy TELECOM_MCP_LOCAL_VERIFIER_SECRET into
telecom-middleware/.env as TELECOM_MW_LOCAL_VERIFIER_SECRET and run this again.
'@
}

Write-Host 'Freeing ports 9000 and 8080' -ForegroundColor Cyan
Stop-Listener -Port 9000
Stop-Listener -Port 8080
Start-Sleep -Seconds 1

$mwProfile  = Join-Path $ProfileDir 'middleware.env'
$mcpProfile = Join-Path $ProfileDir 'mcp.env'

Write-Host 'Starting the middleware on :9000 (external-test profile)' -ForegroundColor Cyan
Start-Service0 -Title 'telecom-middleware :9000 [TESTABLE]' -WorkingDirectory $MiddlewareRoot `
    -Command "uv run --env-file .env --env-file '$mwProfile' telecom-middleware serve"
$null = Wait-Endpoint -Url 'http://127.0.0.1:9000/healthz' -Name 'The middleware' -Seconds $TimeoutSeconds
Write-Host '  middleware is up' -ForegroundColor Green

Write-Host 'Starting the MCP server on :8080 (external-test profile)' -ForegroundColor Cyan
Start-Service0 -Title 'telecom-mcp :8080 [TESTABLE]' -WorkingDirectory $McpRoot `
    -Command "uv run --env-file .env --env-file '$mcpProfile' telecom-mcp serve --transport http"
$ready = Wait-Endpoint -Url 'http://127.0.0.1:8080/readyz' -Name 'The MCP server' -Seconds $TimeoutSeconds

foreach ($component in $ready.components) {
    $colour = if ($component.status -eq 'healthy') { 'Green' } else { 'Red' }
    Write-Host ("  {0,-22} {1}" -f $component.name, $component.status) -ForegroundColor $colour
}
if ($ready.status -ne 'healthy') {
    throw 'The tool server cannot reach the middleware. Check the middleware window before going further.'
}

# --- one token, both audiences ------------------------------------------------------
Write-Host 'Minting a token' -ForegroundColor Cyan
$env:TELECOM_MCP_LOCAL_VERIFIER_SECRET = $mcpSecret
$env:TELECOM_MCP_JWT_AUDIENCE          = 'https://api.telecom.example/v1'
Push-Location $McpRoot
try {
    $token = (uv run python scripts/mint_dev_token.py --cx-id $CxId --minutes $TokenMinutes).Trim()
} finally {
    Pop-Location
}
if (-not $token -or $token.Split('.').Count -ne 3) { throw "mint_dev_token.py did not return a JWT: $token" }
Write-Host ("  a customer token for {0}, good for {1} minutes" -f $CxId, $TokenMinutes) -ForegroundColor Green

# --- prove it, locally, before anything public ---------------------------------------
Write-Host 'Preflight against the local pair' -ForegroundColor Cyan
python (Join-Path $PSScriptRoot 'preflight.py') --token $token `
    --mcp 'http://127.0.0.1:8080' --middleware 'http://127.0.0.1:9000'
if ($LASTEXITCODE -ne 0) {
    throw 'Preflight failed locally. Nothing public will behave better - fix the lines above first.'
}

if ($PublicMcpUrl -or $PublicMiddlewareUrl) {
    Write-Host 'Preflight against the public URLs' -ForegroundColor Cyan
    $args = @('--token', $token)
    if ($PublicMcpUrl)        { $args += @('--mcp', $PublicMcpUrl) }
    if ($PublicMiddlewareUrl) { $args += @('--middleware', $PublicMiddlewareUrl) }
    python (Join-Path $PSScriptRoot 'preflight.py') @args
    if ($LASTEXITCODE -ne 0) {
        throw 'The pair is healthy locally but not through the tunnel. Check each tunnel points at the right port.'
    }
}

Write-Host ''
Write-Host 'Ready. The token is below - set it on BOTH TestSprite projects:' -ForegroundColor Cyan
Write-Host ''
Write-Host $token
Write-Host ''
Write-Host @"
Next, in order:

  1. Tunnel both ports, if you have not already:
        cloudflared tunnel --url http://localhost:8080     # tool server
        cloudflared tunnel --url http://localhost:9000     # middleware

  2. Stamp the tunnel URLs into the tests - the runner does not inject a base URL:
        python stamp_target_url.py https://<mcp-host> https://<middleware-host>

  3. Set the credential on both projects:
        testsprite project credential <mcpProjectId>        --type 'Bearer token' --credential <token>
        testsprite project credential <middlewareProjectId> --type 'Bearer token' --credential <token>

  4. Re-upload from build/ and smoke three tests before running all eighteen.

The token expires in $TokenMinutes minutes. A suite that starts inside that window and
finishes outside it fails as `token_expired` and costs a full run, so re-mint rather
than reusing this one tomorrow.
"@ -ForegroundColor DarkGray
