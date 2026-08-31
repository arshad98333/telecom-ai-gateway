#!/usr/bin/env python3
"""Mint a development token for the local verifier, and optionally park it in .env.

Development only. The local verifier is refused in production by the settings
validator, so a token minted here cannot be used against a real deployment.

``--write-env`` rewrites the ``DEV_TOKEN`` line in ``.env`` so the REST Client requests
in ``requests.http`` pick it up without anyone pasting anything. That is the difference
between trying an endpoint in five seconds and giving up.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt

from telecom_middleware.security.permissions import ROLE_SCOPES, Role

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
ENV_KEY = "DEV_TOKEN"


def build_claims(
    *, role: Role, tenant: str, cx_id: str, audience: str, namespace: str, minutes: int
) -> dict[str, Any]:
    scopes = sorted(str(scope) for scope in ROLE_SCOPES[role])
    subject = cx_id if role is Role.CUSTOMER else f"auth0|{role}-1"
    claims: dict[str, Any] = {
        "sub": subject,
        "aud": audience,
        "iss": "https://dev.local/",
        "exp": int((datetime.now(UTC) + timedelta(minutes=minutes)).timestamp()),
        "jti": f"dev-{datetime.now(UTC).timestamp():.0f}",
        # Auth0 emits `permissions` when RBAC is enabled on the API; the verifier reads
        # it first and falls back to `scope`.
        "permissions": scopes,
        "scope": " ".join(scopes),
        f"{namespace}tenant_id": tenant,
        f"{namespace}role": str(role),
    }
    # Only a customer carries a customer reference. For any other role the service
    # decides access from the assignment collection instead.
    if role is Role.CUSTOMER:
        claims[f"{namespace}cx_id"] = cx_id
    return claims


def write_into_env(token: str, path: Path = ENV_FILE) -> None:
    """Replace the DEV_TOKEN line in .env, leaving every other line untouched."""
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist; copy .env.example first")
    lines = path.read_text(encoding="utf-8").splitlines()
    replaced = False
    for index, line in enumerate(lines):
        if re.match(rf"^\s*{ENV_KEY}\s*=", line):
            lines[index] = f"{ENV_KEY}={token}"
            replaced = True
            break
    if not replaced:
        lines += [
            "",
            "# Written by scripts/dev_token.py --write-env, for requests.http",
            f"{ENV_KEY}={token}",
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--role",
        default="customer",
        choices=[str(role) for role in Role if role is not Role.SERVICE],
    )
    parser.add_argument("--cx-id", default="CX-1234")
    parser.add_argument("--tenant", default="tenant-eu-1")
    parser.add_argument("--minutes", type=int, default=60)
    parser.add_argument("--audience", default=None, help="defaults to TELECOM_MW_JWT_AUDIENCE")
    parser.add_argument("--namespace", default=None, help="defaults to TELECOM_MW_CLAIM_NAMESPACE")
    parser.add_argument(
        "--write-env", action="store_true", help=f"set {ENV_KEY} in .env for requests.http"
    )
    args = parser.parse_args()

    secret = os.environ.get("TELECOM_MW_LOCAL_VERIFIER_SECRET")
    if not secret:
        print(  # noqa: T201 - a script, and this is its whole output
            "set TELECOM_MW_LOCAL_VERIFIER_SECRET first (at least 32 bytes).\n"
            "In VS Code, run the task 'Mint a dev token', which loads .env for you.",
            file=sys.stderr,
        )
        return 2

    token = jwt.encode(
        build_claims(
            role=Role(args.role),
            tenant=args.tenant,
            cx_id=args.cx_id,
            audience=args.audience
            or os.environ.get("TELECOM_MW_JWT_AUDIENCE", "https://api.telecom.example/v1"),
            namespace=args.namespace
            or os.environ.get("TELECOM_MW_CLAIM_NAMESPACE", "https://telecom.example/"),
            minutes=args.minutes,
        ),
        secret,
        algorithm="HS256",
    )

    if args.write_env:
        write_into_env(token)
        print(  # noqa: T201
            f"{ENV_KEY} in .env now holds a {args.role} token, valid {args.minutes} minutes.\n"
            "requests.http will use it on the next request you send."
        )
        return 0

    print(token)  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
