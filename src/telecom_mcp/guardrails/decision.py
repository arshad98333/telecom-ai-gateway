"""What a guardrail returns, and the vocabulary it returns it in.

A guardrail never raises and never edits the thing it is inspecting. It answers one
question - may this proceed - and, when the answer is no, says which rule refused and
why in wording that is safe to put in an audit record.

Two audiences, two strings. ``reason`` is for the operator: precise, specific, and
guaranteed to carry no customer data, because it is built from rule names and counts
rather than from the input. ``public_message`` is for the caller: general enough that
a blocked probe learns nothing about which control caught it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Self


class GuardrailStage(StrEnum):
    """Every control, named. An audit record says exactly which one refused."""

    ARGUMENT_SIZE = "argument_size"
    ARGUMENT_SHAPE = "argument_shape"
    INJECTION = "injection"
    IDENTIFIER_FORMAT = "identifier_format"
    RATE_LIMIT = "rate_limit"
    ACTION_BUDGET = "action_budget"
    OUTPUT_SECRET = "output_secret"  # noqa: S105 - a stage name, not a credential
    OUTPUT_SIZE = "output_size"


#: Told to a caller a guardrail refused. Deliberately the same for every stage: the
#: difference between two messages is a side channel that tells a prober which control
#: they tripped, and therefore which one to work around.
DEFAULT_PUBLIC_MESSAGE = "The request was refused by a safety control."


@dataclass(frozen=True, slots=True)
class GuardrailViolation:
    """One refusal. Everything here is safe to log."""

    stage: GuardrailStage
    #: The specific rule inside the stage, e.g. "max_string_length".
    rule: str
    #: Operator-facing detail. Built from rule names and numbers, never from input.
    reason: str
    public_message: str = DEFAULT_PUBLIC_MESSAGE

    def __str__(self) -> str:
        return f"{self.stage}/{self.rule}: {self.reason}"


@dataclass(frozen=True, slots=True)
class GuardrailDecision:
    """The answer. ``allowed`` and ``violation`` are always consistent."""

    violation: GuardrailViolation | None = None

    @property
    def allowed(self) -> bool:
        return self.violation is None

    @classmethod
    def allow(cls) -> Self:
        return cls()

    @classmethod
    def block(
        cls,
        stage: GuardrailStage,
        rule: str,
        reason: str,
        *,
        public_message: str = DEFAULT_PUBLIC_MESSAGE,
    ) -> Self:
        return cls(
            GuardrailViolation(stage=stage, rule=rule, reason=reason, public_message=public_message)
        )


#: The one allowed decision, reused rather than reallocated on every call.
ALLOWED: GuardrailDecision = GuardrailDecision()
