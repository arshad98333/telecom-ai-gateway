"""The single path a tool call takes, exercised end to end against the fake backend."""

from typing import Any

import pytest

from telecom_mcp.adapters.fake_backend import FailureInjection
from telecom_mcp.api.executor import IN_PROGRESS_MESSAGE, visible_tools
from telecom_mcp.domain.errors import ErrorCode
from telecom_mcp.domain.permissions import Scope
from telecom_mcp.domain.tools import TOOL_SPECS
from telecom_mcp.security.audit import Decision, Outcome
from telecom_mcp.security.identity import ToolRequest
from tests.factory import CUSTOMER, build_test_application, make_token

TICKET_ARGS: dict[str, Any] = {
    "cx_id": CUSTOMER,
    "category": "billing",
    "subject": "Charged twice",
    "description": "My August bill shows the same charge twice.",
    "idempotency_key": "idem-0000-0001",
}


def request(tool: str, arguments: dict[str, Any], **kwargs: Any) -> ToolRequest:
    return ToolRequest(
        tool_name=tool,
        arguments=arguments,
        token=kwargs.pop("token", make_token()),
        correlation_id=kwargs.pop("correlation_id", "corr-1"),
        case_id=kwargs.pop("case_id", "case-1"),
        **kwargs,
    )


async def test_a_permitted_read_returns_the_customers_own_data() -> None:
    harness = build_test_application()

    result = await harness.executor.execute(request("get_customer_account", {"cx_id": CUSTOMER}))

    assert result.ok
    assert result.output["display_name"] == "J. Okonkwo"


async def test_a_successful_call_writes_exactly_one_audit_record() -> None:
    harness = build_test_application()

    await harness.executor.execute(request("get_customer_account", {"cx_id": CUSTOMER}))

    (record,) = harness.audit.records
    assert record.decision is Decision.ACCEPTED
    assert record.outcome is Outcome.SUCCESS
    assert record.action_executed is True
    assert record.case_id == "case-1"


async def test_a_denied_call_also_writes_exactly_one_audit_record() -> None:
    harness = build_test_application()

    result = await harness.executor.execute(request("get_customer_account", {"cx_id": "CX-9999"}))

    (record,) = harness.audit.records
    assert record.decision is Decision.REJECTED
    assert record.outcome is Outcome.NOT_EXECUTED
    assert record.extra["stage"] == "account_ownership"
    assert result.error is not None
    assert result.error.code is ErrorCode.CROSS_ACCOUNT_DENIED


async def test_the_executor_never_raises_at_its_boundary() -> None:
    harness = build_test_application()

    result = await harness.executor.execute(request("no_such_tool", {"cx_id": CUSTOMER}))

    assert result.error is not None
    assert result.error.code is ErrorCode.UNKNOWN_TOOL


async def test_a_write_creates_one_record_and_returns_a_ticket() -> None:
    harness = build_test_application()

    result = await harness.executor.execute(request("create_support_ticket", TICKET_ARGS))

    assert result.ok
    assert result.output["ticket_id"].startswith("TCK-")
    assert len(harness.backend.tickets) == 1


async def test_repeating_a_write_with_the_same_key_produces_exactly_one_ticket() -> None:
    # The check the reviewer asks about: a refund, or a ticket, called twice.
    harness = build_test_application()

    first = await harness.executor.execute(request("create_support_ticket", TICKET_ARGS))
    second = await harness.executor.execute(request("create_support_ticket", TICKET_ARGS))

    assert len(harness.backend.tickets) == 1
    assert second.output["ticket_id"] == first.output["ticket_id"]
    assert second.deduplicated is True
    assert second.output["deduplicated"] is True


async def test_a_replay_is_audited_as_deduplicated_not_as_a_second_execution() -> None:
    harness = build_test_application()

    await harness.executor.execute(request("create_support_ticket", TICKET_ARGS))
    await harness.executor.execute(request("create_support_ticket", TICKET_ARGS))

    assert [record.outcome for record in harness.audit.records] == [
        Outcome.SUCCESS,
        Outcome.DEDUPLICATED,
    ]
    assert harness.audit.records[1].action_executed is False


async def test_reusing_a_key_with_different_input_is_refused_rather_than_replayed() -> None:
    harness = build_test_application()
    await harness.executor.execute(request("create_support_ticket", TICKET_ARGS))

    changed = dict(TICKET_ARGS, subject="Something completely different")
    result = await harness.executor.execute(request("create_support_ticket", changed))

    assert result.error is not None
    assert result.error.code is ErrorCode.IDEMPOTENCY_KEY_REUSED


