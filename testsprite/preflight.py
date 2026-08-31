#!/usr/bin/env python3
"""Prove a credential works against a live target before a paid run uses it.

TestSprite bills for a run whether or not the run learned anything. Two full suites
have now been spent discovering that a token was minted for one verifier and presented
to another, and the reports that came back read as product defects: "the write endpoint
should be reachable", "the v1 contract should include invoices". Neither was true. The
services were fine; the credential could not get past the door.

So this makes the same three calls the suites make, from the same outside, and says in
one line which door refused and why. It is deliberately standard-library only - no uv,
no venv, no project install - so it runs from anywhere including a CI step:

    python preflight.py --token "$TOKEN" \\
        --mcp https://<mcp-host> --middleware https://<middleware-host>

Exit codes: 0 everything a run needs is in place, 1 something would fail the run, 2 the
arguments were wrong. Nothing here verifies a signature: it decodes the token to report
what it claims, and lets the servers be the judge of whether it is real. That is the
whole point - the servers are what the suite is testing.
"""

from __future__ import annotations

import argparse
import base64
import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

TIMEOUT = 20
CX_CLAIM = "https://telecom.example/cx_id"

OK = "  ok   "
BAD = " FAIL  "
INFO = " note  "


@dataclass
class Check:
    passed: bool
    line: str
    #: What to actually do about it. Empty when nothing is wrong.
    remedy: str = ""


def _request(url: str, *, method: str = "GET", headers: dict[str, str], body: bytes | None = None):
    request = urllib.request.Request(url, method=method, data=body, headers=headers)  # noqa: S310
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=context) as response:  # noqa: S310
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - the reason is the useful part
        return None, str(exc)


