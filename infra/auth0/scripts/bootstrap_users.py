#!/usr/bin/env python3
"""Create the demo users for a non-production Auth0 tenant, with the right metadata.

Terraform builds the tenant's shape - API, scopes, roles, clients, Action. It does not
create people. This does, for dev and staging only, so a new environment has one of
each stakeholder to log in as within a minute.

It refuses to run against a tenant whose domain does not contain "dev" or "staging",
because creating demo accounts with known passwords in production is exactly the kind
of convenience that ends up in an incident report.

Usage:
    export AUTH0_DOMAIN=your-tenant-dev.eu.auth0.com
    export AUTH0_MANAGEMENT_CLIENT_ID=...
    export AUTH0_MANAGEMENT_CLIENT_SECRET=...
    python scripts/bootstrap_users.py --tenant tenant-eu-1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

TIMEOUT_S = 15

DEMO_USERS: list[dict[str, Any]] = [
    {
        "email": "customer@demo.invalid",
        "name": "J. Okonkwo (demo customer)",
        "role": "customer",
        "cx_id": "CX-1234",
    },
    {
        "email": "agent@demo.invalid",
        "name": "A. Agent (demo)",
        "role": "support_agent",
    },
    {
        "email": "supervisor@demo.invalid",
        "name": "S. Supervisor (demo)",
        "role": "supervisor_approver",
    },
    {
        "email": "security@demo.invalid",
        "name": "S. Security (demo)",
        "role": "admin_security",
    },
]


def request(method: str, url: str, token: str, body: dict[str, Any] | None = None) -> Any:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as response:
            payload = response.read()
            return json.loads(payload) if payload else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"{method} {url} failed: {exc.code} {detail}") from exc


def management_token(domain: str, client_id: str, client_secret: str) -> str:
    body = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "audience": f"https://{domain}/api/v2/",
    }
    req = urllib.request.Request(
        f"https://{domain}/oauth/token",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as response:
        return str(json.load(response)["access_token"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True, help="tenant_id to stamp on every user")
    parser.add_argument("--password", default=None, help="demo password; generated if omitted")
    parser.add_argument("--connection", default="Username-Password-Authentication")
    args = parser.parse_args()

    domain = os.environ.get("AUTH0_DOMAIN", "")
    client_id = os.environ.get("AUTH0_MANAGEMENT_CLIENT_ID", "")
    client_secret = os.environ.get("AUTH0_MANAGEMENT_CLIENT_SECRET", "")
    if not (domain and client_id and client_secret):
        print(  # noqa: T201
            "set AUTH0_DOMAIN, AUTH0_MANAGEMENT_CLIENT_ID and AUTH0_MANAGEMENT_CLIENT_SECRET",
            file=sys.stderr,
        )
        return 2

    if not any(marker in domain for marker in ("dev", "staging", "test")):
        print(  # noqa: T201
            f"refusing to create demo users in {domain}: this looks like production.",
            file=sys.stderr,
        )
        return 3

    import secrets

    password = args.password or (secrets.token_urlsafe(18) + "aA1!")
    token = management_token(domain, client_id, client_secret)

    roles = {role["name"].rsplit("-", 1)[0]: role["id"] for role in request(
        "GET", f"https://{domain}/api/v2/roles?per_page=100", token
    )}

    created = []
    for user in DEMO_USERS:
        metadata = {"tenant_id": args.tenant, "role": user["role"]}
        if "cx_id" in user:
            metadata["cx_id"] = user["cx_id"]

        account = request(
            "POST",
            f"https://{domain}/api/v2/users",
            token,
            {
                "email": user["email"],
                "name": user["name"],
                "password": password,
                "connection": args.connection,
                "email_verified": True,
                # app_metadata, never user_metadata: a user can edit their own
                # user_metadata, and tenant is not theirs to choose.
                "app_metadata": metadata,
            },
        )
        role_id = roles.get(user["role"])
        if role_id:
            request(
                "POST",
                f"https://{domain}/api/v2/users/{account['user_id']}/roles",
                token,
                {"roles": [role_id]},
            )
        created.append({"email": user["email"], "role": user["role"]})

    print(json.dumps({"created": created, "password": password}, indent=2))  # noqa: T201
    print(  # noqa: T201
        "\nStore that password somewhere sensible and rotate it when you are done.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
