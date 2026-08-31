"""A decision must be unambiguous, and a refusal must be safe to write down."""

from __future__ import annotations

import pytest

from telecom_mcp.guardrails.decision import (
    ALLOWED,
    DEFAULT_PUBLIC_MESSAGE,
    GuardrailDecision,
    GuardrailStage,
    GuardrailViolation,
)


def test_an_allowed_decision_carries_no_violation() -> None:
    assert ALLOWED.allowed is True
    assert ALLOWED.violation is None
    assert GuardrailDecision.allow().allowed is True


def test_a_blocked_decision_names_the_rule_that_refused() -> None:
    decision = GuardrailDecision.block(
        GuardrailStage.ARGUMENT_SIZE, "max_bytes", "arguments were 4096 bytes, limit 2048"
    )
    assert decision.allowed is False
    assert decision.violation is not None
    assert decision.violation.stage is GuardrailStage.ARGUMENT_SIZE
    assert decision.violation.rule == "max_bytes"


def test_every_stage_shares_one_public_message_so_probing_learns_nothing() -> None:
    messages = {
        GuardrailDecision.block(stage, "rule", "reason").violation.public_message  # type: ignore[union-attr]
        for stage in GuardrailStage
    }
    assert messages == {DEFAULT_PUBLIC_MESSAGE}


def test_a_violation_renders_for_an_operator() -> None:
    violation = GuardrailViolation(
        stage=GuardrailStage.INJECTION, rule="instruction_override", reason="matched 1 pattern"
    )
    assert str(violation) == "injection/instruction_override: matched 1 pattern"


def test_decisions_are_frozen() -> None:
    with pytest.raises(AttributeError):
        ALLOWED.violation = None  # type: ignore[misc]
