"""The last line of defence: refuse, never scrub, and do not cry wolf."""

from __future__ import annotations

import pytest

from telecom_mcp.guardrails.decision import GuardrailStage
from telecom_mcp.guardrails.output import check_output
from telecom_mcp.guardrails.policy import GuardrailPolicy

POLICY = GuardrailPolicy()


def test_an_ordinary_response_passes() -> None:
    payload = {"invoices": [{"invoice_id": "INV-99", "amount": "42.10", "currency": "GBP"}]}
    assert check_output(payload, POLICY).allowed


@pytest.mark.parametrize(
    ("value", "rule"),
    [
        ("Bearer abcdefghijklmnopqrstuvwxyz012345", "bearer_token"),
        ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p", "json_web_token"),
        ("-----BEGIN RSA PRIVATE KEY-----", "private_key_block"),
        ("InstrumentationKey=0123456789abcdef0123", "azure_connection_string"),
        ("AKIAIOSFODNN7EXAMPLE", "aws_access_key"),
        ("client_secret: hunter2hunter2", "generic_api_key"),
    ],
)
def test_a_secret_shape_refuses_the_whole_response(value: str, rule: str) -> None:
    decision = check_output({"note": value}, POLICY)
    assert decision.violation is not None, value
    assert decision.violation.stage is GuardrailStage.OUTPUT_SECRET
    assert decision.violation.rule == rule


def test_the_leaked_value_never_appears_in_the_reason() -> None:
    decision = check_output({"note": "AKIAIOSFODNN7EXAMPLE"}, POLICY)
    assert decision.violation is not None
    assert "AKIAIOSFODNN7EXAMPLE" not in decision.violation.reason


def test_a_luhn_valid_card_number_is_refused() -> None:
    decision = check_output({"note": "paid with 4111 1111 1111 1111"}, POLICY)
    assert decision.violation is not None
    assert decision.violation.rule == "card_number"


def test_a_long_reference_that_is_not_luhn_valid_passes() -> None:
    assert check_output({"order_id": "4111111111111112"}, POLICY).allowed
    assert check_output({"msisdn": "447700900123"}, POLICY).allowed


def test_an_oversized_response_is_refused_before_the_scan() -> None:
    tight = GuardrailPolicy(max_output_bytes=64)
    decision = check_output({"note": "x" * 500}, tight)
    assert decision.violation is not None
    assert decision.violation.stage is GuardrailStage.OUTPUT_SIZE


def test_the_secret_scan_can_be_switched_off_without_losing_the_size_cap() -> None:
    policy = GuardrailPolicy(output_secret_scan=False, max_output_bytes=64)
    assert check_output({"note": "AKIAIOSFODNN7EXAMPLE"}, policy).allowed
    assert not check_output({"note": "x" * 500}, policy).allowed


def test_a_disabled_policy_checks_nothing() -> None:
    assert check_output({"note": "-----BEGIN PRIVATE KEY-----"}, GuardrailPolicy.disabled()).allowed
