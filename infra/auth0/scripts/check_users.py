#!/usr/bin/env python3
"""Show what each user actually carries, and optionally repair the demo accounts.

The post-login Action denies any login whose app_metadata lacks tenant_id or role,
which is correct - an account half-way through provisioning must not get a working
token with a guessed tenant. When that denial fires, this says which user is missing
what, rather than leaving you clicking through the dashboard.

    python scripts/check_users.py
    python scripts/check_users.py --repair          # set metadata and roles, then re-check

Needs AUTH0_DOMAIN, AUTH0_MANAGEMENT_CLIENT_ID and AUTH0_MANAGEMENT_CLIENT_SECRET,
and a Management application holding read:users, update:users, read:roles and
create:role_members.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

TIMEOUT_S = 20

#: What each demo account must carry for the Action to let it through.
EXPECTED: dict[str, dict[str, str]] = {
    "customer@demo.invalid": {"role": "customer", "cx_id": "CX-1234"},
    "agent@demo.invalid": {"role": "support_agent"},
    "supervisor@demo.invalid": {"role": "supervisor_approver"},
    "security@demo.invalid": {"role": "admin_security"},
}


def call(method: str, url: str, token: str, body: dict[str, Any] | None = None) -> Any:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
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
    request = urllib.request.Request(
        f"https://{domain}/oauth/token",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        return str(json.load(response)["access_token"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default="tenant-eu-1")
    parser.add_argument(
        "--repair", action="store_true", help="write the missing metadata and role assignments"
    )
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

    token = management_token(domain, client_id, client_secret)

    roles_by_name: dict[str, str] = {}
    for role in call("GET", f"https://{domain}/api/v2/roles?per_page=100", token):
        # Terraform names them "<role>-<environment>"; the code knows them unsuffixed.
        roles_by_name[str(role["name"]).rsplit("-", 1)[0]] = role["id"]

    users = call("GET", f"https://{domain}/api/v2/users?per_page=100", token)

    print(f"\n{len(users)} user(s) in {domain}\n")  # noqa: T201
    problems: list[tuple[dict[str, Any], dict[str, str]]] = []

    for user in users:
        email = user.get("email", "<no email>")
        metadata = user.get("app_metadata") or {}
        assigned = call(
            "GET", f"https://{domain}/api/v2/users/{urllib.parse.quote(user['user_id'])}/roles",
            token,
        )
        role_names = [str(r["name"]) for r in assigned]

        print(f"  {email}")  # noqa: T201
        print(f"    identity      {user['user_id']}")  # noqa: T201
        print(f"    app_metadata  {json.dumps(metadata) if metadata else '(empty)'}")  # noqa: T201
        print(f"    roles         {', '.join(role_names) if role_names else '(none)'}")  # noqa: T201

        expected = EXPECTED.get(email)
        if expected is None:
            # A social or personal account. The Action will deny it, which is correct.
            print("    -> not a demo account; the Action will refuse it")  # noqa: T201
            print()  # noqa: T201
            continue

        missing = []
        if not metadata.get("tenant_id"):
            missing.append("app_metadata.tenant_id")
        if not metadata.get("role"):
            missing.append("app_metadata.role")
        if expected["role"] == "customer" and not metadata.get("cx_id"):
            missing.append("app_metadata.cx_id")
        if not role_names:
            missing.append("an Auth0 role assignment (no permissions without one)")

        if missing:
            print(f"    -> MISSING: {', '.join(missing)}")  # noqa: T201
            problems.append((user, expected))
        else:
            print("    -> complete")  # noqa: T201
        print()  # noqa: T201

    if not problems:
        print("Every demo account is provisioned.")  # noqa: T201
        return 0

    if not args.repair:
        print(f"{len(problems)} account(s) need repair. Re-run with --repair.")  # noqa: T201
        return 1

    for user, expected in problems:
        metadata = {"tenant_id": args.tenant, "role": expected["role"]}
        if "cx_id" in expected:
            metadata["cx_id"] = expected["cx_id"]
        user_id = urllib.parse.quote(user["user_id"])

        call("PATCH", f"https://{domain}/api/v2/users/{user_id}", token, {"app_metadata": metadata})
        role_id = roles_by_name.get(expected["role"])
        if role_id:
            call("POST", f"https://{domain}/api/v2/users/{user_id}/roles", token, {"roles": [role_id]})
        print(f"  repaired {user.get('email')}: {json.dumps(metadata)}")  # noqa: T201

    print("\nRe-run without --repair to confirm.")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
