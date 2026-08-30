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
    "account:read service:read order:read billing:read network:read ticket:read "
    "ticket:write callback:write refund:request case:read case:write"
)
SUPERVISOR_SCOPES = ALL_CUSTOMER_SCOPES + " refund:approve assignment:read assignment:write"
SECURITY_SCOPES = "audit:read config:read config:write assignment:read"

ROLE_SCOPES = {
    "customer": ALL_CUSTOMER_SCOPES,
    "support_agent": ALL_CUSTOMER_SCOPES,
    "supervisor_approver": SUPERVISOR_SCOPES,
    "admin_security": SECURITY_SCOPES,
}

#: The middleware's audience. A development token carries both, because the tool server
#: verifies it and then forwards the same token to the API - which is what a real Auth0
#: token does too.
DEFAULT_API_AUDIENCE = "https://api.telecom.example/v1"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cx-id", default="CX-1234")
    parser.add_argument("--tenant", default="tenant-eu-1")
    parser.add_argument("--role", default="customer")
    parser.add_argument(
        "--scope", default=None, help="override the scopes; defaults to the role's set"
    )
    parser.add_argument("--minutes", type=int, default=30)
    parser.add_argument("--audience", default="telecom-mcp-tools", help="the tool server")
    parser.add_argument(
        "--api-audience",
        default=DEFAULT_API_AUDIENCE,
        help="the middleware API; pass an empty string to leave it out",
    )
    args = parser.parse_args()

    secret = os.environ.get("TELECOM_MCP_LOCAL_VERIFIER_SECRET")
    if not secret:
        print(  # noqa: T201 - a script, and this is its whole output
            "set TELECOM_MCP_LOCAL_VERIFIER_SECRET first (at least 32 bytes)",
            file=sys.stderr,
        )
        return 2

    scopes = args.scope or ROLE_SCOPES.get(args.role, ALL_CUSTOMER_SCOPES)
    audiences = [args.audience] + ([args.api_audience] if args.api_audience else [])

    subject = args.cx_id if args.role == "customer" else f"auth0|{args.role}-1"
    claims: dict[str, object] = {
        "sub": subject,
        TENANT_CLAIM: args.tenant,
        ROLE_CLAIM: args.role,
        # Both shapes: the tool server reads `scope`, and the middleware reads
        # `permissions`, which is what Auth0 emits when RBAC is enabled on the API.
        "scope": scopes,
        "permissions": scopes.split(),
        "aud": audiences,
        "jti": f"dev-{datetime.now(UTC).timestamp():.0f}",
        "exp": int((datetime.now(UTC) + timedelta(minutes=args.minutes)).timestamp()),
    }
    # Only a customer carries a customer reference; for any other role the middleware
    # decides access from the assignment collection instead.
    if args.role == "customer":
        claims[CX_CLAIM] = args.cx_id
    print(jwt.encode(claims, secret, algorithm="HS256"))  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
