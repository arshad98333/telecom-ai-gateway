"""Single source of the package version, read by the package metadata."""

from importlib.metadata import PackageNotFoundError, version

try:  # pragma: no cover - exercised only when the package is not installed
    __version__ = version("telecom-mcp-tools")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0+unknown"

#: The MCP tool contract version. Bumped only on a breaking schema change.
TOOL_CONTRACT_VERSION = "1"
