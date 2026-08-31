<#
.SYNOPSIS
  Stand up the TestSprite projects for this workspace and run the suites.

.DESCRIPTION
  Two backend projects, because the two services have different URLs and different
  credentials and a single project cannot hold both:

    telecom-mcp-tools   the MCP tool server  - 12 tests
    telecom-middleware  the backing API      - 6 tests

  The tests are hand-authored (the official skill's path 3b) rather than generated. The
  OpenAPI specs are uploaded anyway: they are what `testsprite test plan generate` reads
  if you later want TestSprite to propose additional cases.

  Run it in stages. -Stage setup creates the projects and uploads the specs; -Stage
  create uploads the test files; -Stage smoke runs three; -Stage all runs everything.

.PARAMETER TargetUrlMcp
  A PUBLICLY REACHABLE https URL for the tool server. TestSprite runs backend tests from
  its own cloud, and the CLI rejects localhost and private addresses for --target-url.
  `--local <port>` tunnels, but it is frontend-only. Use your staging deployment, or a
  tunnel (cloudflared / ngrok) if you have not deployed yet.

.EXAMPLE
  ./run-testsprite.ps1 -Stage setup   -TargetUrlMcp https://mcp-staging.example -TargetUrlMiddleware https://mw-staging.example
  ./run-testsprite.ps1 -Stage create
  ./run-testsprite.ps1 -Stage smoke
#>
[CmdletBinding()]
param(
  [ValidateSet('preflight', 'setup', 'credentials', 'create', 'smoke', 'all')]
  [string]$Stage = 'preflight',

  [string]$TargetUrlMcp,
  [string]$TargetUrlMiddleware,

  # Written by -Stage setup, read by every later stage.
  [string]$StateFile = "$PSScriptRoot/.testsprite-state.json"
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

function Read-State {
  if (-not (Test-Path $StateFile)) {
    throw "No state file at $StateFile. Run -Stage setup first."
  }
  Get-Content $StateFile -Raw | ConvertFrom-Json
}

function Write-Head($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }

# --- preflight ----------------------------------------------------------------------
if ($Stage -in 'preflight', 'all') {
  Write-Head 'Preflight'
  testsprite --version
  testsprite auth status
  Write-Host 'If auth status failed, run: testsprite setup' -ForegroundColor Yellow
  if ($Stage -eq 'preflight') { return }
}

# --- setup: two projects, both specs ------------------------------------------------
if ($Stage -in 'setup', 'all') {
  Write-Head 'Creating the projects'
  if (-not $TargetUrlMcp -or -not $TargetUrlMiddleware) {
    throw 'Both -TargetUrlMcp and -TargetUrlMiddleware are required for -Stage setup.'
  }
  foreach ($url in @($TargetUrlMcp, $TargetUrlMiddleware)) {
    if ($url -match 'localhost|127\.0\.0\.1|192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.') {
      throw "TestSprite runs backend tests from its cloud and rejects $url. Use a public URL."
    }
  }

  $mcp = testsprite project create --type backend --name 'telecom-mcp-tools' --output json | ConvertFrom-Json
  $mw  = testsprite project create --type backend --name 'telecom-middleware' --output json | ConvertFrom-Json

  $state = [ordered]@{
    mcpProjectId        = $mcp.projectId
    middlewareProjectId = $mw.projectId
    targetUrlMcp        = $TargetUrlMcp
    targetUrlMiddleware = $TargetUrlMiddleware
    createdAt           = (Get-Date).ToString('o')
  }
  $state | ConvertTo-Json | Set-Content $StateFile
  Write-Host "mcp project        : $($state.mcpProjectId)"
  Write-Host "middleware project : $($state.middlewareProjectId)"

  Write-Head 'Uploading the API specs'
  # Generated from the code, so they cannot drift: the middleware's is FastAPI's own
  # document, the tool server's is built from the frozen TOOL_SPECS catalogue.
  testsprite project docs upload "$root/specs/telecom-mcp-tools.openapi.json"  --project $state.mcpProjectId        --role api-doc
  testsprite project docs upload "$root/specs/telecom-middleware.openapi.json" --project $state.middlewareProjectId --role api-doc
}

# --- credentials --------------------------------------------------------------------
if ($Stage -in 'credentials', 'all') {
  Write-Head 'Configuring the bearer tokens'
  $state = Read-State
  Write-Host @'
Each project needs a tenant JWT. Mint them with the repo's own script:

    cd ..\telecom-mcp
    uv run python scripts\mint_dev_token.py

The tests read the token out of __AUTH_HEADERS__ and never hardcode it, so this is the
only place a credential is entered. A static token expires within hours; for anything
recurring use `testsprite project auto-auth <projectId> ...` instead.
'@ -ForegroundColor Yellow

  $mcpToken = Read-Host 'Bearer token for the tool server (audience telecom-mcp-tools)'
  $mwToken  = Read-Host 'Bearer token for the middleware (audience https://api.telecom.example/v1)'

  testsprite project credential $state.mcpProjectId        --type 'Bearer token' --credential $mcpToken
  testsprite project credential $state.middlewareProjectId --type 'Bearer token' --credential $mwToken
}

# --- create the tests ---------------------------------------------------------------
if ($Stage -in 'create', 'all') {
  Write-Head 'Creating the tests'
  $state = Read-State
  # create-batch is frontend-only, so backend tests go in one at a time. --name is
  # required and is what shows up in the dashboard, so it is a sentence, not a filename.
  $suites = @(
    @{ Dir = "$root/tests/mcp";        Project = $state.mcpProjectId;        Prefix = 'MCP' },
    @{ Dir = "$root/tests/middleware"; Project = $state.middlewareProjectId; Prefix = 'Middleware' }
  )
  foreach ($suite in $suites) {
    Get-ChildItem "$($suite.Dir)/*.py" | Sort-Object Name | ForEach-Object {
      $behaviour = ($_.BaseName -replace '^\d+_', '') -replace '_', ' '
      $name = "$($suite.Prefix) - $behaviour"
      Write-Host "  $name"
      testsprite test create --type backend --project $suite.Project --name $name --code-file $_.FullName
    }
  }
  Write-Host "`nList them with: testsprite test list --project $($state.mcpProjectId)"
}

# --- smoke: three tests, not eighteen -----------------------------------------------
if ($Stage -in 'smoke', 'all') {
  Write-Head 'Smoke run (3 tests)'
  $state = Read-State
  Write-Host 'Deliberately not the whole suite - 18 backend tests is real credit.' -ForegroundColor Yellow

  # Highest-value happy paths: the two liveness probes and the tool catalogue.
  $mcpTests = testsprite test list --project $state.mcpProjectId --output json | ConvertFrom-Json
  $chosen = $mcpTests.tests | Where-Object { $_.name -match 'liveness|catalogue' } | Select-Object -First 2
  foreach ($test in $chosen) {
    testsprite test run $test.id --target-url $state.targetUrlMcp --wait --timeout 600 --output json
  }

  $mwTests = testsprite test list --project $state.middlewareProjectId --output json | ConvertFrom-Json
  $mwChosen = $mwTests.tests | Where-Object { $_.name -match 'health' } | Select-Object -First 1
  foreach ($test in $mwChosen) {
    testsprite test run $test.id --target-url $state.targetUrlMiddleware --wait --timeout 600 --output json
  }
}

# --- everything (the user's explicit choice, never automatic) -----------------------
if ($Stage -eq 'all') {
  Write-Head 'Full suite'
  $state = Read-State
  # Backend runs are wave-ordered by the engine; do not hand-sequence them.
  testsprite test run --all --project $state.mcpProjectId        --wait --timeout 900 --output json
  testsprite test run --all --project $state.middlewareProjectId --wait --timeout 900 --output json
}
