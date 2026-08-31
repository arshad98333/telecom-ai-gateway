#!/usr/bin/env python
"""Bring both services up in the external-test profile and prove a token works.

The two reports that prompted this both failed for the same reason and neither said
so: the services were running with IDENTITY_VERIFIER=jwks and SERVICE_AUTH=jwks while
the runner held an HS256 development token and could only send one header. Every call
died at the door, and sixteen of eighteen tests came back as product defects that were
nothing of the kind.

This makes that combination impossible to reach by accident. It layers
testsprite/profile/*.env over each service's own .env - changing only the settings that
stop an outside runner getting in - starts both, mints one token both hops accept, and
runs preflight.py against the pair. If preflight fails, stop: a paid run started now
would only rediscover for money what it just told you for free.

The relaxed settings are development-only by construction. The settings validator
refuses them when ENV=production, and the app logs a warning every start while they are
live on a non-loopback interface.

    python start_testable.py
    python start_testable.py --public-mcp https://a.trycloudflare.com \
                             --public-middleware https://b.trycloudflare.com

Run it from anywhere; paths are resolved from this file. Ctrl-C stops both services.
"""

from __future__ import annotations

import argparse
import atexit
import os
import pathlib
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
MCP_ROOT = ROOT / "telecom-mcp"
MW_ROOT = ROOT / "telecom-middleware"
PROFILE = HERE / "profile"
LOGS = HERE / ".logs"

MW_PORT = 9000
MCP_PORT = 8080


class Fail(SystemExit):
    """Stop with a message, not a traceback."""

    def __init__(self, message: str) -> None:
        super().__init__(f"\n{message}\n")


def say(text: str) -> None:
    print(text, flush=True)


def env_value(path: pathlib.Path, key: str) -> str | None:
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=\s*(.*)$")
    for line in path.read_text(encoding="utf-8").splitlines():
        found = pattern.match(line)
        if found:
            return found.group(1).strip().strip("'\"")
    return None


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        return probe.connect_ex(("127.0.0.1", port)) != 0


def wait_for(url: str, name: str, seconds: int) -> dict:
    """Poll a JSON endpoint until it answers, or give up with a useful message."""
    import json

    deadline = time.monotonic() + seconds
    last = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310
                return json.loads(response.read())
        except urllib.error.HTTPError as http_error:
            # Readiness answers 503 with a body worth reading.
            try:
                return json.loads(http_error.read())
            except Exception:  # noqa: BLE001
                last = f"HTTP {http_error.code}"
        except Exception as error:  # noqa: BLE001
            last = str(error)
        time.sleep(0.7)
    raise Fail(
        f"{name} did not come up within {seconds}s ({last}).\n"
        f"Read {LOGS} - the settings validator names every problem at once."
    )


