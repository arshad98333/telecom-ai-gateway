<#
.SYNOPSIS
    End-to-end verification of the MCP server, the middleware and the database
    behind it. Prints PASS or FAIL per check and exits non-zero if anything failed.

.DESCRIPTION
    Every check mints its own token, so nothing here depends on a variable set in
    an earlier command or on a token that has since expired.

.EXAMPLE
    .\scripts\smoke.ps1
#>
[CmdletBinding()]
param(
    [string]$McpUrl = 'http://127.0.0.1:8080',
    [string]$CxId   = 'CX-1234'
)

$ErrorActionPreference = 'Stop'
$McpRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $McpRoot

# uv resolves the project's own .venv regardless of what is activated, and warns when
# the two differ. Clearing it for this process only removes the warning without
# changing which interpreter runs.
$env:VIRTUAL_ENV = $null

$script:Passed  = 0
$script:Failed  = 0
$script:lastKey = $null

function Test-Step {
    param([string]$Name, [scriptblock]$Body)
    try {
        $detail = & $Body
        Write-Host ('PASS  ' + $Name) -ForegroundColor Green
        if ($detail) { Write-Host ('      ' + $detail) -ForegroundColor DarkGray }
        $script:Passed++
    } catch {
        Write-Host ('FAIL  ' + $Name) -ForegroundColor Red
        Write-Host ('      ' + $_.Exception.Message) -ForegroundColor DarkGray
        $script:Failed++
    }
}

function New-Token {
    param([string]$Scope)
    $arguments = @('run', '--env-file', '.env', 'python', 'scripts/mint_dev_token.py',
                   '--role', 'customer', '--cx-id', $CxId)
    if ($Scope) { $arguments += @('--scope', $Scope) }

    # Windows PowerShell 5.1 turns anything a native command writes to stderr into a
    # terminating error while $ErrorActionPreference is 'Stop'. uv writes a warning
    # there whenever VIRTUAL_ENV points at a different project, which is routine when
    # two services are open in one shell - and it was killing this function before it
    # ever saw the token. Relax the preference around the call, merge both streams,
    # and decide from the exit code instead.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & uv @arguments 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }

    $lines = @($output | ForEach-Object { [string]$_ })
    # A JWT is three base64url segments separated by dots; the header always starts
    # 'ey'. Matching the shape rather than the position means a warning, a progress
    # line or a blank line ahead of it changes nothing.
    $token = $lines |
        Where-Object { $_ -match '^ey[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.' } |
        Select-Object -Last 1

    if (-not $token) {
        $detail = ($lines | Where-Object { $_.Trim() } | Select-Object -Last 3) -join ' | '
        if (-not $detail) { $detail = 'no output' }
        throw "uv exited $exitCode without printing a token. Last output: $detail"
    }
    return $token.Trim()
}

function New-Headers {
    param([string]$Token)
    return @{ Authorization = "Bearer $Token"; Accept = 'application/json, text/event-stream' }
}

function Invoke-Rpc {
    param([hashtable]$Headers, [string]$Method, $Params)
    $payload = @{ jsonrpc = '2.0'; id = 1; method = $Method }
    if ($Params) { $payload['params'] = $Params }
    $json = $payload | ConvertTo-Json -Depth 12 -Compress
    return Invoke-RestMethod -Method Post -Uri ($McpUrl.TrimEnd('/') + '/mcp/') `
        -Headers $Headers -ContentType 'application/json' -Body $json -TimeoutSec 30
}

function Invoke-Tool {
    param([hashtable]$Headers, [string]$Tool, [hashtable]$Arguments)
    return Invoke-Rpc -Headers $Headers -Method 'tools/call' `
        -Params @{ name = $Tool; arguments = $Arguments }
}

function Get-RefusalCode {
    param($Response)
    if (-not $Response.result.isError) { throw 'The call succeeded; a refusal was expected.' }
    return ($Response.result.content[0].text | ConvertFrom-Json).error.code
}

Write-Host ''
Write-Host 'telecom-mcp end-to-end check' -ForegroundColor Cyan
Write-Host '----------------------------' -ForegroundColor Cyan

$full    = $null
$limited = $null

Test-Step 'the MCP server is reachable and the middleware behind it is healthy' {
    $ready = Invoke-RestMethod -Uri ($McpUrl.TrimEnd('/') + '/readyz') -TimeoutSec 10
    $unhealthy = @($ready.components | Where-Object { $_.status -ne 'healthy' })
    if ($unhealthy.Count -gt 0) {
        throw ('unhealthy: ' + (($unhealthy | ForEach-Object { $_.name }) -join ', '))
    }
    return ('status ' + $ready.status)
}

