# Replaced by start_testable.py. This shim exists so an old bookmark still works.
#
# The entry points are Python and make now, so the same command works on a laptop, a
# Linux CI runner and a container:
#
#     make testable
#     python testsprite/start_testable.py --public-mcp https://... --public-middleware https://...
#
# All the parameters this script took have long-form equivalents; see --help.
Write-Host 'start-testable.ps1 has been replaced by start_testable.py. Forwarding...' -ForegroundColor Yellow
python "$PSScriptRoot/start_testable.py" @args
exit $LASTEXITCODE
