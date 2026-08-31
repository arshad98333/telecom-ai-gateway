#!/usr/bin/env python
"""Read the Terraform outputs and write them into both services' .env files.

Copying six values between a terminal and two files by hand is where the issuer loses
its trailing slash and the audience ends up different on each side. This does it from
the state Terraform actually applied.

Only the identity lines are touched; every other line in each .env is left as it is.
The client secret is read from the environment, never from Terraform state.

    export TELECOM_MCP_CLIENT_SECRET=...
    python infra/auth0/scripts/wire_env.py

Add --activate to switch both services from the local verifier onto Auth0. Left off,
the values are written but the services keep using the local verifier, so you can make
the switch deliberately once a real token is in hand.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys
import urllib.parse

INFRA_ROOT = pathlib.Path(__file__).resolve().parent.parent
ROOT = INFRA_ROOT.parent.parent
MCP_ENV = ROOT / "telecom-mcp" / ".env"
MW_ENV = ROOT / "telecom-middleware" / ".env"


class Fail(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(f"\n{message}\n")


def say(text: str) -> None:
    print(text, flush=True)


def terraform_output(name: str) -> str:
    result = subprocess.run(  # noqa: S603
        ["terraform", "output", "-raw", name],
        cwd=INFRA_ROOT, capture_output=True, text=True, check=False,
    )
    value = result.stdout.strip()
    if result.returncode != 0 or not value:
        raise Fail(
            f"terraform output -raw {name} failed. Has 'terraform apply' run in {INFRA_ROOT}?\n"
            f"{result.stderr.strip()}"
        )
    return value


def get_env_value(path: pathlib.Path, key: str) -> str | None:
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for line in path.read_text(encoding="utf-8").splitlines():
        if pattern.match(line):
            return line.split("=", 1)[1].strip()
    return None


def set_env_value(path: pathlib.Path, key: str, value: str) -> None:
    """Rewrite one key in place, leaving every other line exactly as it was.

    Written as UTF-8 with no byte order mark: uv refuses the whole file when it finds
    those three bytes ahead of the first key ("Failed to parse environment file .env at
    position 0"), which is what PowerShell's -Encoding UTF8 used to produce here.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for index, line in enumerate(lines):
        if pattern.match(line):
            lines[index] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def check_config(directory: str, command: str) -> bool:
    result = subprocess.run(  # noqa: S603
        ["uv", "run", "--env-file", ".env", command, "check-config"],
        cwd=ROOT / directory, capture_output=True, text=True, check=False,
    )
    # Exit code 78 is EX_CONFIG. Anything non-zero means the service would not start,
    # and reporting success here would send you to debug the wrong thing later.
    if result.returncode != 0:
        say(f"  {directory}: FAILED")
        for line in (result.stdout + result.stderr).splitlines():
            say(f"      {line}")
        return False
    say(f"  {directory}: loads")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--activate", action="store_true",
                        help="switch both services onto Auth0, not just write the values")
    args = parser.parse_args()

    for path in (MCP_ENV, MW_ENV):
        if not path.exists():
            raise Fail(f"Not found: {path}")

    say("Reading the Terraform outputs")
    issuer = terraform_output("issuer")
    jwks = terraform_output("jwks_url")
    audience = terraform_output("api_identifier")
    namespace = terraform_output("claim_namespace")
    mcp_client = terraform_output("mcp_client_id")
    # The domain is the issuer without its scheme or trailing slash.
    domain = urllib.parse.urlparse(issuer).hostname
    token_url = f"https://{domain}/oauth/token"

    for name, value in (("issuer", issuer), ("jwks_url", jwks),
                        ("api_identifier", audience), ("mcp_client_id", mcp_client)):
        say(f"  {name:<16} {value}")

    # The environment wins, so a rotated secret can be applied by setting it and
    # re-running. Falling back to the value already written means a second run - in a
    # new terminal, say - does not demand a secret sitting in the file it is updating.
    secret = os.environ.get("TELECOM_MCP_CLIENT_SECRET")
    source = "the environment"
    if not secret:
        secret = get_env_value(MCP_ENV, "TELECOM_MCP_SERVICE_CLIENT_SECRET")
        source = "telecom-mcp/.env"
    if secret:
        say(f"\n  client secret    {len(secret)} characters, from {source}")
    else:
        say("""
No client secret for the tool server, so it cannot fetch its own credential. Copy it
from Applications -> telecom-mcp-tools (dev) -> Settings -> Client Secret, then:
  export TELECOM_MCP_CLIENT_SECRET="..."
and run this again.""")

    say("\nWriting telecom-mcp/.env")
    set_env_value(MCP_ENV, "TELECOM_MCP_JWT_ISSUER", issuer)
    set_env_value(MCP_ENV, "TELECOM_MCP_JWKS_URL", jwks)
    set_env_value(MCP_ENV, "TELECOM_MCP_JWT_AUDIENCE", audience)
    set_env_value(MCP_ENV, "TELECOM_MCP_SERVICE_TOKEN_URL", token_url)
    set_env_value(MCP_ENV, "TELECOM_MCP_SERVICE_CLIENT_ID", mcp_client)
    set_env_value(MCP_ENV, "TELECOM_MCP_SERVICE_TOKEN_AUDIENCE", audience)
    if secret:
        set_env_value(MCP_ENV, "TELECOM_MCP_SERVICE_CLIENT_SECRET", secret)

    say("Writing telecom-middleware/.env")
    set_env_value(MW_ENV, "TELECOM_MW_JWT_ISSUER", issuer)
    set_env_value(MW_ENV, "TELECOM_MW_JWKS_URL", jwks)
    set_env_value(MW_ENV, "TELECOM_MW_JWT_AUDIENCE", audience)
    set_env_value(MW_ENV, "TELECOM_MW_CLAIM_NAMESPACE", namespace)
    set_env_value(MW_ENV, "TELECOM_MW_SERVICE_ALLOWED_CLIENT_IDS", mcp_client)

    if args.activate:
        if not secret:
            raise Fail("Refusing to activate: without the client secret the tool server cannot authenticate.")
        say("\nSwitching both services onto Auth0")
        set_env_value(MCP_ENV, "TELECOM_MCP_IDENTITY_VERIFIER", "jwks")
        set_env_value(MCP_ENV, "TELECOM_MCP_SERVICE_IDENTITY_SOURCE", "client_credentials")
        set_env_value(MW_ENV, "TELECOM_MW_IDENTITY_VERIFIER", "jwks")
        set_env_value(MW_ENV, "TELECOM_MW_SERVICE_AUTH", "jwks")
        say("  Tokens minted by scripts/mint_dev_token.py will no longer be accepted;\n"
            "  sign in through the console application for a real one.")
    else:
        say("\nBoth services still use the local verifier. Re-run with --activate to\n"
            "switch them onto Auth0 once you have a real token to test with.")

    say("\nValidating both configurations")
    ok = check_config("telecom-mcp", "telecom-mcp")
    ok = check_config("telecom-middleware", "telecom-middleware") and ok
    if not ok:
        say("\nOne or both services would refuse to start. Fix the above before serving.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