Test-Step 'a development token can be minted' {
    $script:full    = New-Token
    $script:limited = New-Token -Scope 'account:read'
    return 'one full-scope token and one holding only account:read'
}

if (-not $script:full) {
    # Every check below needs a token. Six more failures would say nothing the one
    # above has not already said.
    Write-Host ''
    Write-Host 'Cannot continue without a token. Check that .env has' -ForegroundColor Yellow
    Write-Host 'TELECOM_MCP_LOCAL_VERIFIER_SECRET set to at least 32 characters.' -ForegroundColor Yellow
    exit 1
}

Test-Step 'a read reaches the database and comes back translated' {
    $response = Invoke-Tool -Headers (New-Headers $script:full) -Tool 'get_invoice_summary' `
        -Arguments @{ cx_id = $CxId; limit = 5 }
    $result = $response.result.structuredContent
    if (-not $result.invoices) { throw 'no invoices returned' }
    return ('outstanding ' + $result.total_outstanding + ' ' + $result.currency +
            ' (the database holds integer minor units; the adapter converts)')
}

$ticketId = $null

Test-Step 'a write creates one record' {
    $key = [guid]::NewGuid().ToString()
    $response = Invoke-Tool -Headers (New-Headers $script:full) -Tool 'create_support_ticket' `
        -Arguments @{
            cx_id           = $CxId
            category        = 'billing'
            subject         = 'August bill looks high'
            description     = 'The August invoice is higher than usual and I would like it checked.'
            idempotency_key = $key
        }
    $script:ticketId = $response.result.structuredContent.ticket_id
    $script:lastKey  = $key
    if (-not $script:ticketId) { throw 'no ticket_id returned' }
    return $script:ticketId
}

Test-Step 'repeating that write returns the same record instead of a second one' {
    $response = Invoke-Tool -Headers (New-Headers $script:full) -Tool 'create_support_ticket' `
        -Arguments @{
            cx_id           = $CxId
            category        = 'billing'
            subject         = 'August bill looks high'
            description     = 'The August invoice is higher than usual and I would like it checked.'
            idempotency_key = $script:lastKey
        }
    $result = $response.result.structuredContent
    if ($result.ticket_id -ne $script:ticketId) { throw 'a second ticket was created' }
    if (-not $result.deduplicated) { throw 'the response is not marked as deduplicated' }
    return 'same ticket_id, deduplicated'
}

Test-Step 'a token without billing:read is refused' {
    $response = Invoke-Tool -Headers (New-Headers $script:limited) -Tool 'get_invoice_summary' `
        -Arguments @{ cx_id = $CxId; limit = 5 }
    $code = Get-RefusalCode $response
    if ($code -ne 'forbidden') { throw ("expected forbidden, got $code") }
    return $code
}

Test-Step "a customer cannot read another customer's account" {
    $response = Invoke-Tool -Headers (New-Headers $script:full) -Tool 'get_customer_account' `
        -Arguments @{ cx_id = 'CX-9999' }
    $code = Get-RefusalCode $response
    if ($code -ne 'cross_account_denied') { throw ("expected cross_account_denied, got $code") }
    return $code
}

Test-Step 'the tool catalogue is filtered by the caller scopes' {
    $all     = @((Invoke-Rpc -Headers (New-Headers $script:full)    -Method 'tools/list').result.tools)
    $subset  = @((Invoke-Rpc -Headers (New-Headers $script:limited) -Method 'tools/list').result.tools)
    if ($subset.Count -ge $all.Count) { throw 'the limited token sees just as many tools' }
    return ('' + $all.Count + ' tools for a full token, ' + $subset.Count +
            ' for account:read only (' + (($subset | ForEach-Object { $_.name }) -join ', ') + ')')
}

Test-Step 'an unauthenticated caller is shown nothing' {
    $headers = @{ Accept = 'application/json, text/event-stream' }
    $tools = @((Invoke-Rpc -Headers $headers -Method 'tools/list').result.tools)
    if ($tools.Count -ne 0) { throw 'the catalogue leaked tool names to an unauthenticated caller' }
    return 'empty catalogue'
}

Write-Host ''
if ($script:Failed -eq 0) {
    Write-Host ("All {0} checks passed." -f $script:Passed) -ForegroundColor Green
    exit 0
}
Write-Host ("{0} passed, {1} failed." -f $script:Passed, $script:Failed) -ForegroundColor Red
exit 1
