#!/usr/bin/env bash
# Start the built image and prove it actually boots and serves.
# A build that succeeds and an image that cannot start is the failure this catches.
set -euo pipefail

IMAGE="${1:-telecom-mcp-tools:local}"
NAME="telecom-mcp-smoke-$$"
PORT="${PORT:-18080}"

cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run -d --name "$NAME" -p "${PORT}:8080" \
  -e TELECOM_MCP_ENV=local \
  -e TELECOM_MCP_HTTP_HOST=0.0.0.0 \
  -e TELECOM_MCP_LOCAL_VERIFIER_SECRET=smoke-test-secret-long-enough-for-hs256 \
  "$IMAGE" >/dev/null

echo "waiting for readiness on :${PORT}"
for attempt in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${PORT}/readyz" >/dev/null 2>&1; then
    echo "ready after ${attempt}s"
    curl -fsS "http://127.0.0.1:${PORT}/healthz"
    echo
    curl -fsS "http://127.0.0.1:${PORT}/metrics" | head -1
    exit 0
  fi
  sleep 1
done

echo "the image did not become ready; container logs follow" >&2
docker logs "$NAME" >&2
exit 1
