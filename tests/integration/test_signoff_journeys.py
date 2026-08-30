"""The seven journeys the specification signs off against, in order, as one story."""

from typing import Any

from telecom_mcp.adapters.fake_backend import FailureInjection
from telecom_mcp.api.server import TelecomMCPServer
from telecom_mcp.security.audit import verify_chain
from tests.factory import CUSTOMER, build_test_application, make_token
from tests.fakes import SequentialIds


class StaticToken:
    def __init__(self, token: str) -> None:
        self._token = token

    def current_token(self) -> str:
        return self._token


TICKET: dict[str, Any] = {
    "cx_id": CUSTOMER,
    "category": "network",
    "subject": "Broadband keeps dropping",
    "description": "The connection drops every evening around eight.",
    "idempotency_key": "idem-journey-0001",
}


async def test_the_seven_signoff_journeys() -> None:
    # 1. Install the package fresh - proven by importing it and reading its version.
    import telecom_mcp

    assert telecom_mcp.__version__

    harness = build_test_application()
    server = TelecomMCPServer(
        harness.app, tokens=StaticToken(make_token()), id_generator=SequentialIds("corr")
    )

    # 2. List the tools.
    listed = {tool.name for tool in await server.list_tools_for_caller()}
    assert "get_customer_account" in listed
    assert "cancel_service" not in listed

    # 3. Call a permitted read tool.
    account = await server.call_tool_for_caller("get_customer_account", {"cx_id": CUSTOMER})
    assert account["account_status"] == "active"

    # 4. Refuse a lookup on another account.
    refused = await server.call_tool_for_caller("get_customer_account", {"cx_id": "CX-5555"})
    assert refused["error"]["code"] == "cross_account_denied"

    # 5. Create a support ticket.
    ticket = await server.call_tool_for_caller("create_support_ticket", TICKET)
    assert ticket["ticket_id"].startswith("TCK-")

    # 6. Retry after a timeout: the first attempt times out, the retry succeeds, and
    #    the customer is never told about it.
    harness.backend.failures.timeouts = 1
    after_timeout = await server.call_tool_for_caller("get_active_services", {"cx_id": CUSTOMER})
    assert "error" not in after_timeout
    assert after_timeout["total_count"] == 2

    # 7. Repeat a call and receive one ticket.
    repeated = await server.call_tool_for_caller("create_support_ticket", TICKET)
    assert repeated["ticket_id"] == ticket["ticket_id"]
    assert repeated["deduplicated"] is True
    assert len(harness.backend.tickets) == 1

    # The whole journey is auditable, in order, and the chain is intact.
    records = harness.audit.records
    assert [record.tool for record in records] == [
        "get_customer_account",
        "get_customer_account",
        "create_support_ticket",
        "get_active_services",
        "create_support_ticket",
    ]
    assert verify_chain(records) is None
    assert CUSTOMER not in "".join(record.to_json() for record in records)


async def test_a_case_survives_the_middleware_being_down_and_recovering() -> None:
    # The voice agent's worst day: the backend fails, the breaker opens, the customer
    # is told nothing happened, and the service recovers without a restart.
    harness = build_test_application(
        failures=FailureInjection(failures=99),
        TELECOM_MCP_BREAKER_FAILURE_THRESHOLD="2",
        TELECOM_MCP_BREAKER_RESET_TIMEOUT_S="30",
    )
    server = TelecomMCPServer(
        harness.app, tokens=StaticToken(make_token()), id_generator=SequentialIds("corr")
    )

    first = await server.call_tool_for_caller("get_customer_account", {"cx_id": CUSTOMER})
    assert first["error"]["message"].endswith("no action was completed.")

    second = await server.call_tool_for_caller("get_customer_account", {"cx_id": CUSTOMER})
    assert second["error"]["code"] in ("circuit_open", "backend_unavailable")

    harness.backend.failures.failures = 0
    harness.clock.advance(31)

    recovered = await server.call_tool_for_caller("get_customer_account", {"cx_id": CUSTOMER})
    assert recovered["account_status"] == "active"
