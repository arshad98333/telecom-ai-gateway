"""The server surface: what a real MCP client sees, including what it must not see."""

from typing import Any

import pytest

from telecom_mcp.api.server import TelecomMCPServer, ToolRefusedError
from telecom_mcp.api.tokens import EnvTokenSource
from telecom_mcp.domain.tools import BLOCKED_TOOL_NAMES
from tests.factory import CUSTOMER, build_test_application, make_token
from tests.fakes import SequentialIds


class StaticToken:
    def __init__(self, token: str) -> None:
        self._token = token

    def current_token(self) -> str:
        return self._token


def build_server(token: str, **kwargs: Any) -> TelecomMCPServer:
    harness = build_test_application(**kwargs)
    server = TelecomMCPServer(
        harness.app, tokens=StaticToken(token), id_generator=SequentialIds("corr")
    )
    server.harness = harness  # type: ignore[attr-defined]
    return server


async def test_the_listing_shows_only_the_tools_this_identity_may_call() -> None:
    server = build_server(make_token(scope="account:read billing:read"))

    names = {tool.name for tool in await server.list_tools_for_caller()}

    assert names == {"get_customer_account", "get_invoice_summary"}


async def test_an_unauthenticated_caller_sees_an_empty_catalogue() -> None:
    assert await build_server("").list_tools_for_caller() == []


async def test_an_invalid_token_is_reported_rather_than_answered_with_silence() -> None:
    """A caller holding a bad token is told about its own credential, not about names.

    Deliberately different from the anonymous case above. An empty catalogue here read
    as "the contract is broken" to everyone who saw it, including an external test run
    that filed a mis-minted token as a product defect.
    """
    with pytest.raises(ToolRefusedError) as raised:
        await build_server("not.a.token").list_tools_for_caller()

    payload = raised.value.payload["error"]
    assert payload["code"] == "token_invalid"
    assert payload["operation"] == "tools/list"


async def test_a_scope_the_identity_lacks_is_still_omitted_silently() -> None:
    """Narrowing stays silent: that omission genuinely would enumerate tool names."""
    names = {
        tool.name
        for tool in await build_server(make_token(scope="account:read")).list_tools_for_caller()
    }

    assert names == {"get_customer_account"}


async def test_no_blocked_tool_is_ever_listed() -> None:
    server = build_server(make_token(scope="service:change service:cancel"))

    names = {tool.name for tool in await server.list_tools_for_caller()}

    assert not names & set(BLOCKED_TOOL_NAMES)


async def test_each_listed_tool_carries_an_input_and_an_output_schema() -> None:
    server = build_server(make_token())

    for tool in await server.list_tools_for_caller():
        assert tool.inputSchema["type"] == "object"
        assert tool.outputSchema is not None
        assert tool.description


async def test_calling_a_permitted_tool_returns_structured_content() -> None:
    server = build_server(make_token())

    result = await server.call_tool_for_caller("get_customer_account", {"cx_id": CUSTOMER})

    assert result["account_status"] == "active"


async def test_a_refusal_comes_back_as_data_the_agent_can_act_on() -> None:
    server = build_server(make_token())

    result = await server.call_tool_for_caller("get_customer_account", {"cx_id": "CX-9999"})

    assert result["error"]["code"] == "cross_account_denied"
    assert result["error"]["retryable"] is False
    assert "CX-9999" not in str(result)


@pytest.mark.parametrize("blocked", BLOCKED_TOOL_NAMES)
async def test_calling_a_blocked_tool_by_name_is_refused(blocked: str) -> None:
    server = build_server(make_token(scope="service:change service:cancel"))

    result = await server.call_tool_for_caller(blocked, {"cx_id": CUSTOMER})

    assert result["error"]["code"] == "tool_blocked"


async def test_calling_an_unknown_tool_is_refused() -> None:
    server = build_server(make_token())

    result = await server.call_tool_for_caller("exfiltrate_everything", {"cx_id": CUSTOMER})

    assert result["error"]["code"] == "unknown_tool"


async def test_every_call_gets_its_own_correlation_identifier() -> None:
    server = build_server(make_token())

    await server.call_tool_for_caller("get_customer_account", {"cx_id": CUSTOMER})
    await server.call_tool_for_caller("get_customer_account", {"cx_id": CUSTOMER})

    ids = [record.correlation_id for record in server.harness.audit.records]  # type: ignore[attr-defined]
    assert ids == ["corr-1", "corr-2"]


async def test_the_handlers_registered_with_the_sdk_are_the_two_we_expect() -> None:
    server = build_server(make_token())

    from mcp import types

    assert types.ListToolsRequest in server.server.request_handlers
    assert types.CallToolRequest in server.server.request_handlers


def test_the_stdio_token_source_reads_the_environment_on_every_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = EnvTokenSource()
    monkeypatch.setenv("TELECOM_MCP_ACCESS_TOKEN", "first")
    assert source.current_token() == "first"

    monkeypatch.setenv("TELECOM_MCP_ACCESS_TOKEN", "second")
    assert source.current_token() == "second"
