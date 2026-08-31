"""Consequences are counted, reads are not, and a failed action is refunded."""

from __future__ import annotations

from datetime import UTC, datetime

from telecom_mcp.domain.tools import get_spec
from telecom_mcp.guardrails.budget import ActionBudget, counts_against_budget
from telecom_mcp.guardrails.decision import GuardrailStage
from telecom_mcp.guardrails.policy import GuardrailPolicy

READ = get_spec("get_invoice_summary")
WRITE = get_spec("create_support_ticket")
POLICY = GuardrailPolicy(write_actions_per_case=2, action_budget_window_s=100.0)


class SteppableClock:
    def __init__(self) -> None:
        self.seconds = 0.0

    def now(self) -> datetime:
        return datetime(2026, 3, 1, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.seconds

    def advance(self, seconds: float) -> None:
        self.seconds += seconds


def test_reads_never_count() -> None:
    assert counts_against_budget(READ) is False
    budget = ActionBudget(POLICY, SteppableClock())
    for _ in range(50):
        assert budget.check(READ, case_id="case-1", subject="cx-1").allowed


def test_writes_count_and_run_out() -> None:
    assert counts_against_budget(WRITE) is True
    budget = ActionBudget(POLICY, SteppableClock())
    assert budget.check(WRITE, case_id="case-1", subject="cx-1").allowed
    assert budget.check(WRITE, case_id="case-1", subject="cx-1").allowed
    decision = budget.check(WRITE, case_id="case-1", subject="cx-1")
    assert decision.violation is not None
    assert decision.violation.stage is GuardrailStage.ACTION_BUDGET


def test_the_window_rolls_rather_than_resetting() -> None:
    clock = SteppableClock()
    budget = ActionBudget(POLICY, clock)
    budget.check(WRITE, case_id="case-1", subject="cx-1")
    clock.advance(60)
    budget.check(WRITE, case_id="case-1", subject="cx-1")
    assert not budget.check(WRITE, case_id="case-1", subject="cx-1").allowed
    clock.advance(41)  # the first action is now outside the 100s window
    assert budget.check(WRITE, case_id="case-1", subject="cx-1").allowed


def test_cases_have_separate_allowances() -> None:
    budget = ActionBudget(POLICY, SteppableClock())
    for _ in range(2):
        budget.check(WRITE, case_id="case-1", subject="cx-1")
    assert not budget.check(WRITE, case_id="case-1", subject="cx-1").allowed
    assert budget.check(WRITE, case_id="case-2", subject="cx-1").allowed


def test_omitting_the_case_id_does_not_avoid_the_control() -> None:
    budget = ActionBudget(POLICY, SteppableClock())
    for _ in range(2):
        budget.check(WRITE, case_id=None, subject="cx-1")
    assert not budget.check(WRITE, case_id=None, subject="cx-1").allowed


def test_a_failed_action_is_given_back() -> None:
    budget = ActionBudget(POLICY, SteppableClock())
    budget.check(WRITE, case_id="case-1", subject="cx-1")
    budget.check(WRITE, case_id="case-1", subject="cx-1")
    budget.release(WRITE, case_id="case-1", subject="cx-1")
    assert budget.check(WRITE, case_id="case-1", subject="cx-1").allowed


def test_releasing_a_read_is_a_no_op() -> None:
    budget = ActionBudget(POLICY, SteppableClock())
    budget.release(READ, case_id="case-1", subject="cx-1")
    assert budget.tracked == 0


def test_the_case_table_is_bounded() -> None:
    budget = ActionBudget(POLICY, SteppableClock(), max_tracked=5)
    for index in range(40):
        budget.check(WRITE, case_id=f"case-{index}", subject="cx-1")
    assert budget.tracked <= 5


def test_a_disabled_policy_counts_nothing() -> None:
    budget = ActionBudget(GuardrailPolicy.disabled(), SteppableClock())
    for _ in range(20):
        assert budget.check(WRITE, case_id="case-1", subject="cx-1").allowed
