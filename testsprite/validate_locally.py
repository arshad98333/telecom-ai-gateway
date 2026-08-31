#!/usr/bin/env python
"""Run the TestSprite test files locally, before spending a single credit on them.

TestSprite's runner is a sandbox that injects three globals - ``TARGET_URL``,
``__AUTH_CREDENTIAL__`` and ``__AUTH_HEADERS__`` - and then executes the file top to
bottom. This reproduces exactly that, against both services running for real over HTTP:

    TestSprite test  ->  tool server (uvicorn)  ->  middleware (uvicorn)  ->  memory store

Why bother, when TestSprite will run them anyway: a test with a typo, a wrong field name
or an assertion that can never hold costs credits and an afternoon to discover remotely.
Here it costs eight seconds. This is a dry run, not a replacement - TestSprite's own run
is the one that counts, because it runs from outside against a deployed URL.

    uv run --project ../e2e python validate_locally.py
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import pathlib
import sys
import traceback
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import uvicorn

HERE = pathlib.Path(__file__).resolve().parent
SECRET = "local-validation-signing-secret-long-enough"
MCP_AUDIENCE = "telecom-mcp-tools"
API_AUDIENCE = "https://api.telecom.example/v1"
NAMESPACE = "https://telecom.example/"
TENANT = "tenant-eu-1"
CUSTOMER = "CX-1234"
MW_PORT = 9101
MCP_PORT = 9100

PERMISSIONS = [
    "account:read", "service:read", "order:read", "billing:read", "network:read",
    "ticket:read", "ticket:write", "callback:write", "refund:request",
    "case:read", "case:write",
]


def make_token() -> str:
    """One token, two audiences - which is what the real deployment issues."""
    claims: dict[str, Any] = {
        "sub": CUSTOMER,
        "aud": [MCP_AUDIENCE, API_AUDIENCE],
        "exp": int((datetime.now(UTC) + timedelta(minutes=30)).timestamp()),
        "jti": "local-validation",
        "permissions": PERMISSIONS,
        "scope": " ".join(PERMISSIONS),
        f"{NAMESPACE}tenant_id": TENANT,
        f"{NAMESPACE}role": "customer",
        f"{NAMESPACE}cx_id": CUSTOMER,
    }
    return jwt.encode(claims, SECRET, algorithm="HS256")


class RealClock:
    """The middleware's clock port. Real time, because these tests do not move it."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        import time

        return time.monotonic()


async def build_middleware() -> Any:
    from telecom_middleware.api.app import build_app
    from telecom_middleware.api.container import build_context
    from telecom_middleware.config.settings import load_settings
    from telecom_middleware.repositories.memory import MemoryStore
    from telecom_middleware.services.seed import seed_demo_data

    store = MemoryStore()
    clock = RealClock()
    context = build_context(
        load_settings(
            {
                "TELECOM_MW_LOCAL_VERIFIER_SECRET": SECRET,
                "TELECOM_MW_JWT_AUDIENCE": API_AUDIENCE,
                "TELECOM_MW_LOG_LEVEL": "ERROR",
                "TELECOM_MW_CHANGE_STREAM_ENABLED": "false",
            }
        ),
        store=store,
        clock=clock,
        configure_logs=False,
    )
    app = build_app(context, start_realtime=False)
    await seed_demo_data(store, tenant_id=TENANT, clock=clock)
    return app


def build_mcp() -> Any:
    from telecom_mcp.api.container import build_application
    from telecom_mcp.api.http_app import build_http_app
    from telecom_mcp.api.server import TelecomMCPServer
    from telecom_mcp.api.tokens import ContextTokenSource
    from telecom_mcp.config.settings import load_settings
    from telecom_mcp.domain.ports import UUIDGenerator

    application = build_application(
        load_settings(
            {
                "TELECOM_MCP_LOCAL_VERIFIER_SECRET": SECRET,
                "TELECOM_MCP_JWT_AUDIENCE": MCP_AUDIENCE,
                "TELECOM_MCP_LOG_LEVEL": "ERROR",
                "TELECOM_MCP_RETRY_BASE_DELAY_S": "0.001",
                "TELECOM_MCP_BACKEND": "http",
                "TELECOM_MCP_BACKEND_BASE_URL": f"http://127.0.0.1:{MW_PORT}/api/v1",
                "TELECOM_MCP_BACKEND_API_KEY": "local-service-credential",
                # A whole regression suite is not one customer conversation. The
                # per-case action budget allows a handful of irreversible actions
                # per hour, which is right for a support case and wrong for a test
                # target - so raise it here, exactly as the target under test
                # should raise it, rather than writing tests that dodge it.
                "TELECOM_MCP_GUARDRAIL_WRITE_ACTIONS_PER_CASE": "200",
            }
        )
    )
    server = TelecomMCPServer(
        application, tokens=ContextTokenSource(), id_generator=UUIDGenerator()
    )
    return build_http_app(application, server)


