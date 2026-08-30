"""An audit trail that can be edited without trace is not an audit trail."""

import io
import json

from telecom_mcp.observability.redaction import Redactor, derive_pseudonym_key
from telecom_mcp.security.audit import (
    GENESIS_HASH,
    AuditLog,
    Decision,
    FileSink,
    MemorySink,
    Outcome,
    StdoutSink,
    verify_chain,
)
from tests.fakes import FrozenClock, SequentialIds


def _log(sink: object) -> AuditLog:
    return AuditLog(
        sink=sink,  # type: ignore[arg-type]
        clock=FrozenClock(),
        redactor=Redactor(derive_pseudonym_key("svc", "test-secret")),
        id_generator=SequentialIds("audit"),
    )


def test_an_accepted_call_is_recorded_with_the_minimum_fields() -> None:
    sink = MemorySink()

    record = _log(sink).record(
        tool="get_customer_account",
        decision=Decision.ACCEPTED,
        outcome=Outcome.SUCCESS,
        correlation_id="corr-1",
        authorization_result="allowed",
        cx_id="CX-1234",
        tenant_id="tenant-eu-1",
        role="customer",
        action_executed=True,
        case_id="case-1",
    )

    assert record.record_id == "audit-1"
    assert record.timestamp.endswith("Z")
    assert record.decision is Decision.ACCEPTED
    assert record.action_executed is True
    assert sink.records == [record]


def test_a_rejected_call_is_recorded_too() -> None:
    sink = MemorySink()

    _log(sink).record(
        tool="get_invoice_summary",
        decision=Decision.REJECTED,
        outcome=Outcome.NOT_EXECUTED,
        correlation_id="corr-2",
        authorization_result="denied: cross_account",
        failure_reason="cross_account_denied",
    )

    assert sink.records[0].decision is Decision.REJECTED
    assert sink.records[0].action_executed is False


def test_the_customer_identifier_is_pseudonymised_not_stored() -> None:
    sink = MemorySink()

    _log(sink).record(
        tool="t",
        decision=Decision.ACCEPTED,
        outcome=Outcome.SUCCESS,
        correlation_id="c",
        authorization_result="allowed",
        cx_id="CX-1234",
    )

    assert sink.records[0].cx_ref is not None
    assert sink.records[0].cx_ref.startswith("ref_")
    assert "CX-1234" not in sink.records[0].to_json()


def test_a_secret_in_the_arguments_never_reaches_the_audit_record() -> None:
    sink = MemorySink()

    _log(sink).record(
        tool="t",
        decision=Decision.REJECTED,
        outcome=Outcome.NOT_EXECUTED,
        correlation_id="c",
        authorization_result="denied",
        action_requested={"cx_id": "CX-1234", "passcode": "4821"},
    )

    assert "4821" not in sink.records[0].to_json()


def test_records_form_a_chain_from_the_genesis_hash() -> None:
    sink = MemorySink()
    log = _log(sink)

    for index in range(3):
        log.record(
            tool=f"tool-{index}",
            decision=Decision.ACCEPTED,
            outcome=Outcome.SUCCESS,
            correlation_id="c",
            authorization_result="allowed",
        )

    assert sink.records[0].previous_hash == GENESIS_HASH
    assert sink.records[1].previous_hash == sink.records[0].entry_hash
    assert sink.records[2].previous_hash == sink.records[1].entry_hash
    assert log.head == sink.records[2].entry_hash
    assert verify_chain(sink.records) is None


def test_editing_a_record_breaks_the_chain_at_that_point() -> None:
    from dataclasses import replace

    sink = MemorySink()
    log = _log(sink)
    for _ in range(3):
        log.record(
            tool="t",
            decision=Decision.ACCEPTED,
            outcome=Outcome.SUCCESS,
            correlation_id="c",
            authorization_result="allowed",
        )

    tampered = list(sink.records)
    tampered[1] = replace(tampered[1], outcome=Outcome.FAILURE)

    assert verify_chain(tampered) == 1


def test_deleting_a_record_breaks_the_chain() -> None:
    sink = MemorySink()
    log = _log(sink)
    for _ in range(3):
        log.record(
            tool="t",
            decision=Decision.ACCEPTED,
            outcome=Outcome.SUCCESS,
            correlation_id="c",
            authorization_result="allowed",
        )

    assert verify_chain([sink.records[0], sink.records[2]]) == 1


def test_an_empty_chain_is_valid() -> None:
    assert verify_chain([]) is None


def test_the_stdout_sink_writes_one_json_line_per_record() -> None:
    stream = io.StringIO()

    _log(StdoutSink(stream)).record(
        tool="t",
        decision=Decision.ACCEPTED,
        outcome=Outcome.SUCCESS,
        correlation_id="c",
        authorization_result="allowed",
    )

    (line,) = stream.getvalue().splitlines()
    assert json.loads(line)["tool"] == "t"


def test_the_file_sink_appends_rather_than_truncating(tmp_path: object) -> None:
    from pathlib import Path

    path = Path(str(tmp_path)) / "nested" / "audit.log"
    log = _log(FileSink(path))

    for _ in range(2):
        log.record(
            tool="t",
            decision=Decision.ACCEPTED,
            outcome=Outcome.SUCCESS,
            correlation_id="c",
            authorization_result="allowed",
        )

    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
