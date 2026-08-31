"""Mint a development token for the local verifier.

Development only. The local verifier is refused in production by the settings
validator, so a token minted here cannot be used against a real deployment.

``--write-env`` rewrites the ``DEV_TOKEN`` line in ``.env`` so the REST Client requests
in ``requests.http`` pick it up without anyone pasting anything.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
ENV_KEY = "DEV_TOKEN"

#: Overridden by TELECOM_MCP_CLAIM_NAMESPACE, so a token minted here matches whatever
#: namespace the deployment is configured for.
NAMESPACE = os.environ.get("TELECOM_MCP_CLAIM_NAMESPACE", "https://telecom.example/")
CX_CLAIM = f"{NAMESPACE}cx_id"
TENANT_CLAIM = f"{NAMESPACE}tenant_id"
ROLE_CLAIM = f"{NAMESPACE}role"

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
DEFAULT_API_AUDIENCE = os.environ.get("TELECOM_MCP_JWT_AUDIENCE", "https://api.telecom.example/v1")


def write_into_env(token: str, path: Path = ENV_FILE) -> None:
    """Replace the DEV_TOKEN line in .env, leaving every other line untouched."""
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist; copy .env.example first")
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if re.match(rf"^\s*{ENV_KEY}\s*=", line):
            lines[index] = f"{ENV_KEY}={token}"
            break
    else:
        lines += [
            "",
            "# Written by scripts/mint_dev_token.py --write-env, for requests.http",
            f"{ENV_KEY}={token}",
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    parser.add_argument(
        "--write-env", action="store_true", help=f"set {ENV_KEY} in .env for requests.http"
    )
    args = parser.parse_args()

    secret = os.environ.get("TELECOM_MCP_LOCAL_VERIFIER_SECRET")
    if not secret:
        print(
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
        # Both shapes. Auth0 emits `permissions` when RBAC is enabled on the API; the
        # standard `scope` claim is the fallback both services read.
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
    token = jwt.encode(claims, secret, algorithm="HS256")

    if args.write_env:
        write_into_env(token)
        print(
            f"{ENV_KEY} in .env now holds a {args.role} token, valid {args.minutes} minutes.\n"
            "requests.http will use it on the next request you send."
        )
        return 0

    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