async def serve(app: Any, port: int) -> uvicorn.Server:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False)
    server = uvicorn.Server(config)
    asyncio.get_running_loop().create_task(server.serve())
    for _ in range(100):
        if server.started:
            return server
        await asyncio.sleep(0.05)
    raise RuntimeError(f"the server on {port} never started")


def run_file(path: pathlib.Path, target_url: str, token: str) -> tuple[bool, str]:
    """Execute one test file the way TestSprite's V3 runner does.

    The runner injects the credential block and __VARS__ - and *nothing else*. It does
    not provide a base URL, which is why the uploaded copies carry a resolved literal
    (stamp_target_url.py) rather than reading anything at run time.

    The sources themselves take the target from TARGET_URL, so here the target is set
    in the environment and the file is executed unmodified. Nothing rewrites a source.
    To dry-run the bytes that actually get uploaded, stamp into build/ and point this
    at that directory instead.
    """
    source = path.read_text(encoding="utf-8")
    namespace: dict[str, Any] = {
        "__name__": "__testsprite__",
        # The exact names TestSprite prepends, with the same shapes.
        "__AUTH_CREDENTIAL__": f"Bearer {token}",
        "__AUTH_TYPE__": "Bearer token",
        "__AUTH_HEADERS__": {"Authorization": f"Bearer {token}"},
        "__VARS__": {},
    }
    previous = os.environ.get("TARGET_URL")
    os.environ["TARGET_URL"] = target_url
    try:
        exec(compile(source, str(path), "exec"), namespace)  # noqa: S102
    except AssertionError as failure:
        return False, f"assertion failed: {failure}"
    except Exception:  # noqa: BLE001 - a test file may raise anything
        return False, traceback.format_exc(limit=3).strip().splitlines()[-1]
    finally:
        if previous is None:
            os.environ.pop("TARGET_URL", None)
        else:
            os.environ["TARGET_URL"] = previous
    return True, "passed"


async def main() -> int:
    token = make_token()
    middleware = await build_middleware()
    mw_server = await serve(middleware, MW_PORT)
    mcp_server_handle = await serve(build_mcp(), MCP_PORT)

    async with httpx.AsyncClient(timeout=5) as probe:
        for url in (f"http://127.0.0.1:{MW_PORT}/healthz", f"http://127.0.0.1:{MCP_PORT}/healthz"):
            assert (await probe.get(url)).status_code == 200, url

    suites = [
        ("mcp", HERE / "tests" / "mcp", f"http://127.0.0.1:{MCP_PORT}"),
        ("mcp-integration", HERE / "tests" / "mcp_integration", f"http://127.0.0.1:{MCP_PORT}"),
        ("middleware", HERE / "tests" / "middleware", f"http://127.0.0.1:{MW_PORT}"),
        (
            "middleware-integration",
            HERE / "tests" / "middleware_integration",
            f"http://127.0.0.1:{MW_PORT}",
        ),
    ]

    results: list[dict[str, Any]] = []
    for label, directory, target in suites:
        print(f"\n=== {label} ({target}) ===")
        for path in sorted(directory.glob("*.py")):
            ok, detail = await asyncio.to_thread(run_file, path, target, token)
            results.append({"suite": label, "file": path.name, "passed": ok, "detail": detail})
            mark = "PASS" if ok else "FAIL"
            print(f"  [{mark}] {path.name}")
            if not ok:
                print(f"         {detail}")

    for server in (mw_server, mcp_server_handle):
        server.should_exit = True
    await asyncio.sleep(0.4)

    passed = sum(1 for r in results if r["passed"])
    print(f"\n{passed}/{len(results)} TestSprite test files pass against the live services")
    (HERE / "validation-result.json").write_text(
        json.dumps(
            {"generated_at": datetime.now(UTC).isoformat(), "passed": passed,
             "total": len(results), "results": results},
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        sys.exit(asyncio.run(main()))
