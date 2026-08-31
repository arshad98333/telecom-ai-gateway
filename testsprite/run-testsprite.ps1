# Replaced by run_testsprite.py. This shim exists so an old bookmark still works.
#
#     make testsprite-preflight
#     python testsprite/run_testsprite.py setup --mcp-url https://... --middleware-url https://...
#
# -Stage <name> is now a positional argument: `run_testsprite.py smoke`.
Write-Host 'run-testsprite.ps1 has been replaced by run_testsprite.py. Forwarding...' -ForegroundColor Yellow
python "$PSScriptRoot/run_testsprite.py" @args
exit $LASTEXITCODE
