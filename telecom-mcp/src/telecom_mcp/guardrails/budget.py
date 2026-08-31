"""How many irreversible things one case may do before a human is required.

Rate limiting counts calls. This counts consequences. Reading an invoice a hundred
times is rude; raising six refund approvals on one support case is a pattern, and the
right answer to a pattern is a supervisor rather than a faster agent.

Only actions that change something are counted. A read leaves nothing behind, so
spending budget on it would mean an agent doing thorough research gets refused when it
finally tries to help.

The window rolls. A daily reset would let a loop spend the whole allowance at 23:59
and the whole allowance again at 00:01, which is exactly the shape of the incident
this is here to stop.
"""

from __future__ import annotations

import threading
from collections import OrderedDict, deque
from typing import Final

from telecom_mcp.domain.ports import Clock
from telecom_mcp.domain.tools import ToolSpec
from telecom_mcp.guardrails.decision import ALLOWED, GuardrailDecision, GuardrailStage
from telecom_mcp.guardrails.policy import GuardrailPolicy

#: How many cases are tracked before the coldest is forgotten. A forgotten case starts
#: with a full allowance, so the failure mode is one extra action, not a refusal.
MAX_TRACKED_CASES: Final = 10_000


def counts_against_budget(spec: ToolSpec) -> bool:
    """Whether this tool leaves something behind that someone would have to undo."""
    return spec.requires_idempotency_key or spec.requires_human_approval


class ActionBudget:
    """A rolling count of consequential actions, per case."""

    def __init__(
        self, policy: GuardrailPolicy, clock: Clock, *, max_tracked: int = MAX_TRACKED_CASES
    ) -> None:
        self._policy = policy
        self._clock = clock
        self._max_tracked = max_tracked
        self._lock = threading.Lock()
        self._spent: OrderedDict[str, deque[float]] = OrderedDict()

    @property
    def tracked(self) -> int:
        with self._lock:
            return len(self._spent)

    def check(self, spec: ToolSpec, *, case_id: str | None, subject: str) -> GuardrailDecision:
        """Reserve one action, or refuse. Reads never spend."""
        if not self._policy.enabled or not counts_against_budget(spec):
            return ALLOWED

        # A call with no case is still somebody's; the subject is the fallback scope so
        # the control cannot be avoided by omitting the case id.
        key = case_id or f"subject:{subject}"
        now = self._clock.monotonic()
        cutoff = now - self._policy.action_budget_window_s

        with self._lock:
            spent = self._spent.get(key)
            if spent is None:
                spent = deque()
                self._spent[key] = spent
                self._prune_table()
            else:
                self._spent.move_to_end(key)
            while spent and spent[0] < cutoff:
                spent.popleft()

            if len(spent) >= self._policy.write_actions_per_case:
                return GuardrailDecision.block(
                    GuardrailStage.ACTION_BUDGET,
                    "per_case",
                    f"{len(spent)} irreversible actions already taken in the last "
                    f"{self._policy.action_budget_window_s:g}s, limit "
                    f"{self._policy.write_actions_per_case}",
                )
            spent.append(now)
            return ALLOWED

    def release(self, spec: ToolSpec, *, case_id: str | None, subject: str) -> None:
        """Give back the most recent reservation when the action did not happen.

        Called when the backend refused, so a downstream outage does not quietly burn
        a customer's allowance for the rest of the window.
        """
        if not self._policy.enabled or not counts_against_budget(spec):
            return
        key = case_id or f"subject:{subject}"
        with self._lock:
            spent = self._spent.get(key)
            if spent:
                spent.pop()

    def _prune_table(self) -> None:
        """Caller holds the lock."""
        while len(self._spent) > self._max_tracked:
            self._spent.popitem(last=False)
