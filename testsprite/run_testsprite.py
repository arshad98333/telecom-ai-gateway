#!/usr/bin/env python
"""Stand up the TestSprite projects for this workspace and run the suites.

Two backend projects, because the two services have different URLs and different
credentials and a single project cannot hold both:

    telecom-mcp-tools   the MCP tool server  - 12 tests
    telecom-middleware  the backing API      -  6 tests

The tests are hand-authored (the official skill's path 3b) rather than generated. The
OpenAPI specs are uploaded anyway: they are what `testsprite test plan generate` reads
if you later want TestSprite to propose additional cases.

Run it in stages, cheapest first:

    python run_testsprite.py preflight
    python run_testsprite.py setup --mcp-url https://... --middleware-url https://...
    python run_testsprite.py credentials
    python run_testsprite.py create
    python run_testsprite.py smoke
    python run_testsprite.py all

setup writes .testsprite-state.json; every later stage reads it. TestSprite runs backend
tests from its own cloud, so both URLs must be publicly reachable https - the CLI
rejects localhost and private addresses, and so does this.
"""

from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.parse
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
STATE = HERE / ".testsprite-state.json"


class Fail(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(f"\n{message}\n")


def head(text: str) -> None:
    print(f"\n=== {text} ===", flush=True)


def testsprite(*args: str, capture: bool = False) -> str:
    if shutil.which("testsprite") is None:
        raise Fail("The testsprite CLI is not on PATH. Install it, then run `testsprite setup`.")
    result = subprocess.run(  # noqa: S603
        ["testsprite", *args], capture_output=capture, text=True, check=False
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() if capture else ""
        raise Fail(f"testsprite {' '.join(args)} failed (exit {result.returncode}).\n{detail}")
    return (result.stdout or "") if capture else ""


def read_state() -> dict:
    if not STATE.exists():
        raise Fail(f"No state file at {STATE}. Run the setup stage first.")
    return json.loads(STATE.read_text(encoding="utf-8"))


def check_public(url: str) -> None:
    host = urllib.parse.urlparse(url).hostname or ""
    if not url.startswith("https://"):
        raise Fail(f"TestSprite calls from the public internet. {url} must be https.")
    if host in {"localhost", "127.0.0.1", "::1"}:
        raise Fail(f"TestSprite runs backend tests from its cloud and rejects {url}. Use a public URL.")
    try:
        if ipaddress.ip_address(host).is_private:
            raise Fail(f"TestSprite cannot reach the private address {url}. Use a public URL or a tunnel.")
    except ValueError:
        pass  # a hostname, which is what we want


# --- stages ---------------------------------------------------------------------------

def stage_preflight(_: argparse.Namespace) -> None:
    head("Preflight")
    testsprite("--version")
    try:
        testsprite("auth", "status")
    except SystemExit:
        raise Fail("Not authenticated. Run: testsprite setup") from None


def stage_setup(args: argparse.Namespace) -> None:
    head("Creating the projects")
    if not args.mcp_url or not args.middleware_url:
        raise Fail("Both --mcp-url and --middleware-url are required for the setup stage.")
    for url in (args.mcp_url, args.middleware_url):
        check_public(url)

    mcp = json.loads(testsprite("project", "create", "--type", "backend",
                                "--name", "telecom-mcp-tools", "--output", "json", capture=True))
    mw = json.loads(testsprite("project", "create", "--type", "backend",
                               "--name", "telecom-middleware", "--output", "json", capture=True))
    state = {
        "mcpProjectId": mcp["projectId"],
        "middlewareProjectId": mw["projectId"],
        "targetUrlMcp": args.mcp_url.rstrip("/"),
        "targetUrlMiddleware": args.middleware_url.rstrip("/"),
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"mcp project        : {state['mcpProjectId']}")
    print(f"middleware project : {state['middlewareProjectId']}")

    head("Uploading the API specs")
    # Generated from the code, so they cannot drift: the middleware's is FastAPI's own
    # document, the tool server's is built from the frozen TOOL_SPECS catalogue.
    testsprite("project", "docs", "upload", str(HERE / "specs" / "telecom-mcp-tools.openapi.json"),
               "--project", state["mcpProjectId"], "--role", "api-doc")
    testsprite("project", "docs", "upload", str(HERE / "specs" / "telecom-middleware.openapi.json"),
               "--project", state["middlewareProjectId"], "--role", "api-doc")


def stage_credentials(_: argparse.Namespace) -> None:
    head("Configuring the bearer tokens")
    state = read_state()
    print("""Each project needs a tenant JWT. `python start_testable.py` mints one that both
hops accept and prints it. The tests read the token out of __AUTH_HEADERS__ and never
hardcode it, so this is the only place a credential is entered. A static token expires
within hours; for anything recurring use `testsprite project auto-auth <projectId> ...`.
""")
    mcp_token = getpass.getpass("Bearer token for the tool server (audience telecom-mcp-tools): ")
    mw_token = getpass.getpass("Bearer token for the middleware (audience https://api.telecom.example/v1): ")
    testsprite("project", "credential", state["mcpProjectId"], "--type", "Bearer token",
               "--credential", mcp_token)
    testsprite("project", "credential", state["middlewareProjectId"], "--type", "Bearer token",
               "--credential", mw_token)


def stage_create(args: argparse.Namespace) -> None:
    head("Creating the tests")
    state = read_state()
    source = HERE / ("build" if args.from_build else "tests")
    if not source.exists():
        raise Fail(f"{source} does not exist. Run stamp_target_url.py first, or drop --from-build.")
    # create-batch is frontend-only, so backend tests go in one at a time. --name is
    # required and is what shows up in the dashboard, so it is a sentence, not a filename.
    suites = (
        (source / "mcp", state["mcpProjectId"], "MCP"),
        (source / "middleware", state["middlewareProjectId"], "Middleware"),
    )
    for directory, project, prefix in suites:
        for path in sorted(directory.glob("*.py")):
            behaviour = re.sub(r"^\d+_", "", path.stem).replace("_", " ")
            name = f"{prefix} - {behaviour}"
            print(f"  {name}")
            testsprite("test", "create", "--type", "backend", "--project", project,
                       "--name", name, "--code-file", str(path))
    print(f"\nList them with: testsprite test list --project {state['mcpProjectId']}")


def stage_smoke(_: argparse.Namespace) -> None:
    head("Smoke run (3 tests)")
    state = read_state()
    print("Deliberately not the whole suite - 18 backend tests is real credit.")

    # Highest-value happy paths: the two liveness probes and the tool catalogue.
    mcp_tests = json.loads(testsprite("test", "list", "--project", state["mcpProjectId"],
                                      "--output", "json", capture=True))["tests"]
    chosen = [t for t in mcp_tests if re.search(r"liveness|catalogue", t["name"], re.I)][:2]
    for test in chosen:
        testsprite("test", "run", test["id"], "--target-url", state["targetUrlMcp"],
                   "--wait", "--timeout", "600", "--output", "json")

    mw_tests = json.loads(testsprite("test", "list", "--project", state["middlewareProjectId"],
                                     "--output", "json", capture=True))["tests"]
    for test in [t for t in mw_tests if re.search(r"health", t["name"], re.I)][:1]:
        testsprite("test", "run", test["id"], "--target-url", state["targetUrlMiddleware"],
                   "--wait", "--timeout", "600", "--output", "json")


def stage_all(args: argparse.Namespace) -> None:
    stage_preflight(args)
    stage_credentials(args)
    stage_create(args)
    stage_smoke(args)
    head("Full suite")
    state = read_state()
    # Backend runs are wave-ordered by the engine; do not hand-sequence them.
    testsprite("test", "run", "--all", "--project", state["mcpProjectId"],
               "--wait", "--timeout", "900", "--output", "json")
    testsprite("test", "run", "--all", "--project", state["middlewareProjectId"],
               "--wait", "--timeout", "900", "--output", "json")


STAGES = {
    "preflight": stage_preflight,
    "setup": stage_setup,
    "credentials": stage_credentials,
    "create": stage_create,
    "smoke": stage_smoke,
    "all": stage_all,
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("stage", choices=sorted(STAGES), nargs="?", default="preflight")
    parser.add_argument("--mcp-url", help="public https URL of the tool server (setup only)")
    parser.add_argument("--middleware-url", help="public https URL of the middleware (setup only)")
    parser.add_argument("--from-build", action="store_true",
                        help="upload the stamped copies in build/ rather than the sources")
    args = parser.parse_args()
    STAGES[args.stage](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
