"""The MCP server: protocol handling only, no business logic and no security logic.

Two handlers, both thin. ``list_tools`` returns the tools this identity may call, which
is why an unauthenticated caller sees an empty catalogue rather than a menu. ``call_tool``
hands the request to the executor and turns its result into an MCP response.

Input validation is deliberately not delegated to the SDK. Our kernel validates against
the same schema and owns the error shape, and two validators that can disagree are
worse than one.
"""

from __future__ import annotations

import json
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server

from telecom_mcp._version import TOOL_CONTRACT_VERSION
from telecom_mcp.api.container import Application
from telecom_mcp.api.executor import visible_tools
from telecom_mcp.api.tokens import TokenSource
from telecom_mcp.domain.ports import IdGenerator
from telecom_mcp.domain.tools import TOOL_SPECS, ToolSpec
from telecom_mcp.observability.logging import get_logger
from telecom_mcp.security.identity import ToolRequest
from telecom_mcp.security.verifier import TokenVerificationError

logger = get_logger(__name__)

SERVER_NAME = "telecom-mcp-tools"


class ToolRefusedError(Exception):
    """A refusal on its way to the MCP transport.

    Carries the same document ``call_tool_for_caller`` returns, serialised, so a caller
    reading the error text gets the error code, the operation and the correlation id
    rather than a sentence about schema validation.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        super().__init__(json.dumps(payload, indent=2, default=str))


def describe(spec: ToolSpec) -> types.Tool:
    """Render one tool for the MCP catalogue."""
    return types.Tool(
        name=spec.name,
        description=spec.description,
        inputSchema=spec.input_model.model_json_schema(),
        outputSchema=spec.output_model.model_json_schema(),
    )


class TelecomMCPServer:
    """Wraps the low-level MCP server so both transports share one implementation."""

    def __init__(
        self,
        application: Application,
        *,
        tokens: TokenSource,
        id_generator: IdGenerator,
    ) -> None:
        self._app = application
        self._tokens = tokens
        self._ids = id_generator
        self.server: Server[Any, Any] = Server(
            name=SERVER_NAME,
            version=TOOL_CONTRACT_VERSION,
            instructions=(
                "Telecom customer support tools. Every call is authorized against the "
                "caller's identity; write operations require an idempotency key."
            ),
        )
        self._register()

    def _register(self) -> None:
        # The SDK's decorators are untyped, so the two handlers are registered
        # explicitly. Both bodies are one line that delegates to a typed method, which
        # is where the tests point.
        async def list_tools() -> list[types.Tool]:
            return await self.list_tools_for_caller()

        async def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            result = await self.call_tool_for_caller(name, arguments)
            if "error" in result:
                # The SDK validates any dictionary a handler returns against the tool's
                # outputSchema and rejects a refusal as a malformed *success*, replacing
                # it with "Output validation error". Raising is how the SDK is told the
                # call failed; it renders the exception's text as an isError result, so
                # the agent still receives the whole refusal document, unaltered.
                raise ToolRefusedError(result)
            return result

        self.server.list_tools()(list_tools)  # type: ignore[no-untyped-call]
        self.server.call_tool(validate_input=False)(call_tool)

    async def list_tools_for_caller(self) -> list[types.Tool]:
        """Only the tools the caller's identity may invoke.

        An unverifiable token yields an empty list rather than an error: the catalogue
        is not a place to leak whether a name exists.
        """
        token = self._tokens.current_token()
        if not token:
            return []
        try:
            identity = await self._app.executor.authorizer.verifier.verify(token)
        except TokenVerificationError:
            logger.warning("tool_listing_unauthenticated")
            return []
        return [describe(spec) for spec in visible_tools(identity.scopes, TOOL_SPECS)]

    async def call_tool_for_caller(
        self, name: str, arguments: dict[str, Any], *, case_id: str | None = None
    ) -> dict[str, Any]:
        """Execute one tool call and return a structured result.

        Always returns a dictionary. A refusal is data the agent can act on, not an
        exception that would strand a voice call mid-sentence.
        """
        request = ToolRequest(
            tool_name=name,
            arguments=arguments or {},
            token=self._tokens.current_token(),
            correlation_id=self._ids.new_id(),
            case_id=case_id,
            contract_version=TOOL_CONTRACT_VERSION,
        )
        result = await self._app.executor.execute(request)
        if result.error is not None:
            return result.error.to_dict()
        return result.output or {}
