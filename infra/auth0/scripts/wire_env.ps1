<#
.SYNOPSIS
    Read the Terraform outputs and write them into both services' .env files.

.DESCRIPTION
    Copying six values between a terminal and two files by hand is where the issuer
    loses its trailing slash and the audience ends up different on each side. This does
    it from the state Terraform actually applied.

    Only the identity lines are touched; every other line in each .env is left as it is.
    The client secret is read from the environment, never from Terraform state.

.EXAMPLE
    $env:TELECOM_MCP_CLIENT_SECRET = "..."
    .\scripts\wire_env.ps1
#>
[CmdletBinding()]
param(
    # Leave off to configure the services for Auth0 but keep them on the local
    # verifier, so you can switch over deliberately once a real token is in hand.
    [switch]$Activate
)

$ErrorActionPreference = 'Stop'
$env:VIRTUAL_ENV = $null

$InfraRoot = Split-Path -Parent $PSScriptRoot
$Root      = Split-Path -Parent (Split-Path -Parent $InfraRoot)
$McpEnv    = Join-Path $Root 'telecom-mcp\.env'
$MwEnv     = Join-Path $Root 'telecom-middleware\.env'

foreach ($file in @($McpEnv, $MwEnv)) {
    if (-not (Test-Path -LiteralPath $file)) { throw "Not found: $file" }
}

function Get-Output {
    param([string]$Name)
    Push-Location -LiteralPath $InfraRoot
    try {
        $previous = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $value = (& terraform output -raw $Name 2>&1 | Out-String).Trim()
        $code = $LASTEXITCODE
        $ErrorActionPreference = $previous
    } finally {
        Pop-Location
    }
    if ($code -ne 0 -or -not $value) {
        throw "terraform output -raw $Name failed. Has 'terraform apply' run in $InfraRoot?"
    }
    return $value
}

#: UTF-8 with no byte order mark. Windows PowerShell's -Encoding UTF8 writes one, and
#: uv refuses the whole file when it finds those three bytes ahead of the first key:
#: "Failed to parse environment file .env at position 0".
$script:NoBom = New-Object System.Text.UTF8Encoding($false)

function Get-EnvValue {
    param([string]$File, [string]$Key)
    $line = Get-Content -LiteralPath $File |
        Where-Object { $_ -match "^\s*$([regex]::Escape($Key))\s*=" } |
        Select-Object -First 1
    if (-not $line) { return $null }
    return ($line -split '=', 2)[1].Trim()
}

function Set-EnvValue {
    param([string]$File, [string]$Key, [string]$Value)
    $lines = @(Get-Content -LiteralPath $File)
    $pattern = "^\s*$([regex]::Escape($Key))\s*="
    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match $pattern) { $lines[$i] = "$Key=$Value"; $found = $true; break }
    }
    if (-not $found) { $lines += "$Key=$Value" }
    [System.IO.File]::WriteAllLines($File, $lines, $script:NoBom)
}

Write-Host 'Reading the Terraform outputs' -ForegroundColor Cyan
$issuer    = Get-Output 'issuer'
$jwks      = Get-Output 'jwks_url'
$audience  = Get-Output 'api_identifier'
$namespace = Get-Output 'claim_namespace'
$mcpClient = Get-Output 'mcp_client_id'
# The domain is the issuer without its scheme or trailing slash.
$domain    = ([uri]$issuer).Host
$tokenUrl  = "https://$domain/oauth/token"

foreach ($pair in @(
    @{ n = 'issuer';        v = $issuer },
    @{ n = 'jwks_url';      v = $jwks },
    @{ n = 'api_identifier'; v = $audience },
    @{ n = 'mcp_client_id'; v = $mcpClient })) {
    Write-Host ("  {0,-16} {1}" -f $pair.n, $pair.v) -ForegroundColor DarkGray
}

# The environment wins, so a rotated secret can be applied by setting it and re-running.
# Falling back to the value already written means a second run - in a new terminal, say -
# does not demand a secret that is sitting in the file it is about to update.
$secret = $env:TELECOM_MCP_CLIENT_SECRET
$secretSource = 'the environment'
if (-not $secret) {
    $secret = Get-EnvValue $McpEnv 'TELECOM_MCP_SERVICE_CLIENT_SECRET'
    $secretSource = 'telecom-mcp\.env'
}
if ($secret) {
    Write-Host ''
    Write-Host ("  client secret    {0} characters, from {1}" -f $secret.Length, $secretSource) -ForegroundColor DarkGray
} else {
    Write-Host ''
    Write-Host 'No client secret for the tool server, so it cannot fetch its own' -ForegroundColor Yellow
    Write-Host 'credential. Copy it from Applications -> telecom-mcp-tools (dev) ->' -ForegroundColor Yellow
    Write-Host 'Settings -> Client Secret, then:' -ForegroundColor Yellow
    Write-Host '  $env:TELECOM_MCP_CLIENT_SECRET = "..."' -ForegroundColor Yellow
    Write-Host 'and run this again.' -ForegroundColor Yellow
}

