#!/usr/bin/env bash
# A deployment that starts is not the same as a deployment that is configured.
#
# The settings validator refuses to start production with the guardrails or tracing
# switched off, so in principle this cannot fail. In principle is doing a lot of work
# in that sentence: the validator protects against a bad environment variable, and this
# protects against the template that sets it being edited by someone who meant well.
#
# It asks the running service rather than reading the template, which is the only
# version of this check worth having.
set -euo pipefail

url="${1:?usage: verify_posture.sh <base url>}"

kpi="$(curl -fsS --max-time 10 "${url}/kpi")"

fail() {
  echo "::error title=Posture::$1"
  exit 1
}

# The KPI endpoint reports the environment it believes it is in. A staging deployment
# reporting 'local' means the environment variables did not arrive.
environment="$(printf '%s' "$kpi" | python3 -c 'import json,sys; print(json.load(sys.stdin)["environment"])')"
[ "$environment" = "production" ] || fail "the service reports environment '$environment', not 'production'"

# Every safety indicator must be present. A missing one means the build shipped a
# catalogue that no longer matches what the dashboards and alerts were generated from.
for key in guardrail_block_ratio output_guardrail_blocks authorization_denial_ratio; do
  printf '%s' "$kpi" | grep -q "\"$key\"" || fail "the KPI catalogue is missing '$key'"
done

# Readiness must not be reporting a required dependency down.
ready="$(curl -fsS --max-time 10 "${url}/readyz")"
printf '%s' "$ready" | grep -q '"status": *"unhealthy"' && fail "readiness reports unhealthy"

echo "posture verified: environment=$environment, safety indicators present, readiness ok"
