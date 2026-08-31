#!/usr/bin/env python
"""Get a fresh clone to the point where both services start. Safe to re-run.

Three things a newcomer otherwise gets wrong, in the order they get them wrong:

  1. No .env, because it is gitignored and the example is not obviously a template.
  2. Two .env files whose local verifier secrets differ. One token is verified at both
     hops, so a mismatch reads as `token_invalid` from whichever service you call
     first - which looks like a bug in that service and is not.
  3. A secret short enough for the settings validator to refuse at startup.

This copies each .env.example to .env if and only if there is no .env there already,
generates one 48-byte secret and writes it into both, and then asks each service
whether its configuration actually loads.

An existing .env is never modified. Nothing here needs the network except the two
`uv sync` calls, and --no-install skips those too.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import secrets
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: (directory, the key that service calls the shared secret, its console command)
SERVICES = (
    ("telecom-mcp", "TELECOM_MCP_LOCAL_VERIFIER_SECRET", "telecom-mcp"),
    ("telecom-middleware", "TELECOM_MW_LOCAL_VERIFIER_SECRET", "telecom-middleware"),
)

GREEN, YELLOW, RED, OFF = "\033[32m", "\033[33m", "\033[31m", "\033[0m"


def ok(text: str) -> None:
    print(f"  {GREEN}ok{OFF}   {text}")


def note(text: str) -> None:
    print(f"  {YELLOW}..{OFF}   {text}")


def bad(text: str) -> None:
    print(f"  {RED}fail{OFF} {text}")


def read_key(path: pathlib.Path, key: str) -> str | None:
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=\s*(.*)$")
    for line in path.read_text(encoding="utf-8").splitlines():
        found = pattern.match(line)
        if found:
            return found.group(1).strip().strip("'\"")
    return None


def write_key(path: pathlib.Path, key: str, value: str) -> None:
    """Set one key, leaving every other line alone.

    Written with no BOM: uv refuses an env file that starts with one
    ("Failed to parse environment file .env at position 0").
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(rf"^\s*#?\s*{re.escape(key)}\s*=")
    for index, line in enumerate(lines):
        if pattern.match(line):
            lines[index] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-install", action="store_true", help="skip `uv sync`")
    args = parser.parse_args()

    if shutil.which("uv") is None:
        bad("uv is not on PATH. Install it first: https://docs.astral.sh/uv/getting-started/")
        return 1

    missing = [name for name, _, _ in SERVICES if not (ROOT / name).is_dir()]
    if missing:
        bad(f"not checked out: {', '.join(missing)}")
        return 1

    print("\nEnvironment files")
    created: list[pathlib.Path] = []
    for name, _, _ in SERVICES:
        env, example = ROOT / name / ".env", ROOT / name / ".env.example"
        if env.exists():
            ok(f"{name}/.env exists, leaving it alone")
        elif not example.exists():
            bad(f"{name}/.env.example is missing")
            return 1
        else:
            shutil.copyfile(example, env)
            created.append(env)
            ok(f"{name}/.env created from the example")

    # One secret, both files. Only written where the value is still the example's, so a
    # re-run cannot invalidate tokens someone is already using.
    print("\nShared development secret")
    values = {name: read_key(ROOT / name / ".env", key) for name, key, _ in SERVICES}
    # `search`, not `match`: the shipped example is
    # "dummy-local-signing-secret-change-me-32b", and the giveaway is in the middle.
    placeholder = re.compile(r"change|example|replace|dummy|your|xxx|placeholder|secret$", re.I)
    if any(v is None or not v or placeholder.search(v) or len(v) < 32 for v in values.values()) \
            or len(set(values.values())) != 1:
        secret = secrets.token_urlsafe(48)
        for name, key, _ in SERVICES:
            write_key(ROOT / name / ".env", key, secret)
        ok("one 48-byte secret written into both .env files")
    else:
        ok("both .env files already share a usable secret")

    if not args.no_install:
        print("\nDependencies")
        for name, _, _ in SERVICES:
            print(f"  .. {name}: uv sync --frozen --all-extras")
            result = subprocess.run(  # noqa: S603
                ["uv", "sync", "--frozen", "--all-extras"], cwd=ROOT / name, check=False
            )
            if result.returncode != 0:
                bad(f"{name}: uv sync failed")
                return 1
            ok(f"{name}: installed")

    print("\nConfiguration")
    problems = 0
    for name, _, command in SERVICES:
        result = subprocess.run(  # noqa: S603
            ["uv", "run", "--env-file", ".env", command, "check-config"],
            cwd=ROOT / name, capture_output=True, text=True, check=False,
        )
        if result.returncode == 0:
            ok(f"{name}: configuration loads")
        else:
            problems += 1
            bad(f"{name}: would not start")
            for line in (result.stdout + result.stderr).strip().splitlines()[-12:]:
                print(f"         {line}")

    if problems:
        print("\nFix the above, then run this again. Nothing else needs redoing.")
        return 1

    print(f"""
{GREEN}Ready.{OFF} Next:

  make demo     everything in Docker, seeded, on http://localhost:9000
  make dev      the two services on your machine, in two terminals
  make test     both test suites

Both .env files use the local verifier and an in-memory or local store. Nothing here
reaches a real cluster or a real Auth0 tenant - see docs/SETUP-MONGODB-ATLAS.md and
infra/auth0/README.md when you want those.""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
