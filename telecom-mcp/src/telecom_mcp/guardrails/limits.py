"""Size and shape checks on the arguments, run before anything else looks at them.

These are cheap and they run first for a reason. A pathological argument - a megabyte
of text, a structure nested a thousand deep, an array with a hundred thousand entries
- costs real work to validate, redact, log and forward. Refusing it on a length check
costs a few microseconds.

Nothing here inspects meaning. The rules are counts and depths, so the reason a call
was refused can be written into an audit record without carrying any of the input that
caused it.
"""

from __future__ import annotations

import json
from typing import Any, Final

from telecom_mcp.guardrails.decision import ALLOWED, GuardrailDecision, GuardrailStage
from telecom_mcp.guardrails.policy import GuardrailPolicy

#: Control characters have no place in a tool argument and are how a log line is
#: forged: a raw newline lets an attacker write a second, fabricated record.
_FORBIDDEN_CONTROL: Final[frozenset[str]] = frozenset(
    chr(code) for code in range(0x20) if chr(code) not in "\t\n\r"
) | {"\x7f"}


def check_arguments(arguments: dict[str, Any], policy: GuardrailPolicy) -> GuardrailDecision:
    """Refuse arguments that are too big, too deep or too wide."""
    if not policy.enabled:
        return ALLOWED

    encoded = json.dumps(arguments, default=str, ensure_ascii=False).encode("utf-8")
    if len(encoded) > policy.max_argument_bytes:
        return GuardrailDecision.block(
            GuardrailStage.ARGUMENT_SIZE,
            "max_bytes",
            f"arguments serialize to {len(encoded)} bytes, limit {policy.max_argument_bytes}",
        )

    return _walk(arguments, policy, depth=1)


def _walk(value: Any, policy: GuardrailPolicy, *, depth: int) -> GuardrailDecision:
    """Depth-first shape check. Returns on the first rule that refuses."""
    if depth > policy.max_argument_depth:
        return GuardrailDecision.block(
            GuardrailStage.ARGUMENT_SHAPE,
            "max_depth",
            f"nesting reached depth {depth}, limit {policy.max_argument_depth}",
        )

    if isinstance(value, str):
        return _check_string(value, policy)

    if isinstance(value, dict):
        if len(value) > policy.max_object_keys:
            return GuardrailDecision.block(
                GuardrailStage.ARGUMENT_SHAPE,
                "max_object_keys",
                f"object has {len(value)} keys, limit {policy.max_object_keys}",
            )
        for key, item in value.items():
            key_decision = _check_string(str(key), policy)
            if not key_decision.allowed:
                return key_decision
            decision = _walk(item, policy, depth=depth + 1)
            if not decision.allowed:
                return decision
        return ALLOWED

    if isinstance(value, list | tuple):
        if len(value) > policy.max_array_items:
            return GuardrailDecision.block(
                GuardrailStage.ARGUMENT_SHAPE,
                "max_array_items",
                f"array has {len(value)} items, limit {policy.max_array_items}",
            )
        for item in value:
            decision = _walk(item, policy, depth=depth + 1)
            if not decision.allowed:
                return decision
        return ALLOWED

    return ALLOWED


def _check_string(value: str, policy: GuardrailPolicy) -> GuardrailDecision:
    if len(value) > policy.max_string_length:
        return GuardrailDecision.block(
            GuardrailStage.ARGUMENT_SIZE,
            "max_string_length",
            f"a string field is {len(value)} characters, limit {policy.max_string_length}",
        )
    found = _FORBIDDEN_CONTROL.intersection(value)
    if found:
        # The characters themselves are reported as code points, never echoed, so the
        # audit record cannot be used to smuggle them somewhere else.
        points = ", ".join(f"U+{ord(char):04X}" for char in sorted(found))
        return GuardrailDecision.block(
            GuardrailStage.ARGUMENT_SHAPE,
            "control_characters",
            f"a string field contains control characters ({points})",
        )
    return ALLOWED
