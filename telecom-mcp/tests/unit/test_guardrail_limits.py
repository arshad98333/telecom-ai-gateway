"""Size and shape refusals, and the promise that a reason carries no input."""

from __future__ import annotations

from typing import Any

from telecom_mcp.guardrails.decision import GuardrailStage
from telecom_mcp.guardrails.limits import check_arguments
from telecom_mcp.guardrails.policy import GuardrailPolicy

POLICY = GuardrailPolicy(
    max_argument_bytes=512,
    max_argument_depth=3,
    max_string_length=64,
    max_array_items=4,
    max_object_keys=5,
)


def test_an_ordinary_argument_set_passes() -> None:
    assert check_arguments({"cx_id": "CX-1001", "reason": "billing query"}, POLICY).allowed


def test_an_oversized_payload_is_refused_by_bytes() -> None:
    # Every individual string and array is inside its own limit; the total is not.
    payload = {f"field{index}": ["y" * 60] * 4 for index in range(4)}
    decision = check_arguments(payload, POLICY)
    assert not decision.allowed
    assert decision.violation is not None
    assert decision.violation.stage is GuardrailStage.ARGUMENT_SIZE
    assert decision.violation.rule == "max_bytes"


def test_a_long_string_is_refused() -> None:
    decision = check_arguments({"note": "x" * 65}, POLICY)
    assert decision.violation is not None
    assert decision.violation.rule == "max_string_length"


def test_deep_nesting_is_refused() -> None:
    nested: Any = {"a": {"b": {"c": {"d": 1}}}}
    decision = check_arguments(nested, POLICY)
    assert decision.violation is not None
    assert decision.violation.rule == "max_depth"


def test_a_wide_array_is_refused() -> None:
    decision = check_arguments({"items": [1, 2, 3, 4, 5]}, POLICY)
    assert decision.violation is not None
    assert decision.violation.rule == "max_array_items"


def test_a_wide_object_is_refused() -> None:
    decision = check_arguments({str(index): index for index in range(6)}, POLICY)
    assert decision.violation is not None
    assert decision.violation.rule == "max_object_keys"


def test_a_control_character_is_refused_and_never_echoed() -> None:
    decision = check_arguments({"note": "ok\x00then"}, POLICY)
    assert decision.violation is not None
    assert decision.violation.rule == "control_characters"
    assert "\x00" not in decision.violation.reason
    assert "U+0000" in decision.violation.reason


def test_tab_and_newline_are_ordinary_text() -> None:
    assert check_arguments({"note": "line one\nline two\tindented"}, POLICY).allowed


def test_a_hostile_key_is_checked_as_well_as_a_value() -> None:
    decision = check_arguments({"k" * 65: "short"}, POLICY)
    assert decision.violation is not None
    assert decision.violation.rule == "max_string_length"


def test_a_disabled_policy_checks_nothing() -> None:
    assert check_arguments({"note": "x" * 10_000}, GuardrailPolicy.disabled()).allowed


def test_no_reason_ever_contains_the_offending_value() -> None:
    secret = "SUPERSECRETVALUE"
    decision = check_arguments({"note": secret * 10}, POLICY)
    assert decision.violation is not None
    assert secret not in decision.violation.reason