async def test_a_repeat_while_the_first_call_is_running_is_told_to_wait() -> None:
    harness = build_test_application()
    key = "idem-0000-0009"
    from telecom_mcp.adapters.idempotency import fingerprint, scoped_key
    from telecom_mcp.domain.schemas import CreateSupportTicketInput

    validated = CreateSupportTicketInput(**dict(TICKET_ARGS, idempotency_key=key)).model_dump(
        mode="json"
    )
    await harness.app.idempotency.reserve(
        scoped_key("tenant-eu-1", CUSTOMER, "create_support_ticket", key),
        fingerprint(
            "tenant-eu-1",
            CUSTOMER,
            "create_support_ticket",
            {name: value for name, value in validated.items() if name != "idempotency_key"},
        ),
    )

    result = await harness.executor.execute(
        request("create_support_ticket", dict(TICKET_ARGS, idempotency_key=key))
    )

    assert result.error is not None
    assert result.error.message == IN_PROGRESS_MESSAGE
    assert result.error.retryable is True
    assert harness.backend.tickets == {}


async def test_a_failed_write_frees_its_key_so_a_genuine_retry_can_proceed() -> None:
    harness = build_test_application(failures=FailureInjection(failures=99))

    failed = await harness.executor.execute(request("create_support_ticket", TICKET_ARGS))
    assert failed.error is not None

    harness.backend.failures.failures = 0
    retried = await harness.executor.execute(request("create_support_ticket", TICKET_ARGS))

    assert retried.ok


async def test_a_transient_backend_failure_is_retried_and_the_customer_never_sees_it() -> None:
    harness = build_test_application(failures=FailureInjection(timeouts=1))

    result = await harness.executor.execute(request("get_customer_account", {"cx_id": CUSTOMER}))

    assert result.ok


async def test_a_persistent_backend_failure_returns_the_safe_message() -> None:
    harness = build_test_application(failures=FailureInjection(failures=99))

    result = await harness.executor.execute(request("get_customer_account", {"cx_id": CUSTOMER}))

    assert result.error is not None
    assert result.error.message == (
        "The requested service is temporarily unavailable; no action was completed."
    )
    assert result.error.retryable is True
    assert harness.audit.records[0].outcome is Outcome.FAILURE


async def test_a_refund_request_is_never_retried_automatically() -> None:
    harness = build_test_application(failures=FailureInjection(failures=99))
    calls_before = len(harness.backend.failures.calls)

    await harness.executor.execute(
        request(
            "request_refund_approval",
            {
                "cx_id": CUSTOMER,
                "invoice_id": "INV-2026-08",
                "amount": "4.50",
                "currency": "GBP",
                "reason": "duplicate_charge",
                "justification": "Charged twice in August.",
                "idempotency_key": "idem-0000-0002",
            },
        )
    )

    assert len(harness.backend.failures.calls) - calls_before == 1


async def test_a_refund_request_is_audited_as_pending_supervisor_approval() -> None:
    harness = build_test_application()

    result = await harness.executor.execute(
        request(
            "request_refund_approval",
            {
                "cx_id": CUSTOMER,
                "invoice_id": "INV-2026-08",
                "amount": "4.50",
                "currency": "GBP",
                "reason": "duplicate_charge",
                "justification": "Charged twice in August.",
                "idempotency_key": "idem-0000-0003",
            },
        )
    )

    assert result.output["money_moved"] is False
    assert harness.audit.records[0].approval_result == "pending_supervisor"


async def test_metrics_count_the_call_without_ever_labelling_a_customer() -> None:
    harness = build_test_application()

    await harness.executor.execute(request("get_customer_account", {"cx_id": CUSTOMER}))

    snapshot = harness.app.metrics.snapshot()["tool_calls_total"]
    labels = {name for key in snapshot for name, _ in key}
    assert labels <= {"tool", "outcome", "code"}


async def test_a_correlation_identifier_reaches_the_audit_record() -> None:
    harness = build_test_application()

    await harness.executor.execute(
        request("get_customer_account", {"cx_id": CUSTOMER}, correlation_id="corr-abc")
    )

    assert harness.audit.records[0].correlation_id == "corr-abc"


async def test_the_customer_identifier_is_never_written_to_the_audit_trail_in_the_clear() -> None:
    harness = build_test_application()

    await harness.executor.execute(request("create_support_ticket", TICKET_ARGS))

    assert CUSTOMER not in harness.audit.records[0].to_json()


def test_tool_visibility_is_filtered_by_the_identitys_scopes() -> None:
    read_only = frozenset({Scope.ACCOUNT_READ, Scope.BILLING_READ})

    visible = {spec.name for spec in visible_tools(read_only, TOOL_SPECS)}

    assert visible == {"get_customer_account", "get_invoice_summary"}


def test_an_identity_with_no_scopes_sees_no_tools_at_all() -> None:
    assert visible_tools(frozenset(), TOOL_SPECS) == []


@pytest.mark.parametrize(
    "tool",
    ["get_customer_account", "get_active_services", "get_order_status", "get_invoice_summary"],
)
async def test_every_read_tool_works_for_the_owning_customer(tool: str) -> None:
    harness = build_test_application()

    result = await harness.executor.execute(request(tool, {"cx_id": CUSTOMER}))

    assert result.ok, result.error
