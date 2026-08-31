"""Sanity rules that the frozen contract cannot express.

The tool schemas already refuse the wrong type, the wrong shape and the wrong range.
What they cannot do is change: they are frozen for contract version 1, and they have
no clock, so "not in the past" and "not two years out" are not expressible there.

That leaves a small set of rules that are genuinely operational rather than
contractual, and those belong here where an environment can tighten them. A refund
ceiling is the clearest example: the contract caps an autonomous refund at 5.00 for
the life of v1, and a business that wants 2.00 in production should not have to cut a
new contract version to get it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from telecom_mcp.domain.ports import Clock
from telecom_mcp.guardrails.decision import ALLOWED, GuardrailDecision, GuardrailStage
from telecom_mcp.guardrails.policy import GuardrailPolicy


def check_business_rules(
    arguments: dict[str, Any], policy: GuardrailPolicy, clock: Clock
) -> GuardrailDecision:
    """Apply the rules that need a clock or an environment-specific ceiling."""
    if not policy.enabled:
        return ALLOWED

    decision = _check_callback_date(arguments, policy, clock)
    if not decision.allowed:
        return decision
    return _check_refund_amount(arguments, policy)


def _check_callback_date(
    arguments: dict[str, Any], policy: GuardrailPolicy, clock: Clock
) -> GuardrailDecision:
    raw = arguments.get("preferred_date")
    if raw is None:
        return ALLOWED
    when = _as_datetime(raw)
    if when is None:
        # The schema already guarantees a timezone-aware datetime; anything else here
        # means the caller bypassed it, which is a refusal rather than a shrug.
        return GuardrailDecision.block(
            GuardrailStage.ARGUMENT_SHAPE,
            "callback_date_unreadable",
            "preferred_date is not a timezone-aware datetime",
        )

    now = clock.now()
    if when < now:
        return GuardrailDecision.block(
            GuardrailStage.ARGUMENT_SHAPE,
            "callback_in_the_past",
            "preferred_date is before the current time",
        )
    horizon = now + timedelta(days=policy.callback_horizon_days)
    if when > horizon:
        return GuardrailDecision.block(
            GuardrailStage.ARGUMENT_SHAPE,
            "callback_beyond_horizon",
            f"preferred_date is more than {policy.callback_horizon_days} days ahead",
        )
    return ALLOWED


def _check_refund_amount(arguments: dict[str, Any], policy: GuardrailPolicy) -> GuardrailDecision:
    raw = arguments.get("amount")
    if raw is None:
        return ALLOWED
    try:
        amount = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return GuardrailDecision.block(
            GuardrailStage.ARGUMENT_SHAPE,
            "amount_unreadable",
            "amount is not a decimal number",
        )
    if amount > policy.refund_ceiling:
        # The amount is a number the customer chose, and a number is not identifying,
        # so naming it here helps the operator and costs nothing.
        return GuardrailDecision.block(
            GuardrailStage.ARGUMENT_SHAPE,
            "refund_ceiling",
            f"amount {amount} exceeds the operational ceiling {policy.refund_ceiling}",
        )
    return ALLOWED


def _as_datetime(value: Any) -> datetime | None:
    """Accept a datetime or the ISO string a serialized argument set carries."""
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None
