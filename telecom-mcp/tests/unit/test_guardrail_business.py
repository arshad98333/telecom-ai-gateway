"""The rules a frozen schema cannot hold: anything needing a clock or a ceiling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from telecom_mcp.guardrails.business import check_business_rules
from telecom_mcp.guardrails.policy import GuardrailPolicy

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
POLICY = GuardrailPolicy()


class FrozenClock:
    def now(self) -> datetime:
        return NOW

    def monotonic(self) -> float:
        return 0.0


CLOCK = FrozenClock()


def test_a_callback_next_week_is_fine() -> None:
    arguments = {"preferred_date": NOW + timedelta(days=7)}
    assert check_business_rules(arguments, POLICY, CLOCK).allowed


def test_a_callback_in_the_past_is_refused() -> None:
    decision = check_business_rules({"preferred_date": NOW - timedelta(minutes=1)}, POLICY, CLOCK)
    assert decision.violation is not None
    assert decision.violation.rule == "callback_in_the_past"


def test_a_callback_beyond_the_horizon_is_refused() -> None:
    decision = check_business_rules({"preferred_date": NOW + timedelta(days=400)}, POLICY, CLOCK)
    assert decision.violation is not None
    assert decision.violation.rule == "callback_beyond_horizon"


def test_an_iso_string_is_accepted_the_same_as_a_datetime() -> None:
    arguments = {"preferred_date": (NOW + timedelta(days=1)).isoformat().replace("+00:00", "Z")}
    assert check_business_rules(arguments, POLICY, CLOCK).allowed


def test_an_unreadable_date_is_refused_rather_than_ignored() -> None:
    decision = check_business_rules({"preferred_date": "next tuesday"}, POLICY, CLOCK)
    assert decision.violation is not None
    assert decision.violation.rule == "callback_date_unreadable"


def test_an_amount_within_the_ceiling_passes() -> None:
    assert check_business_rules({"amount": Decimal("4.99")}, POLICY, CLOCK).allowed


def test_an_amount_above_the_ceiling_is_refused() -> None:
    tight = GuardrailPolicy(refund_ceiling=Decimal("2.00"))
    decision = check_business_rules({"amount": "3.50"}, tight, CLOCK)
    assert decision.violation is not None
    assert decision.violation.rule == "refund_ceiling"
    assert "2.00" in decision.violation.reason


def test_tools_without_these_fields_are_untouched() -> None:
    assert check_business_rules({"cx_id": "CX-1001", "limit": 5}, POLICY, CLOCK).allowed


def test_a_disabled_policy_checks_nothing() -> None:
    off = GuardrailPolicy.disabled()
    assert check_business_rules({"amount": "9999.00"}, off, CLOCK).allowed
