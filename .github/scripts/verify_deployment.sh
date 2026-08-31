#!/usr/bin/env bash
# A deployment is not finished when the ARM operation returns; it is finished when the
# thing answers. Readiness consults dependencies, so this proves the revision can
# actually serve rather than that a container started.
set -euo pipefail

url="${1:?usage: verify_deployment.sh <base url>}"
attempts="${ATTEMPTS:-30}"
delay="${DELAY_S:-5}"

echo "waiting for ${url}/readyz"
for attempt in $(seq 1 "$attempts"); do
  body="$(curl -fsS --max-time 10 "${url}/readyz" 2>/dev/null || true)"
  if [ -n "$body" ] && printf '%s' "$body" | grep -q '"status": *"healthy"'; then
    echo "ready after ${attempt} attempt(s)"
    printf '%s\n' "$body"
    exit 0
  fi
  [ -n "$body" ] && printf 'attempt %s: %s\n' "$attempt" "$body"
  sleep "$delay"
done

echo "::error title=Not ready::${url}/readyz never reported healthy"
curl -sS --max-time 10 "${url}/readyz" || true
exit 1
