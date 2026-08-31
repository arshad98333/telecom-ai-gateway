# Replaced by wire_env.py. This shim exists so an old bookmark still works.
#
#     make wire-auth0            # write the values, keep the local verifier
#     make wire-auth0-activate   # write them and switch both services onto Auth0
#
# -Activate is now --activate.
Write-Host 'wire_env.ps1 has been replaced by wire_env.py. Forwarding...' -ForegroundColor Yellow
python "$PSScriptRoot/wire_env.py" @args
exit $LASTEXITCODE
