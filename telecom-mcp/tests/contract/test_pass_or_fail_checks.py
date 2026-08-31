"""The eight pass-or-fail checks from the build specification, by their given names.

These are the tests a reviewer looks for by name. Each one states its target in the
docstring and asserts it, so "did it pass" is answered by running the suite rather
than by reading a report.
"""

from typing import Any

import pytest

from telecom_mcp.api.server import TelecomMCPServer
from telecom_mcp.domain.schemas import MAX_PAGE_SIZE
from telecom_mcp.domain.tools import BLOCKED_TOOL_NAMES, TOOL_SPECS
from telecom_mcp.security.identity import ToolRequest
from tests.factory import CUSTOMER, build_test_application, make_token
from tests.fakes import SequentialIds


class StaticToken:
    def __init__(self, token: str) -> None:
        self._token = token

    def current_token(self) -> str:
        return self._token


def request(tool: str, arguments: dict[str, Any], token: str | None = None) -> ToolRequest:
    return ToolRequest(
        tool_name=tool,
        arguments=arguments,
        token=token or make_token(),
        correlation_id="corr-1",
        case_id="case-1",
    )


TICKET_ARGS: dict[str, Any] = {
    "cx_id": CUSTOMER,
    "category": "billing",
    "subject": "Charged twice",
    "description": "My August bill shows the same charge twice.",
    "idempotency_key": "idem-check-0001",
}


async def test_tool_listing() -> None:
    """Target: only authorized tools are exposed."""
    harness = build_test_application()
    server = TelecomMCPServer(
        harness.app,
        tokens=StaticToken(make_token(scope="account:read ticket:write")),
        id_generator=SequentialIds("corr"),
    )

    listed = {tool.name for tool in await server.list_tools_for_caller()}

    assert listed == {"get_customer_account", "create_support_ticket"}
    assert not listed & set(BLOCKED_TOOL_NAMES)


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("get_customer_account", {}),
        ("get_customer_account", {"cx_id": ""}),
        ("get_customer_account", {"cx_id": "CX-1234", "extra": 1}),
        ("get_active_services", {"cx_id": CUSTOMER, "limit": MAX_PAGE_SIZE + 1}),
        ("get_active_services", {"cx_id": CUSTOMER, "limit": 0}),
        ("get_order_status", {"cx_id": CUSTOMER, "order_id": "bad;id"}),
        ("create_support_ticket", {"cx_id": CUSTOMER}),
        ("create_support_ticket", dict(TICKET_ARGS, category="whatever")),
        ("create_support_ticket", dict(TICKET_ARGS, idempotency_key="short")),
        ("create_support_ticket", dict(TICKET_ARGS, description="x" * 2001)),
        ("schedule_callback", {"cx_id": CUSTOMER, "window": "midnight"}),
        (
            "request_refund_approval",
            {
                "cx_id": CUSTOMER,
                "invoice_id": "INV-1",
                "amount": "5.01",
                "currency": "GBP",
                "reason": "goodwill",
                "justification": "j",
                "idempotency_key": "idem-check-0002",
            },
        ),
        ("unknown_tool", {"cx_id": CUSTOMER}),
    ],
)
async def test_invalid_inputs_rejected(tool: str, arguments: dict[str, Any]) -> None:
    """Target: 100% of invalid inputs are rejected."""
    harness = build_test_application()

    result = await harness.executor.execute(request(tool, arguments))

    assert not result.ok
    assert harness.audit.records[0].action_executed is False


async def test_cross_account_access_denied() -> None:
    """Target: zero unauthorized calls succeed."""
    harness = build_test_application()

    attempts = [
        await harness.executor.execute(request(spec.name, {"cx_id": "CX-5555"}))
        for spec in TOOL_SPECS
        if spec.risk.value == "read_only"
    ]

    assert attempts, "no read-only tools were exercised"
    assert all(not result.ok for result in attempts)
    assert all(
        result.error is not None and result.error.code.value == "cross_account_denied"
        for result in attempts
    )
    assert all(record.action_executed is False for record in harness.audit.records)


async def test_sensitive_data_redaction() -> None:
    """Target: 100% of sensitive fields are redacted in telemetry."""
    harness = build_test_application()

    await harness.executor.execute(
        request(
            "create_support_ticket",
            dict(
                TICKET_ARGS,
                description="My passcode is 4821 and my card is 4111 1111 1111 1111.",
            ),
        )
    )

    serialised = "".join(record.to_json() for record in harness.audit.records)
    assert "4821" not in serialised
    assert "4111 1111 1111 1111" not in serialised
    assert CUSTOMER not in serialised


async def test_idempotent_write() -> None:
    """Target: zero duplicate tickets."""
    harness = build_test_application()

    results = [
        await harness.executor.execute(request("create_support_ticket", TICKET_ARGS))
        for _ in range(5)
    ]

    assert len(harness.backend.tickets) == 1
    assert len({result.output["ticket_id"] for result in results}) == 1
    assert [result.deduplicated for result in results] == [False, True, True, True, True]


async def test_timeout_and_failure() -> None:
    """Target: a safe error is returned within ten seconds."""
    from telecom_mcp.adapters.fake_backend import FailureInjection

    harness = build_test_application(failures=FailureInjection(failures=99))
    started = harness.clock.monotonic()

    result = await harness.executor.execute(request("get_customer_account", {"cx_id": CUSTOMER}))

    assert not result.ok
    assert result.error is not None
    assert result.error.message == (
        "The requested service is temporarily unavailable; no action was completed."
    )
    assert harness.clock.monotonic() - started <= 10.0


async def test_backward_compatibility() -> None:
    """Target: 100% of supported version 1 calls keep working."""
    harness = build_test_application()

    accepted = await harness.executor.execute(
        ToolRequest(
            tool_name="get_customer_account",
            arguments={"cx_id": CUSTOMER},
            token=make_token(),
            correlation_id="corr-1",
            contract_version="1",
        )
    )
    future = await harness.executor.execute(
        ToolRequest(
            tool_name="get_customer_account",
            arguments={"cx_id": CUSTOMER},
            token=make_token(),
            correlation_id="corr-2",
            contract_version="2",
        )
    )

    assert accepted.ok
    # An unsupported version is refused clearly rather than served a guess.
    assert future.error is not None
    assert future.error.code.value == "unsupported_contract_version"


def test_clean_install() -> None:
    """Target: a clean environment installs and imports successfully.

    The full check builds a wheel and installs it into an empty virtual environment;
    that runs in CI, where a network is available. Here we assert the two things that
    make it possible: the package metadata resolves, and the data files the package
    needs at runtime are inside the package rather than beside it.
    """
    import telecom_mcp
    from telecom_mcp.adapters.fake_backend import load_seed

    assert telecom_mcp.__version__ != "0.0.0+unknown"
    assert "tenants" in load_seed()
