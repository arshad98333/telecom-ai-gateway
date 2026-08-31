"""Text that renders as one thing and contains another is refused, not corrected."""

from __future__ import annotations

import pytest

from telecom_mcp.guardrails.policy import GuardrailPolicy
from telecom_mcp.guardrails.unicode_safety import check_unicode_safety

POLICY = GuardrailPolicy()


@pytest.mark.parametrize(
    "char",
    ["​", "‎", "‮", "﻿", "­", "⁩"],
)
def test_invisible_characters_are_refused(char: str) -> None:
    decision = check_unicode_safety({"description": f"refund{char} please"}, POLICY)
    assert decision.violation is not None
    assert decision.violation.rule == "invisible_characters"


def test_the_offending_character_is_reported_as_a_code_point_not_echoed() -> None:
    decision = check_unicode_safety({"note": "a‮b"}, POLICY)
    assert decision.violation is not None
    assert "‮" not in decision.violation.reason
    assert "U+202E" in decision.violation.reason


def test_a_cyrillic_lookalike_inside_latin_text_is_refused() -> None:
    # The second character is CYRILLIC SMALL LETTER A, not LATIN SMALL LETTER A.
    decision = check_unicode_safety({"cx_id": "pаyments"}, POLICY)
    assert decision.violation is not None
    assert decision.violation.rule == "mixed_script"
    assert "cyrillic" in decision.violation.reason


def test_a_single_script_passes_whichever_it_is() -> None:
    assert check_unicode_safety({"note": "ordinary latin text"}, POLICY).allowed
    assert check_unicode_safety({"note": "привет"}, POLICY).allowed


def test_digits_punctuation_and_spacing_do_not_count_as_a_script() -> None:
    assert check_unicode_safety({"note": "Order 12-345, due 01/02."}, POLICY).allowed


def test_a_stack_of_combining_marks_is_refused() -> None:
    decision = check_unicode_safety({"note": "e" + "́" * 4}, POLICY)
    assert decision.violation is not None
    assert decision.violation.rule == "combining_marks"


def test_an_ordinary_accent_passes() -> None:
    assert check_unicode_safety({"note": "café"}, POLICY).allowed


def test_nested_fields_are_named() -> None:
    decision = check_unicode_safety({"ticket": {"body": "x​y"}}, POLICY)
    assert decision.violation is not None
    assert "ticket.body" in decision.violation.reason


def test_a_disabled_policy_checks_nothing() -> None:
    assert check_unicode_safety({"note": "x‮y"}, GuardrailPolicy.disabled()).allowed