def _json(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def decode_claims(token: str) -> dict[str, Any]:
    """Read the payload without verifying it. The servers do the verifying."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("not a three-part JWT")
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def check_token(token: str) -> list[Check]:
    """Everything that can be known from the token alone, before any network call."""
    checks: list[Check] = []
    try:
        claims = decode_claims(token)
    except Exception as exc:  # noqa: BLE001
        return [
            Check(
                False,
                f"the token is not a readable JWT: {exc}",
                "Mint one with telecom-mcp/scripts/mint_dev_token.py and paste the whole "
                "value, with no 'Bearer ' prefix and no line breaks.",
            )
        ]

    expires_at = claims.get("exp")
    if expires_at is None:
        checks.append(Check(False, "the token carries no exp claim", "Both verifiers require it."))
    else:
        remaining = float(expires_at) - time.time()
        if remaining <= 0:
            checks.append(
                Check(
                    False,
                    f"the token expired {-remaining / 60:.1f} minutes ago",
                    "Mint a fresh one. A suite takes minutes to run, so mint with at "
                    "least --minutes 60 and set it on the project immediately.",
                )
            )
        elif remaining < 600:
            checks.append(
                Check(
                    False,
                    f"the token expires in {remaining / 60:.1f} minutes",
                    "Too close. A run that starts inside the window still finishes "
                    "outside it. Mint with --minutes 60 or more.",
                )
            )
        else:
            checks.append(Check(True, f"the token is live for another {remaining / 60:.0f} minutes"))

    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience] if audience else []
    checks.append(
        Check(
            bool(audiences),
            f"audience: {', '.join(str(a) for a in audiences) or '<none>'}",
            "" if audiences else "Both verifiers require aud.",
        )
    )

    subject = claims.get(CX_CLAIM) or claims.get("sub")
    checks.append(
        Check(
            bool(subject),
            f"the suites will read the customer as {subject!r}",
            "" if subject else "Neither sub nor the namespaced cx_id claim is present.",
        )
    )
    return checks


def check_middleware(base: str, token: str) -> list[Check]:
    """The three answers that tell the two failure modes apart."""
    checks: list[Check] = []
    status, text = _request(f"{base}/healthz", headers={})
    if status != 200:
        return [
            Check(
                False,
                f"{base}/healthz answered {status}: {text[:200]}",
                "Nothing else can be judged until the service answers. Check the tunnel "
                "is up and pointing at the middleware's port, not the tool server's.",
            )
        ]
    checks.append(Check(True, "middleware liveness answers"))

    try:
        subject = decode_claims(token).get(CX_CLAIM) or decode_claims(token)["sub"]
    except Exception:  # noqa: BLE001
        return checks

    status, text = _request(
        f"{base}/api/v1/customers/{subject}",
        headers={"Authorization": f"Bearer {token}"},
    )
    body = _json(text)
    code = body.get("code")

    if status == 200:
        checks.append(Check(True, "an authenticated account read is served"))
    elif code == "service_credential_missing":
        checks.append(
            Check(
                False,
                "the service-credential gate refused: this API wants two credentials",
                "TestSprite can only send Authorization, so the second header never "
                "arrives. Run the target with TELECOM_MW_SERVICE_AUTH=unchecked (the "
                "external-test profile does this) or put the tool server in front of "
                "it. Production still refuses 'unchecked' - the settings validator "
                "enforces that.",
            )
        )
    elif code == "service_not_recognised":
        checks.append(
            Check(
                False,
                "a service credential was sent but not recognised",
                "The value in X-Service-Authorization does not match "
                "TELECOM_MW_SERVICE_SHARED_SECRET, or the client id is not in "
                "TELECOM_MW_SERVICE_ALLOWED_CLIENT_IDS.",
            )
        )
    elif code in {"token_invalid", "token_expired", "unauthenticated"}:
        checks.append(
            Check(
                False,
                f"the middleware rejected the token: {code}",
                "Almost always a verifier mismatch. A token minted by "
                "scripts/mint_dev_token.py is HS256 and only works against "
                "TELECOM_MW_IDENTITY_VERIFIER=local with the same "
                "TELECOM_MW_LOCAL_VERIFIER_SECRET. If the target runs verifier=jwks it "
                "wants a real Auth0 RS256 token instead.",
            )
        )
    elif status == 403:
        checks.append(
            Check(
                False,
                "authenticated, but the account read was denied",
                "The token authenticates but does not own this customer, or the store "
                "holds no such record. Seed the target, or mint for a seeded cx_id.",
            )
        )
    else:
        checks.append(Check(False, f"unexpected answer {status}: {text[:200]}", "Read the body."))
    return checks


def check_mcp(base: str, token: str) -> list[Check]:
    checks: list[Check] = []
    status, text = _request(f"{base}/healthz", headers={})
    if status != 200:
        return [
            Check(
                False,
                f"{base}/healthz answered {status}: {text[:200]}",
                "Check the tunnel is up and pointing at the tool server's port.",
            )
        ]
    checks.append(Check(True, "tool server liveness answers"))

    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).encode()
    status, text = _request(
        f"{base}/mcp/",
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        body=payload,
    )
    body = _json(text)
    tools = (body.get("result") or {}).get("tools")

    if tools:
        checks.append(Check(True, f"the catalogue lists {len(tools)} tools"))
    elif body.get("error") or "token_invalid" in text:
        checks.append(
            Check(
                False,
                "tools/list refused the token",
                "Same verifier mismatch as above: TELECOM_MCP_IDENTITY_VERIFIER and "
                "TELECOM_MCP_LOCAL_VERIFIER_SECRET must match how the token was minted, "
                "and TELECOM_MCP_JWT_AUDIENCE must be one of the token's aud values.",
            )
        )
    elif tools == []:
        checks.append(
            Check(
                False,
                "the catalogue came back empty",
                "An empty catalogue now means the identity verified but holds no "
                "scopes for any tool - a token minted with --scope '' or an unknown "
                "role. An unverifiable token is reported as an error instead.",
            )
        )
    else:
        checks.append(Check(False, f"unexpected answer {status}: {text[:200]}", "Read the body."))
    return checks


def report(title: str, checks: list[Check]) -> bool:
    print(f"\n{title}")
    ok = True
    for check in checks:
        print(f"[{OK if check.passed else BAD}] {check.line}")
        if not check.passed:
            ok = False
            if check.remedy:
                for line in check.remedy.split(". "):
                    if line.strip():
                        print(f"[{INFO}]   {line.strip().rstrip('.')}.")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token", required=True, help="the bearer token, without 'Bearer '")
    parser.add_argument("--mcp", help="base URL of the tool server, e.g. https://host")
    parser.add_argument("--middleware", help="base URL of the middleware")
    args = parser.parse_args()

    if not args.mcp and not args.middleware:
        print("give at least one of --mcp and --middleware", file=sys.stderr)
        return 2

    token = args.token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    healthy = report("The credential itself", check_token(token))
    if args.middleware:
        healthy &= report(f"Middleware at {args.middleware.rstrip('/')}",
                          check_middleware(args.middleware.rstrip("/"), token))
    if args.mcp:
        healthy &= report(f"Tool server at {args.mcp.rstrip('/')}",
                          check_mcp(args.mcp.rstrip("/"), token))

    if healthy:
        print("\nEverything a run needs is in place. Spending credits now buys real signal.")
        return 0
    print(
        "\nA run started now would report these as product defects. Fix the lines above "
        "first - none of them costs a TestSprite credit to fix."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