def start(name: str, cwd: pathlib.Path, command: list[str]) -> subprocess.Popen:
    LOGS.mkdir(exist_ok=True)
    log = (LOGS / f"{name}.log").open("w", encoding="utf-8")
    say(f"  {' '.join(command)}")
    return subprocess.Popen(  # noqa: S603
        command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, text=True
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-mcp", help="the https URL your tunnel gives the tool server")
    parser.add_argument("--public-middleware", help="the https URL your tunnel gives the middleware")
    parser.add_argument("--cx-id", default="CX-1234")
    parser.add_argument("--minutes", type=int, default=90, help="token lifetime")
    parser.add_argument("--timeout", type=int, default=60, help="seconds to wait for each service")
    args = parser.parse_args()

    if shutil.which("uv") is None:
        raise Fail("uv is not on PATH. See https://docs.astral.sh/uv/ - `make install` needs it too.")

    for path in (MCP_ROOT, MW_ROOT, PROFILE):
        if not path.exists():
            raise Fail(f"Missing: {path}")
    for service in (MCP_ROOT, MW_ROOT):
        if not (service / ".env").exists():
            raise Fail(f"No .env in {service}. Copy .env.example and fill it in first.")

    # --- the two secrets that have to agree ------------------------------------------
    # One token is verified at both hops, so one secret signs it. When these drift the
    # symptom is `token_invalid` from whichever service you call first, which reads as a
    # bug in that service and is not.
    mcp_secret = env_value(MCP_ROOT / ".env", "TELECOM_MCP_LOCAL_VERIFIER_SECRET")
    mw_secret = env_value(MW_ROOT / ".env", "TELECOM_MW_LOCAL_VERIFIER_SECRET")
    if not mcp_secret:
        raise Fail("TELECOM_MCP_LOCAL_VERIFIER_SECRET is not set in telecom-mcp/.env (needs 32+ bytes).")
    if mcp_secret != mw_secret:
        raise Fail(
            "The two local verifier secrets differ. One token is verified at both hops, so\n"
            "they must be byte-identical. Copy TELECOM_MCP_LOCAL_VERIFIER_SECRET into\n"
            "telecom-middleware/.env as TELECOM_MW_LOCAL_VERIFIER_SECRET and run this again."
        )

    for port, what in ((MW_PORT, "the middleware"), (MCP_PORT, "the tool server")):
        if not port_is_free(port):
            raise Fail(
                f"Port {port} is already listening, so {what} cannot bind it.\n"
                f"Stop whatever holds it and run this again "
                f"(lsof -ti tcp:{port} on macOS/Linux; Get-NetTCPConnection -LocalPort {port} on Windows)."
            )

    processes: list[subprocess.Popen] = []

    def stop_all() -> None:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()

    atexit.register(stop_all)
    signal.signal(signal.SIGINT, lambda *_: sys.exit(130))

    say(f"Starting the middleware on :{MW_PORT} (external-test profile)")
    processes.append(
        start(
            "middleware",
            MW_ROOT,
            ["uv", "run", "--env-file", ".env", "--env-file", str(PROFILE / "middleware.env"),
             "telecom-middleware", "serve"],
        )
    )
    wait_for(f"http://127.0.0.1:{MW_PORT}/healthz", "The middleware", args.timeout)
    say("  middleware is up")

    say(f"Starting the MCP server on :{MCP_PORT} (external-test profile)")
    processes.append(
        start(
            "mcp",
            MCP_ROOT,
            ["uv", "run", "--env-file", ".env", "--env-file", str(PROFILE / "mcp.env"),
             "telecom-mcp", "serve", "--transport", "http"],
        )
    )
    ready = wait_for(f"http://127.0.0.1:{MCP_PORT}/readyz", "The MCP server", args.timeout)

    for component in ready.get("components", []):
        say(f"  {component.get('name', '?'):<22} {component.get('status', '?')}")
    if ready.get("status") != "healthy":
        raise Fail("The tool server cannot reach the middleware. Read testsprite/.logs/middleware.log first.")

    # --- one token, both audiences ---------------------------------------------------
    say("Minting a token")
    minted = subprocess.run(  # noqa: S603
        ["uv", "run", "python", "scripts/mint_dev_token.py", "--cx-id", args.cx_id,
         "--minutes", str(args.minutes)],
        cwd=MCP_ROOT,
        capture_output=True,
        text=True,
        env={**os.environ,
             "TELECOM_MCP_LOCAL_VERIFIER_SECRET": mcp_secret,
             "TELECOM_MCP_JWT_AUDIENCE": "https://api.telecom.example/v1"},
    )
    token = minted.stdout.strip()
    if minted.returncode != 0 or token.count(".") != 2:
        raise Fail(f"mint_dev_token.py did not return a JWT:\n{minted.stdout}{minted.stderr}")
    say(f"  a customer token for {args.cx_id}, good for {args.minutes} minutes")

    # --- prove it, locally, before anything public -----------------------------------
    say("Preflight against the local pair")
    local = subprocess.run(  # noqa: S603
        [sys.executable, str(HERE / "preflight.py"), "--token", token,
         "--mcp", f"http://127.0.0.1:{MCP_PORT}", "--middleware", f"http://127.0.0.1:{MW_PORT}"],
    )
    if local.returncode != 0:
        raise Fail("Preflight failed locally. Nothing public will behave better - fix the lines above first.")

    if args.public_mcp or args.public_middleware:
        say("Preflight against the public URLs")
        command = [sys.executable, str(HERE / "preflight.py"), "--token", token]
        if args.public_mcp:
            command += ["--mcp", args.public_mcp]
        if args.public_middleware:
            command += ["--middleware", args.public_middleware]
        if subprocess.run(command).returncode != 0:  # noqa: S603
            raise Fail("Healthy locally but not through the tunnel. Check each tunnel points at the right port.")

    say("\nReady. The token is below - set it on BOTH TestSprite projects:\n")
    say(token)
    say(f"""
Next, in order:

  1. Tunnel both ports, if you have not already:
        cloudflared tunnel --url http://localhost:{MCP_PORT}     # tool server
        cloudflared tunnel --url http://localhost:{MW_PORT}     # middleware

  2. Resolve the tunnel URLs into a build/ copy - the V3 sandbox rejects a
     non-literal base URL in an uploaded file, and only there:
        python stamp_target_url.py https://<mcp-host> https://<middleware-host>

  3. Set the credential on both projects:
        python run_testsprite.py credentials

  4. Re-upload from build/ and smoke three tests before running all eighteen.

The token expires in {args.minutes} minutes. A suite that starts inside that window and
finishes outside it fails as `token_expired` and costs a full run, so re-mint rather
than reusing this one tomorrow.

Both services are still running; logs are in testsprite/.logs/. Ctrl-C stops them.""")

    try:
        while all(process.poll() is None for process in processes):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