Write-Host ''
Write-Host 'Writing telecom-mcp\.env' -ForegroundColor Cyan
Set-EnvValue $McpEnv 'TELECOM_MCP_JWT_ISSUER'   $issuer
Set-EnvValue $McpEnv 'TELECOM_MCP_JWKS_URL'     $jwks
Set-EnvValue $McpEnv 'TELECOM_MCP_JWT_AUDIENCE' $audience
Set-EnvValue $McpEnv 'TELECOM_MCP_SERVICE_TOKEN_URL'      $tokenUrl
Set-EnvValue $McpEnv 'TELECOM_MCP_SERVICE_CLIENT_ID'      $mcpClient
Set-EnvValue $McpEnv 'TELECOM_MCP_SERVICE_TOKEN_AUDIENCE' $audience
if ($secret) { Set-EnvValue $McpEnv 'TELECOM_MCP_SERVICE_CLIENT_SECRET' $secret }

Write-Host 'Writing telecom-middleware\.env' -ForegroundColor Cyan
Set-EnvValue $MwEnv 'TELECOM_MW_JWT_ISSUER'   $issuer
Set-EnvValue $MwEnv 'TELECOM_MW_JWKS_URL'     $jwks
Set-EnvValue $MwEnv 'TELECOM_MW_JWT_AUDIENCE' $audience
Set-EnvValue $MwEnv 'TELECOM_MW_CLAIM_NAMESPACE' $namespace
Set-EnvValue $MwEnv 'TELECOM_MW_SERVICE_ALLOWED_CLIENT_IDS' $mcpClient

if ($Activate) {
    if (-not $secret) { throw 'Refusing to activate: without the client secret the tool server cannot authenticate.' }
    Write-Host ''
    Write-Host 'Switching both services onto Auth0' -ForegroundColor Cyan
    Set-EnvValue $McpEnv 'TELECOM_MCP_IDENTITY_VERIFIER'     'jwks'
    Set-EnvValue $McpEnv 'TELECOM_MCP_SERVICE_IDENTITY_SOURCE' 'client_credentials'
    Set-EnvValue $MwEnv  'TELECOM_MW_IDENTITY_VERIFIER' 'jwks'
    Set-EnvValue $MwEnv  'TELECOM_MW_SERVICE_AUTH'      'jwks'
    Write-Host '  Tokens minted by scripts/mint_dev_token.py will no longer be accepted;' -ForegroundColor DarkGray
    Write-Host '  sign in through the console application for a real one.' -ForegroundColor DarkGray
} else {
    Write-Host ''
    Write-Host 'Both services still use the local verifier. Re-run with -Activate to' -ForegroundColor DarkGray
    Write-Host 'switch them onto Auth0 once you have a real token to test with.' -ForegroundColor DarkGray
}

Write-Host ''
Write-Host 'Validating both configurations' -ForegroundColor Cyan

function Test-Config {
    param([string]$Directory, [string]$Command)
    Push-Location -LiteralPath (Join-Path $Root $Directory)
    try {
        $previous = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $output = & uv run --env-file .env $Command check-config 2>&1
        $code = $LASTEXITCODE
        $ErrorActionPreference = $previous
    } finally {
        Pop-Location
    }
    # Exit code 78 is EX_CONFIG. Anything non-zero means the service would not start,
    # and reporting success here would send you to debug the wrong thing later.
    if ($code -ne 0) {
        Write-Host ("  {0}: FAILED" -f $Directory) -ForegroundColor Red
        $output | ForEach-Object { Write-Host ("      " + [string]$_) -ForegroundColor DarkGray }
        return $false
    }
    Write-Host ("  {0}: loads" -f $Directory) -ForegroundColor Green
    return $true
}

$mcpOk = Test-Config 'telecom-mcp' 'telecom-mcp'
$mwOk  = Test-Config 'telecom-middleware' 'telecom-middleware'
if (-not ($mcpOk -and $mwOk)) {
    Write-Host ''
    Write-Host 'One or both services would refuse to start. Fix the above before serving.' -ForegroundColor Yellow
    exit 1
}
