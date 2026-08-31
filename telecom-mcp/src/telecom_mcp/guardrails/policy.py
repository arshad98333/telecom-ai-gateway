"""Every guardrail threshold, in one frozen object, validated at startup.

Thresholds are the part of a guardrail that changes. Keeping them in one dataclass
rather than as constants next to each check means the whole policy can be logged in a
line, diffed between environments, and tightened for production without editing the
control it tunes.

The defaults are the production defaults. A deployment that wants a looser policy has
to say so out loud in its environment, which is the point: nobody loosens a control by
forgetting to set something.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Self

from telecom_mcp.domain.errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class GuardrailPolicy:
    """Thresholds for every stage. All limits are inclusive upper bounds."""

    #: Master switch. Off is a development convenience and is refused in production
    #: by the settings validator, not here.
    enabled: bool = True

    # --- Argument size and shape ---
    #: Serialized argument budget. A tool argument is a customer sentence, not a file.
    max_argument_bytes: int = 8_192
    #: Nesting depth. Deep structures are how a parser is made to work harder than the
    #: request is worth.
    max_argument_depth: int = 6
    max_string_length: int = 4_096
    max_array_items: int = 100
    max_object_keys: int = 50

    # --- Content ---
    injection_scan: bool = True

    # --- Volume ---
    #: Sustained rate per identity, and how far above it one burst may go.
    rate_limit_per_minute: int = 120
    rate_limit_burst: int = 30
    #: Irreversible actions allowed on one case before a human is required. A support
    #: case that needs six refunds needs a supervisor, not a faster agent.
    write_actions_per_case: int = 5
    action_budget_window_s: float = 3_600.0

    # --- Business sanity ---
    #: How far ahead a callback may be booked. A date beyond this is a typo in the
    #: year, not a customer planning ahead.
    callback_horizon_days: int = 90
    #: The operational refund ceiling. The contract caps an autonomous refund at 5.00
    #: and is frozen for v1; this can be lowered per environment without touching it.
    refund_ceiling: Decimal = Decimal("5.00")

    # --- Output ---
    max_output_bytes: int = 262_144
    output_secret_scan: bool = True

    def __post_init__(self) -> None:
        problems: list[str] = []
        positives = (
            ("max_argument_bytes", self.max_argument_bytes),
            ("max_argument_depth", self.max_argument_depth),
            ("max_string_length", self.max_string_length),
            ("max_array_items", self.max_array_items),
            ("max_object_keys", self.max_object_keys),
            ("rate_limit_per_minute", self.rate_limit_per_minute),
            ("write_actions_per_case", self.write_actions_per_case),
            ("max_output_bytes", self.max_output_bytes),
        )
        problems += [
            f"{name} must be positive, got {value}" for name, value in positives if value <= 0
        ]
        if self.rate_limit_burst < 0:
            problems.append(f"rate_limit_burst must not be negative, got {self.rate_limit_burst}")
        if self.action_budget_window_s <= 0:
            problems.append("action_budget_window_s must be positive")
        if self.callback_horizon_days <= 0:
            problems.append("callback_horizon_days must be positive")
        if self.refund_ceiling <= Decimal("0"):
            problems.append("refund_ceiling must be positive")
        if self.max_string_length > self.max_argument_bytes:
            problems.append(
                "max_string_length exceeds max_argument_bytes, so the string limit can "
                "never be the rule that refuses and the message would name the wrong one"
            )
        if problems:
            raise ConfigurationError(
                "invalid guardrail policy:\n  - " + "\n  - ".join(problems),
                operation="guardrail_policy",
            )

    @classmethod
    def strict(cls) -> Self:
        """The production posture: the defaults, with no room above them."""
        return cls()

    @classmethod
    def disabled(cls) -> Self:
        """Every check off. For tests that are about something else."""
        return cls(enabled=False, injection_scan=False, output_secret_scan=False)

    def tightened(self, **overrides: object) -> Self:
        """A copy with fields replaced, validated the same way as the original."""
        return replace(self, **overrides)  # type: ignore[arg-type]

    def describe(self) -> dict[str, object]:
        """One loggable line. No secrets live here, so nothing is redacted."""
        return {
            "enabled": self.enabled,
            "max_argument_bytes": self.max_argument_bytes,
            "max_argument_depth": self.max_argument_depth,
            "max_string_length": self.max_string_length,
            "max_array_items": self.max_array_items,
            "max_object_keys": self.max_object_keys,
            "injection_scan": self.injection_scan,
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "rate_limit_burst": self.rate_limit_burst,
            "write_actions_per_case": self.write_actions_per_case,
            "action_budget_window_s": self.action_budget_window_s,
            "callback_horizon_days": self.callback_horizon_days,
            "refund_ceiling": str(self.refund_ceiling),
            "max_output_bytes": self.max_output_bytes,
            "output_secret_scan": self.output_secret_scan,
        }
