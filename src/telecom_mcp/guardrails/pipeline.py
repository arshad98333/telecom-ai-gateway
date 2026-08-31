"""The one object the executor talks to. Order is the design, not an accident.

Input checks run cheapest-first and volume-first, so a caller in a loop is refused by
the token bucket before anyone pays to serialize, walk, normalize and pattern-match
their arguments. The budget runs last of all, because it is the only input check that
records something: reserving an action for a call that a later check would have
refused would burn a customer's allowance on a request that never happened.

The pipeline does not raise, does not log and does not touch metrics. It returns a
decision. The executor owns the audit record and the counters, because the executor is
where every other outcome is already recorded, and a control that reports through two
different paths is a control whose numbers never quite add up.

The pipeline takes a ``GuardedCall`` rather than the kernel's ``AuthorizedCall`` so
that this package does not depend on the security package. The dependency would only
run one way today, but a guardrail importing a security control is how the two end up
being edited together, and they are meant to change at different speeds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from telecom_mcp.domain.ports import Clock
from telecom_mcp.domain.tools import ToolSpec
from telecom_mcp.guardrails.budget import ActionBudget
from telecom_mcp.guardrails.business import check_business_rules
from telecom_mcp.guardrails.decision import ALLOWED, GuardrailDecision
from telecom_mcp.guardrails.injection import check_for_injection
from telecom_mcp.guardrails.limits import check_arguments
from telecom_mcp.guardrails.output import check_output
from telecom_mcp.guardrails.policy import GuardrailPolicy
from telecom_mcp.guardrails.rate_limit import RateLimiter
from telecom_mcp.guardrails.unicode_safety import check_unicode_safety


@dataclass(frozen=True, slots=True)
class GuardedCall:
    """Everything a guardrail needs, and nothing it does not.

    In particular there is no token here. A guardrail has no business holding one, and
    a structure that cannot carry a credential cannot leak one.
    """

    spec: ToolSpec
    arguments: dict[str, Any]
    tenant_id: str
    subject: str
    case_id: str | None = None


class GuardrailPipeline:
    """Runs every guardrail, in order, and returns the first refusal."""

    def __init__(self, policy: GuardrailPolicy, clock: Clock) -> None:
        self._policy = policy
        self._clock = clock
        self._rate_limiter = RateLimiter(policy, clock)
        self._budget = ActionBudget(policy, clock)

    @property
    def policy(self) -> GuardrailPolicy:
        return self._policy

    def check_input(self, call: GuardedCall) -> GuardrailDecision:
        """Cheapest first, and the only stateful check last."""
        if not self._policy.enabled:
            return ALLOWED

        decision = self._rate_limiter.check(call.tenant_id, call.subject)
        if not decision.allowed:
            return decision

        for check in (check_arguments, check_unicode_safety, check_for_injection):
            decision = check(call.arguments, self._policy)
            if not decision.allowed:
                return decision

        decision = check_business_rules(call.arguments, self._policy, self._clock)
        if not decision.allowed:
            return decision

        return self._budget.check(call.spec, case_id=call.case_id, subject=call.subject)

    def release(self, call: GuardedCall) -> None:
        """Return a reserved action when it turned out not to happen."""
        self._budget.release(call.spec, case_id=call.case_id, subject=call.subject)

    def check_output(self, payload: dict[str, Any]) -> GuardrailDecision:
        return check_output(payload, self._policy)
