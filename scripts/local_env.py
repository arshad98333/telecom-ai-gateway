#!/usr/bin/env python
"""Apply the local development profile to service .env files.

Local development uses a shared HS256 secret and does not call Auth0. This script
rewrites the identity and backend keys that otherwise drift toward a production
Auth0 tenant and cause token errors on a laptop.

Safe to re-run. Secrets are preserved when they are already shared and long enough.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import secrets
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

API_AUDIENCE = "https://api.telecom.example/v1"
CLAIM_NAMESPACE = "https://telecom.example/"

MCP_ENV = ROOT / "telecom-mcp" / ".env"
MW_ENV = ROOT / "telecom-middleware" / ".env"

MCP_PROFILE: dict[str, str] = {
    "TELECOM_MCP_ENV": "local",
    "TELECOM_MCP_BACKEND": "fake",
    "TELECOM_MCP_BACKEND_BASE_URL": "http://127.0.0.1:9000/api/v1",
    "TELECOM_MCP_BACKEND_API_KEY": "dummy-backend-key",
    "TELECOM_MCP_IDENTITY_VERIFIER": "local",
    "TELECOM_MCP_JWT_AUDIENCE": API_AUDIENCE,
    "TELECOM_MCP_CLAIM_NAMESPACE": CLAIM_NAMESPACE,
    "TELECOM_MCP_SERVICE_IDENTITY_SOURCE": "static",
    "TELECOM_MCP_SERVICE_TOKEN_AUDIENCE": API_AUDIENCE,
    "TELECOM_MCP_IDEMPOTENCY_STORE": "memory",
}

MW_PROFILE: dict[str, str] = {
    "TELECOM_MW_ENV": "local",
    "TELECOM_MW_STORE": "memory",
    "TELECOM_MW_IDENTITY_VERIFIER": "local",
    "TELECOM_MW_JWT_AUDIENCE": API_AUDIENCE,
    "TELECOM_MW_CLAIM_NAMESPACE": CLAIM_NAMESPACE,
    "TELECOM_MW_SERVICE_AUTH": "unchecked",
    "TELECOM_MW_SERVICE_SHARED_SECRET": "dummy-service-credential-change-me",
}


def read_key(path: pathlib.Path, key: str) -> str | None:
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=\s*(.*)$")
    for line in path.read_text(encoding="utf-8").splitlines():
        found = pattern.match(line)
        if found:
            return found.group(1).strip().strip("'\"")
    return None


def write_key(path: pathlib.Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(rf"^\s*#?\s*{re.escape(key)}\s*=")
    for index, line in enumerate(lines):
        if pattern.match(line):
            lines[index] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def ensure_shared_secret() -> None:
    mcp_key = "TELECOM_MCP_LOCAL_VERIFIER_SECRET"
    mw_key = "TELECOM_MW_LOCAL_VERIFIER_SECRET"
    mcp_secret = read_key(MCP_ENV, mcp_key)
    mw_secret = read_key(MW_ENV, mw_key)
    placeholder = re.compile(r"change|example|replace|dummy|your|xxx|placeholder|secret$", re.I)
    values = [mcp_secret, mw_secret]
    if (
        any(v is None or not v or placeholder.search(v) or len(v) < 32 for v in values)
        or len(set(values)) != 1
    ):
        secret = secrets.token_urlsafe(48)
        write_key(MCP_ENV, mcp_key, secret)
        write_key(MW_ENV, mw_key, secret)


def apply_profile(path: pathlib.Path, profile: dict[str, str]) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist; run scripts/setup.py first")
    for key, value in profile.items():
        write_key(path, key, value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    missing = [p for p in (MCP_ENV, MW_ENV) if not p.exists()]
    if missing:
        print("Missing .env files. Run: python scripts/setup.py", file=sys.stderr)
        for path in missing:
            print(f"  {path}", file=sys.stderr)
        return 1

    apply_profile(MCP_ENV, MCP_PROFILE)
    apply_profile(MW_ENV, MW_PROFILE)
    ensure_shared_secret()

    print("Local development profile applied.")
    print("  telecom-mcp:          local verifier, fake backend (built-in demo data)")
    print("  telecom-middleware:   local verifier, in-memory store")
    print("  Auth0 is not used. Identity on a laptop is the local verifier.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
