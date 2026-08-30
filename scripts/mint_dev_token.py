"""Mint a development token for the local verifier.

Development only. The local verifier is refused in production by the settings
validator, so a token minted here cannot be used against a real deployment.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta

import jwt

CX_CLAIM = "https://telecom.example/cx_id"
TENANT_CLAIM = "https://telecom.example/tenant_id"
ROLE_CLAIM = "https://telecom.example/role"

ALL_CUSTOMER_SCOPES = (
    "account:read service:read order:read billing:read network:read "
    "ticket:write callback:write refund:request"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cx-id", default="CX-1234")
    parser.add_argument("--tenant", default="tenant-eu-1")
    parser.add_argument("--role", default="customer")
    parser.add_argument("--scope", default=ALL_CUSTOMER_SCOPES)
    parser.add_argument("--minutes", type=int, default=30)
    parser.add_argument("--audience", default="telecom-mcp-tools")
    args = parser.parse_args()

    secret = os.environ.get("TELECOM_MCP_LOCAL_VERIFIER_SECRET")
    if not secret:
        print(  # noqa: T201 - a script, and this is its whole output
            "set TELECOM_MCP_LOCAL_VERIFIER_SECRET first (at least 32 bytes)",
            file=sys.stderr,
        )
        return 2

    claims = {
        "sub": args.cx_id,
        CX_CLAIM: args.cx_id,
        TENANT_CLAIM: args.tenant,
        ROLE_CLAIM: args.role,
        "scope": args.scope,
        "aud": args.audience,
        "jti": f"dev-{datetime.now(UTC).timestamp():.0f}",
        "exp": int((datetime.now(UTC) + timedelta(minutes=args.minutes)).timestamp()),
    }
    print(jwt.encode(claims, secret, algorithm="HS256"))  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
