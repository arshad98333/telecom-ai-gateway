"""The command line entry point.

Configuration failures exit non-zero with one message naming every problem, before any
transport starts. Nothing starts halfway.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence

from telecom_mcp._version import __version__
from telecom_mcp.api.container import build_application
from telecom_mcp.api.server import TelecomMCPServer
from telecom_mcp.api.tokens import ContextTokenSource, EnvTokenSource
from telecom_mcp.config.settings import load_settings
from telecom_mcp.domain.errors import ConfigurationError
from telecom_mcp.domain.ports import UUIDGenerator
from telecom_mcp.security.audit import AuditRecord, verify_chain

EXIT_OK = 0
EXIT_CONFIGURATION_ERROR = 78  # EX_CONFIG, so a supervisor can tell why we exited
EXIT_AUDIT_BROKEN = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="telecom-mcp", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="run the MCP server")
    serve.add_argument("--transport", choices=("stdio", "http"), default="stdio")

    commands.add_parser("check-config", help="validate configuration and exit")

    audit = commands.add_parser("verify-audit", help="verify an audit log's hash chain")
    audit.add_argument("path", help="path to a JSON-lines audit log, or - for stdin")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "verify-audit":
        return _verify_audit(args.path)

    try:
        settings = load_settings()
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)  # noqa: T201 - the process boundary
        return EXIT_CONFIGURATION_ERROR

    if args.command == "check-config":
        print(json.dumps(settings.describe(), indent=2, default=str))  # noqa: T201
        return EXIT_OK

    if args.transport == "stdio":
        asyncio.run(_serve_stdio(settings))
    else:
        _serve_http(settings)
    return EXIT_OK


async def _serve_stdio(settings: object) -> None:
    from mcp.server.stdio import stdio_server

    from telecom_mcp.config.settings import Settings

    assert isinstance(settings, Settings)
    application = build_application(settings)
    server = TelecomMCPServer(application, tokens=EnvTokenSource(), id_generator=UUIDGenerator())
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.server.run(
                read_stream,
                write_stream,
                server.server.create_initialization_options(),
            )
    finally:
        await application.aclose()


def _serve_http(settings: object) -> None:
    import uvicorn

    from telecom_mcp.api.http_app import build_http_app
    from telecom_mcp.config.settings import Settings

    assert isinstance(settings, Settings)
    application = build_application(settings)
    server = TelecomMCPServer(
        application, tokens=ContextTokenSource(), id_generator=UUIDGenerator()
    )
    uvicorn.run(
        build_http_app(application, server),
        host=settings.http_host,
        port=settings.http_port,
        log_config=None,  # our own structured logging is already configured
    )


def _verify_audit(path: str) -> int:
    from pathlib import Path

    lines = (
        sys.stdin.read().splitlines()
        if path == "-"
        else Path(path).read_text(encoding="utf-8").splitlines()
    )
    records = [
        AuditRecord(**{**json.loads(line), "decision": json.loads(line)["decision"]})
        for line in lines
        if line.strip()
    ]
    broken = verify_chain(records)
    if broken is None:
        print(f"audit chain intact: {len(records)} records")  # noqa: T201
        return EXIT_OK
    print(f"audit chain broken at record {broken}", file=sys.stderr)  # noqa: T201
    return EXIT_AUDIT_BROKEN


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
