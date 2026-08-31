"""A guardrail refusal must be auditable in detail and opaque to the caller."""

from __future__ import annotations

from telecom_mcp.domain.errors import ErrorCode, GuardrailBlockedError


def test_the_envelope_names_no_stage() -> None:
    error = GuardrailBlockedError(
        "arguments were 9001 bytes, limit 2048",
        operation="get_invoice_summary",
        stage="argument_size",
        rule="max_bytes",
    )
    envelope = error.envelope("corr-1")
    assert envelope.code is ErrorCode.GUARDRAIL_BLOCKED
    assert envelope.message == "The request was refused by a safety control."
    assert "argument_size" not in envelope.message
    assert "9001" not in envelope.message
    assert envelope.retryable is False


def test_the_stage_and_rule_survive_for_the_audit_trail() -> None:
    error = GuardrailBlockedError(operation="create_support_ticket", stage="injection", rule="x")
    assert error.stage == "injection"
    assert error.rule == "x"
    assert error.operation == "create_support_ticket"


def test_an_unqualified_refusal_still_has_a_public_message() -> None:
    assert str(GuardrailBlockedError()) == "The request was refused by a safety control."
