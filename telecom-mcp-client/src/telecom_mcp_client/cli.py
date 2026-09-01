"""A minimal CLI: `telecom-mcp-client list-tools`, `telecom-mcp-client call <tool>
--json '{...}'`. Not a UX exercise — this is enough to drive telecom-mcp from a
terminal or a script for ops/debugging.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from telecom_mcp_client.client import MCPClient, MCPClientError
from telecom_mcp_client.models import Outcome


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="telecom-mcp-client")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("TELECOM_MCP_URL", "http://127.0.0.1:8080"),
        help="telecom-mcp base URL (default: $TELECOM_MCP_URL or http://127.0.0.1:8080)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("TELECOM_MCP_ACCESS_TOKEN"),
        help="bearer token (default: $TELECOM_MCP_ACCESS_TOKEN)",
    )
    parser.add_argument("--connect-timeout", type=float, default=3.0)
    parser.add_argument("--read-timeout", type=float, default=10.0)
    parser.add_argument("--max-retries", type=int, default=3)

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list-tools", help="list the tools this token may call")

    call = subparsers.add_parser("call", help="call one tool")
    call.add_argument("tool")
    call.add_argument("--json", dest="arguments_json", default="{}", help="arguments, as JSON")

    return parser


async def _run(args: argparse.Namespace) -> int:
    async with MCPClient(
        base_url=args.base_url,
        token=args.token,
        connect_timeout_s=args.connect_timeout,
        read_timeout_s=args.read_timeout,
        max_retries=args.max_retries,
    ) as client:
        try:
            await client.initialize()
        except MCPClientError as exc:
            print(f"could not connect: {exc}", file=sys.stderr)  # noqa: T201
            return 2

        if args.command == "list-tools":
            try:
                tools = await client.list_tools()
            except MCPClientError as exc:
                print(f"tools/list failed: {exc}", file=sys.stderr)  # noqa: T201
                return 2
            print(json.dumps(tools, indent=2))  # noqa: T201
            return 0

        if args.command == "call":
            try:
                arguments: Any = json.loads(args.arguments_json)
            except json.JSONDecodeError as exc:
                print(f"--json was not valid JSON: {exc}", file=sys.stderr)  # noqa: T201
                return 2
            result = await client.call_tool(args.tool, arguments)
            if result.outcome is Outcome.OK and result.ok is not None:
                print(  # noqa: T201
                    json.dumps(
                        {
                            "content": result.ok.content,
                            "structuredContent": result.ok.structured_content,
                        },
                        indent=2,
                    )
                )
                return 0
            error = result.error
            message = error.message if error else "unknown failure"
            print(f"{result.outcome.value}: {message}", file=sys.stderr)  # noqa: T201
            return 1

        return 2


def main() -> int:
    args = _build_parser().parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
