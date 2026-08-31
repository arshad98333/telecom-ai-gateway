"""Refuse text that does not look like what it is.

Two attacks share one root cause: a string can render as one thing and contain
another.

*Invisible characters.* Zero-width spaces, soft hyphens and the bidirectional
override family are not shown by any terminal, log viewer or ticket UI. A reviewer
approving a ticket body sees a harmless sentence; the model, and the next system to
parse it, see the characters in between. The bidi overrides are worse than hidden -
they reorder what is displayed, so text can be made to read as its own opposite.

*Confusable scripts.* Cyrillic U+0430 renders identically to Latin 'a' in almost
every font. A customer reference or a name that mixes scripts is either an encoding
accident or someone building a lookalike, and neither is a thing to pass through to a
billing system.

Both are refused rather than stripped. Silently rewriting a customer's input means the
record of what they asked for is no longer what they asked for, and an audit trail
that has been quietly corrected is not an audit trail.
"""

from __future__ import annotations

import unicodedata
from typing import Any, Final

from telecom_mcp.guardrails.decision import ALLOWED, GuardrailDecision, GuardrailStage
from telecom_mcp.guardrails.policy import GuardrailPolicy

#: Characters that occupy no visual space, or that change the direction of what
#: follows them. None of these has a legitimate place in a tool argument.
INVISIBLE: Final[frozenset[str]] = frozenset(
    {
        "­",  # soft hyphen
        "​",  # zero width space
        "‌",  # zero width non-joiner
        "‍",  # zero width joiner
        "‎",  # left-to-right mark
        "‏",  # right-to-left mark
        "‪",  # left-to-right embedding
        "‫",  # right-to-left embedding
        "‬",  # pop directional formatting
        "‭",  # left-to-right override
        "‮",  # right-to-left override
        "⁠",  # word joiner
        "⁦",  # left-to-right isolate
        "⁧",  # right-to-left isolate
        "⁨",  # first strong isolate
        "⁩",  # pop directional isolate
        "﻿",  # zero width no-break space
    }
)

#: A coarse script classification, by the blocks that actually collide. This is not a
#: full implementation of Unicode script extensions and does not pretend to be: it
#: covers the three alphabets whose letters are mutually indistinguishable on screen.
_SCRIPT_RANGES: Final[tuple[tuple[str, int, int], ...]] = (
    ("latin", 0x0041, 0x024F),
    ("greek", 0x0370, 0x03FF),
    ("greek", 0x1F00, 0x1FFF),
    ("cyrillic", 0x0400, 0x04FF),
    ("cyrillic", 0x0500, 0x052F),
)

#: More than this many combining marks on one base character is a rendering attack,
#: not a language. No natural script stacks four.
MAX_COMBINING_RUN: Final = 3


def check_unicode_safety(arguments: dict[str, Any], policy: GuardrailPolicy) -> GuardrailDecision:
    """Refuse the first field that hides something or mixes confusable scripts."""
    if not policy.enabled:
        return ALLOWED

    for field, value in _strings(arguments):
        decision = _check_one(field, value)
        if not decision.allowed:
            return decision
    return ALLOWED


def _check_one(field: str, value: str) -> GuardrailDecision:
    hidden = INVISIBLE.intersection(value)
    if hidden:
        points = ", ".join(f"U+{ord(char):04X}" for char in sorted(hidden))
        return GuardrailDecision.block(
            GuardrailStage.ARGUMENT_SHAPE,
            "invisible_characters",
            f"field '{field}' contains characters that render as nothing ({points})",
        )

    scripts = {script for script in map(_script_of, value) if script}
    if len(scripts) > 1:
        return GuardrailDecision.block(
            GuardrailStage.ARGUMENT_SHAPE,
            "mixed_script",
            f"field '{field}' mixes the {' and '.join(sorted(scripts))} alphabets, "
            "whose letters are visually identical",
        )

    run = 0
    for char in value:
        run = run + 1 if unicodedata.combining(char) else 0
        if run > MAX_COMBINING_RUN:
            return GuardrailDecision.block(
                GuardrailStage.ARGUMENT_SHAPE,
                "combining_marks",
                f"field '{field}' stacks more than {MAX_COMBINING_RUN} combining marks "
                "on one character",
            )
    return ALLOWED


def _script_of(char: str) -> str | None:
    """The alphabet a letter belongs to, or None for digits, spaces and punctuation."""
    if not char.isalpha():
        return None
    code = ord(char)
    for script, low, high in _SCRIPT_RANGES:
        if low <= code <= high:
            return script
    return None  # anything outside the confusable set is not what this rule is about


def _strings(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(prefix or "<root>", value)]
    if isinstance(value, dict):
        found: list[tuple[str, str]] = []
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            found += _strings(item, path)
        return found
    if isinstance(value, list | tuple):
        found = []
        for index, item in enumerate(value):
            found += _strings(item, f"{prefix}[{index}]")
        return found
    return []
