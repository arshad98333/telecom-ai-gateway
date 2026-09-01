"""A reference/ops client for telecom-mcp's streamable-HTTP MCP endpoint."""

from telecom_mcp_client.client import MCPClient, MCPClientError, MCPHandshakeError
from telecom_mcp_client.models import Outcome, ToolCallError, ToolCallOk, ToolCallResult

__version__ = "0.1.0"

__all__ = [
    "MCPClient",
    "MCPClientError",
    "MCPHandshakeError",
    "Outcome",
    "ToolCallError",
    "ToolCallOk",
    "ToolCallResult",
]
