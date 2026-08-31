#!/usr/bin/env python3
"""Sign in as a real user and print the access token the services will accept.

There is no console application yet, and the tenant's SPA client is the only thing
allowed to sign a person in. This runs the same flow that application will run -
authorization code with PKCE - so the token it prints is a genuine Auth0 access token
with the Action's claims and the role's permissions on it, not an approximation.

PKCE rather than a client secret because the SPA has none: a browser cannot keep one.
The verifier is generated per run, never stored, and the code is useless without it.

Usage:
    python scripts/get_token.py --client-id <console_client_id>
    python scripts/get_token.py --client-id <id> --write-env      # into telecom-mcp/.env

Requires the redirect URI below to be listed on the client. Terraform sets
console_callback_urls to http://localhost:5173/callback, which is what this listens on.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import re
import secrets
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

REDIRECT_URI = "http://localhost:5173/callback"
LISTEN_PORT = 5173
TIMEOUT_S = 300

ENV_FILE = Path(__file__).resolve().parents[3] / "telecom-mcp" / ".env"
ENV_KEY = "DEV_TOKEN"

_received: dict[str, str] = {}
_done = threading.Event()


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Catches the one redirect Auth0 sends back, then stops."""

    def log_message(self, *args: object) -> None:
        pass  # the browser's noise is not the operator's problem

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return

        params = urllib.parse.parse_qs(parsed.query)
        _received["code"] = (params.get("code") or [""])[0]
        _received["state"] = (params.get("state") or [""])[0]
        _received["error"] = (params.get("error") or [""])[0]
        _received["error_description"] = (params.get("error_description") or [""])[0]

        ok = bool(_received["code"])
        body = (
            b"<h2>Signed in.</h2><p>Return to the terminal; this window can be closed.</p>"
            if ok
            else b"<h2>Sign-in failed.</h2><p>The terminal has the reason.</p>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        _done.set()


def pkce_pair() -> tuple[str, str]:
    """A fresh verifier and its S256 challenge. RFC 7636."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def post_json(url: str, body: dict[str, str]) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            document: dict[str, object] = json.load(response)
            return document
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"token exchange failed: {exc.code} {detail}") from exc


def write_into_env(token: str, path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"{path} does not exist")
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if re.match(rf"^\s*{ENV_KEY}\s*=", line):
            lines[index] = f"{ENV_KEY}={token}"
            break
    else:
        lines += ["", f"{ENV_KEY}={token}"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def describe(token: str) -> None:
    """Show the claims that decide what this token may do. No signature check here -
    the services do that; this is so you can see why a call was allowed or refused."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        print("could not decode the token payload", file=sys.stderr)  # noqa: T201
        return

    print("\nclaims that matter:", file=sys.stderr)  # noqa: T201
    for key in ("iss", "aud", "sub", "exp"):
        print(f"  {key:<12} {claims.get(key)}", file=sys.stderr)  # noqa: T201
    for key, value in claims.items():
        if key.startswith("http"):
            print(f"  {key:<12} {value}", file=sys.stderr)  # noqa: T201
    permissions = claims.get("permissions") or []
    print(f"  permissions  {len(permissions)}: {', '.join(sorted(permissions))}", file=sys.stderr)  # noqa: T201
    if not permissions:
        print(  # noqa: T201
            "  -> no permissions. Is RBAC on, 'Add Permissions in the Access Token' set, "
            "and a role assigned to this user?",
            file=sys.stderr,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", default="dev-dd56qu5i64yxhygz.us.auth0.com")
    parser.add_argument("--client-id", required=True, help="the console SPA client id")
    parser.add_argument("--audience", default="https://api.telecom.example/v1")
    parser.add_argument("--scope", default="openid profile email")
    parser.add_argument(
        "--connection",
        default="Username-Password-Authentication",
        help=(
            "Which connection to sign in through. Pinned to the database connection by "
            "default so the social buttons are not offered: a Google account in this "
            "tenant carries no app_metadata, and the Action refuses it - correctly, but "
            "confusingly if you meant to use a demo user. Pass an empty string to get "
            "the full login screen."
        ),
    )
    parser.add_argument(
        "--write-env", action="store_true", help=f"set {ENV_KEY} in telecom-mcp/.env"
    )
    args = parser.parse_args()

    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)

    parameters = {
        "response_type": "code",
        "client_id": args.client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": args.scope,
        "audience": args.audience,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # Force re-authentication, so signing in as a different demo user does not
        # silently reuse the last session.
        "prompt": "login",
    }
    if args.connection:
        parameters["connection"] = args.connection
    query = urllib.parse.urlencode(parameters)
    authorize_url = f"https://{args.domain}/authorize?{query}"

    server = http.server.HTTPServer(("127.0.0.1", LISTEN_PORT), CallbackHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print(f"Opening a browser to sign in. If it does not open:\n\n{authorize_url}\n", file=sys.stderr)  # noqa: T201
    webbrowser.open(authorize_url)

    if not _done.wait(TIMEOUT_S):
        server.shutdown()
        raise SystemExit(f"no callback received within {TIMEOUT_S} seconds")
    server.shutdown()

    if _received.get("error"):
        message = (
            f"Auth0 refused the sign-in: {_received['error']} - "
            f"{_received.get('error_description')}"
        )
        if "not provisioned" in (_received.get("error_description") or ""):
            message += (
                "\n\nThat account has no app_metadata. Run scripts/check_users.py to see "
                "which accounts are provisioned; a personal or social account in this "
                "tenant will always be refused."
            )
        raise SystemExit(message)
    if _received.get("state") != state:
        # A mismatch means the response did not come from the request we made.
        raise SystemExit("state mismatch; refusing to exchange the code")

    grant = post_json(
        f"https://{args.domain}/oauth/token",
        {
            "grant_type": "authorization_code",
            "client_id": args.client_id,
            "code_verifier": verifier,
            "code": _received["code"],
            "redirect_uri": REDIRECT_URI,
        },
    )
    token = str(grant.get("access_token") or "")
    if not token:
        raise SystemExit(f"no access token in the response: {grant}")

    describe(token)

    if args.write_env:
        write_into_env(token, ENV_FILE)
        print(f"\n{ENV_KEY} written into {ENV_FILE}", file=sys.stderr)  # noqa: T201
        return 0

    print(token)  # noqa: T201 - the whole point of the script
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
