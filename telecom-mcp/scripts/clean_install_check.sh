#!/usr/bin/env bash
# Build a wheel, install it into an empty environment, and prove it imports and runs.
# This is the "a stranger can clone, install and verify it" check, automated.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

cd "$ROOT"
uv build --out-dir "$WORK/dist"

WHEEL="$(ls "$WORK"/dist/*.whl)"
echo "installing $WHEEL into a clean environment"

uv venv "$WORK/venv" --python 3.12
VIRTUAL_ENV="$WORK/venv" uv pip install --python "$WORK/venv/bin/python" "$WHEEL"

"$WORK/venv/bin/python" - <<'PY'
import telecom_mcp
from telecom_mcp.adapters.fake_backend import load_seed
from telecom_mcp.domain.tools import TOOL_SPECS

assert telecom_mcp.__version__ != "0.0.0+unknown", "version metadata did not install"
assert "tenants" in load_seed(), "packaged seed data is missing from the wheel"
assert len(TOOL_SPECS) == 8, "tool catalogue did not install intact"
print(f"clean install verified: telecom-mcp-tools {telecom_mcp.__version__}")
PY

TELECOM_MCP_LOCAL_VERIFIER_SECRET=clean-install-secret-long-enough-hs256 \
  "$WORK/venv/bin/telecom-mcp" check-config >/dev/null
echo "console entry point verified"
