from __future__ import annotations

import json

import pytest

from telecom_mcp_client.cli import _build_parser, _run
from tests.conftest import FakeServer, ok_result, refused_result


def _last_json_block(printed: str) -> str:
    """`_run` logs a handshake line via structlog to stdout before printing its real
    JSON output; find the line that starts a top-level `[` or `{` and take everything
    from there, so structlog's own noise doesn't confuse the JSON parser."""
    lines = printed.splitlines()
    for index, line in enumerate(lines):
        if line in ("[", "{"):
            return "\n".join(lines[index:])
    raise AssertionError(f"no JSON block found in:\n{printed}")


def _parse(fake_server: FakeServer, *args: str) -> object:
    parser = _build_parser()
    return parser.parse_args(["--base-url", "http://testserver", *args])


@pytest.fixture(autouse=True)
def _patch_transport(fake_server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
    """`_run` builds its own `MCPClient` with no way to inject a transport from the
    CLI args, so patch `httpx.AsyncClient`'s default transport via env — simplest is
    to monkeypatch `MCPClient.__init__` defaults isn't available either, so instead we
    monkeypatch the module-level `MCPClient` the CLI imports to a thin wrapper that
    forces our fake transport in."""
    import telecom_mcp_client.cli as cli_module
    from telecom_mcp_client.client import MCPClient

    real_init = MCPClient.__init__

    def patched_init(self: MCPClient, **kwargs: object) -> None:
        kwargs["transport"] = fake_server.transport()
        real_init(self, **kwargs)

    monkeypatch.setattr(MCPClient, "__init__", patched_init)
    monkeypatch.setattr(cli_module, "MCPClient", MCPClient)


async def test_cli_list_tools(fake_server: FakeServer, capsys: pytest.CaptureFixture[str]) -> None:
    fake_server.list_tools_result = [{"name": "get_customer_account"}]
    args = _parse(fake_server, "--token", "t", "list-tools")
    code = await _run(args)
    assert code == 0
    out = json.loads(_last_json_block(capsys.readouterr().out))
    assert out == [{"name": "get_customer_account"}]


async def test_cli_call_success(
    fake_server: FakeServer, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_server.tool_call_queue.append(lambda body: ok_result(body))
    args = _parse(
        fake_server, "--token", "t", "call", "get_customer_account", "--json", '{"cx_id": "CX-1"}'
    )
    code = await _run(args)
    assert code == 0
    payload = json.loads(_last_json_block(capsys.readouterr().out))
    assert payload["content"]


async def test_cli_call_refused(
    fake_server: FakeServer, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_server.tool_call_queue.append(lambda body: refused_result(body))
    args = _parse(
        fake_server, "--token", "t", "call", "get_customer_account", "--json", '{"cx_id": "CX-1"}'
    )
    code = await _run(args)
    assert code == 1
    err = capsys.readouterr().err
    assert "refused" in err


async def test_cli_call_bad_json(
    fake_server: FakeServer, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _parse(
        fake_server, "--token", "t", "call", "get_customer_account", "--json", "{not json"
    )
    code = await _run(args)
    assert code == 2
    assert "not valid JSON" in capsys.readouterr().err


async def test_cli_initialize_failure(
    fake_server: FakeServer, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_server.initialize_ok = False
    args = _parse(fake_server, "--token", "t", "list-tools")
    code = await _run(args)
    assert code == 2
    assert "could not connect" in capsys.readouterr().err
