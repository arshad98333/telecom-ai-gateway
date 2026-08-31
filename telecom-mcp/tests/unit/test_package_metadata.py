"""The package must be importable and must report a real version."""

import telecom_mcp
from telecom_mcp._version import TOOL_CONTRACT_VERSION


def test_package_reports_an_installed_version() -> None:
    assert telecom_mcp.__version__ != "0.0.0+unknown"


def test_tool_contract_version_is_pinned() -> None:
    assert TOOL_CONTRACT_VERSION == "1"
