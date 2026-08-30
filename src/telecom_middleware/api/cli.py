"""The command line entry point.

Configuration failures exit non-zero with one message naming every problem, before any
server starts. Nothing starts halfway.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence

from telecom_middleware._version import __version__
from telecom_middleware.config.settings import Settings, load_settings
from telecom_middleware.domain.errors import ConfigurationError

EXIT_OK = 0
EXIT_CONFIGURATION_ERROR = 78  # EX_CONFIG, so a supervisor can tell why we exited
EXIT_SCHEMA_INCOMPLETE = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="telecom-middleware", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="run the HTTP API")
    serve.add_argument("--reload", action="store_true", help="reload on change, for development")

    commands.add_parser("check-config", help="validate configuration and exit")
    commands.add_parser("migrate", help="create collections, validators and indexes")
    commands.add_parser("verify-schema", help="report declared indexes the database is missing")
    commands.add_parser("check-store", help="check the configured database is usable")
    seed_command = commands.add_parser("seed", help="load the demo dataset")
    seed_command.add_argument("--tenant", default="tenant-eu-1")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        settings = load_settings()
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)  # noqa: T201 - the process boundary
        return EXIT_CONFIGURATION_ERROR

    if args.command == "check-config":
        print(json.dumps(settings.describe(), indent=2, default=str))  # noqa: T201
        return EXIT_OK
    if args.command == "migrate":
        return asyncio.run(migrate(settings))
    if args.command == "verify-schema":
        return asyncio.run(verify_schema(settings))
    if args.command == "check-store":
        return asyncio.run(check_store(settings))
    if args.command == "seed":
        return asyncio.run(seed(settings, args.tenant))

    _serve(settings, reload=args.reload)
    return EXIT_OK


def _serve(settings: Settings, *, reload: bool) -> None:
    import uvicorn

    from telecom_middleware.api.app import build_app
    from telecom_middleware.api.container import build_context

    app = build_app(build_context(settings))
    uvicorn.run(
        app,
        host=settings.http_host,
        port=settings.http_port,
        reload=reload,
        log_config=None,  # our own structured logging is already configured
    )


async def migrate(settings: Settings) -> int:
    """Apply the schema. Public so it can be awaited directly rather than re-entered."""
    from telecom_middleware.api.container import build_context

    context = build_context(settings, configure_logs=False)
    await context.store.start()
    await context.store.close()
    print("schema applied")  # noqa: T201
    return EXIT_OK


async def verify_schema(settings: Settings) -> int:
    from telecom_middleware.api.container import build_context
    from telecom_middleware.repositories.schema import missing_indexes

    context = build_context(settings, configure_logs=False)
    database = getattr(context.store, "database", None)
    if database is None:
        print("the in-memory store has no indexes to verify")  # noqa: T201
        return EXIT_OK
    gaps = await missing_indexes(database)
    await context.store.close()
    if not gaps:
        print("every declared index is present")  # noqa: T201
        return EXIT_OK
    print(json.dumps({"missing_indexes": gaps}, indent=2), file=sys.stderr)  # noqa: T201
    return EXIT_SCHEMA_INCOMPLETE


async def check_store(settings: Settings) -> int:
    """Report whether the configured database is usable, without printing the URI."""
    from telecom_middleware.api.container import build_context
    from telecom_middleware.services.diagnostics import inspect_store

    context = build_context(settings, configure_logs=False)
    try:
        report = await inspect_store(context.store)
    finally:
        await context.store.close()

    print(report.render())  # noqa: T201
    if report.ok:
        return EXIT_OK
    print("\nthe database is not usable as configured; see the failures above", file=sys.stderr)  # noqa: T201
    return EXIT_SCHEMA_INCOMPLETE


async def seed(settings: Settings, tenant: str) -> int:
    from telecom_middleware.api.container import build_context
    from telecom_middleware.services.seed import seed_demo_data

    context = build_context(settings, configure_logs=False)
    await context.store.start()
    summary = await seed_demo_data(context.store, tenant_id=tenant, clock=context.clock)
    await context.store.close()
    print(json.dumps(summary, indent=2))  # noqa: T201
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
