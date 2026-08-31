"""Refuse free text that is shaped like an instruction to the model rather than a
sentence from a customer.

The threat is specific. A tool argument travels from the customer, through the agent,
into a transcript, and back through the model on the next turn. Text that reads as an
instruction gets a second chance to be obeyed there, and the model has no way to tell
that "ignore your previous instructions" arrived as a support ticket subject rather
than from the operator.

This is a filter, not a proof. It catches the well-known shapes cheaply and refuses
them; it does not claim to catch a determined novel attempt, and nothing downstream is
allowed to relax because this ran. Defence in depth means the authorization kernel
still refuses what this misses.

Every pattern is anchored on the imperative form rather than on a keyword, because
"what does the system prompt do" is a fair question from a customer and "reveal your
system prompt" is not.
"""

from __future__ import annotations

import re
from typing import Any, Final

from telecom_mcp.guardrails.decision import ALLOWED, GuardrailDecision, GuardrailStage
from telecom_mcp.guardrails.policy import GuardrailPolicy

_FLAGS: Final = re.IGNORECASE | re.DOTALL

#: Rule name to the patterns that trip it. The rule name is what reaches the audit
#: record and the dashboard, so it is stable and readable; the patterns move.
INJECTION_PATTERNS: Final[dict[str, tuple[re.Pattern[str], ...]]] = {
    "instruction_override": (
        re.compile(
            r"\b(?:ignore|disregard|forget|override)\b[^.\n]{0,40}?\b"
            r"(?:previous|prior|earlier|above|all)\b[^.\n]{0,20}?\b"
            r"(?:instruction|instructions|prompt|prompts|rule|rules|direction|directions)\b",
            _FLAGS,
        ),
        re.compile(r"\bnew\s+instructions?\s*[:\-]", _FLAGS),
    ),
    "role_reassignment": (
        re.compile(r"\byou\s+are\s+now\b", _FLAGS),
        re.compile(r"\b(?:act|behave)\s+as\s+(?:if\s+you\s+are\s+)?(?:an?\s+)?\w+", _FLAGS),
        re.compile(r"\bpretend\s+(?:to\s+be|that\s+you)\b", _FLAGS),
        re.compile(r"\b(?:developer|god|jailbreak|dan)\s+mode\b", _FLAGS),
        re.compile(r"\bfrom\s+now\s+on\b[^.\n]{0,30}\byou\b", _FLAGS),
    ),
    "prompt_disclosure": (
        re.compile(
            r"\b(?:reveal|show|print|repeat|output|reproduce|disclose)\b[^.\n]{0,30}?\b"
            r"(?:system\s+prompt|your\s+instructions|your\s+prompt|the\s+above)\b",
            _FLAGS,
        ),
        re.compile(r"\bwhat\s+(?:were|are)\s+your\s+(?:exact\s+)?instructions\b", _FLAGS),
    ),
    "control_token_forgery": (
        re.compile(r"<\|\s*(?:im_start|im_end|endoftext|system|assistant)\s*\|>", _FLAGS),
        re.compile(r"<\s*/?\s*(?:system|assistant|tool_call|function_call)\s*>", _FLAGS),
        re.compile(r"\[\s*/?\s*INST\s*\]", _FLAGS),
        re.compile(r"^\s*(?:system|assistant|developer)\s*:", re.IGNORECASE | re.MULTILINE),
    ),
    "control_evasion": (
        re.compile(
            r"\b(?:bypass|skip|suppress|disable|turn\s+off)\b[^.\n]{0,30}?\b"
            r"(?:guardrail|guardrails|safety|approval|authorization|validation|audit|log|logging)\b",
            _FLAGS,
        ),
        re.compile(r"\b(?:do\s+not|don't|never)\s+(?:log|record|audit|tell|ask)\b", _FLAGS),
        re.compile(r"\bwithout\s+(?:asking|informing|notifying)\b", _FLAGS),
    ),
    "exfiltration": (
        re.compile(r"!\[[^\]]*\]\(\s*https?://", _FLAGS),
        re.compile(r"\b(?:send|post|upload|exfiltrate|forward)\b[^.\n]{0,30}?\bhttps?://", _FLAGS),
        re.compile(r"\b(?:curl|wget|fetch)\s+https?://", _FLAGS),
    ),
}


def check_for_injection(arguments: dict[str, Any], policy: GuardrailPolicy) -> GuardrailDecision:
    """Refuse the first free-text field that reads as an instruction."""
    if not (policy.enabled and policy.injection_scan):
        return ALLOWED

    for field, value in _strings(arguments):
        for rule, patterns in INJECTION_PATTERNS.items():
            hits = sum(1 for pattern in patterns if pattern.search(value))
            if hits:
                # The field name comes from the frozen tool schema, so naming it is
                # safe. The text that matched is never repeated.
                return GuardrailDecision.block(
                    GuardrailStage.INJECTION,
                    rule,
                    f"field '{field}' matched {hits} {rule} pattern(s)",
                )
    return ALLOWED


def _strings(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    """Every string in the structure, with a dotted path to it."""
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
