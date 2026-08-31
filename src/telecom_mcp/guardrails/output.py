"""The last thing that looks at a response before the model does.

The redactor upstream removes what it knows about, by field name first and pattern
second. This is the check that runs afterwards and asks a blunter question: does the
payload still contain something that is a secret whatever it is called?

The two failures it is here to catch are the ones a field-name rule cannot. A backend
adds a field nobody here has heard of, and it holds a token. Or a secret arrives
inside free text - pasted into a ticket body by a customer trying to be helpful - and
comes back out in the response.

A hit refuses the whole response rather than scrubbing it. A scrubbed response is a
response somebody will read as complete, and a redacted-in-place payload gives the
model a half-answer it will happily narrate. Refusing is louder, and loud is correct
for a control that should never fire.

Card numbers are checked with the Luhn algorithm before being called card numbers,
because a sixteen-digit order reference is not a payment instrument and refusing every
response that has one would make this control worthless within a week.
"""

from __future__ import annotations

import json
import re
from typing import Any, Final

from telecom_mcp.guardrails.decision import ALLOWED, GuardrailDecision, GuardrailStage
from telecom_mcp.guardrails.policy import GuardrailPolicy

#: Rule name to pattern. Named so a dashboard can show which shape leaked without the
#: value that leaked appearing anywhere.
SECRET_PATTERNS: Final[dict[str, re.Pattern[str]]] = {
    "bearer_token": re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}", re.IGNORECASE),
    "json_web_token": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "private_key_block": re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    "azure_connection_string": re.compile(
        r"(?:InstrumentationKey|AccountKey|SharedAccessKey)\s*=\s*[A-Za-z0-9+/=._-]{16,}",
        re.IGNORECASE,
    ),
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "generic_api_key": re.compile(
        r"\b(?:api[_-]?key|client[_-]?secret|password|passcode)\b\s*[:=]\s*[^\s\"',}]{8,}",
        re.IGNORECASE,
    ),
}

_CARD_CANDIDATE: Final = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def check_output(payload: dict[str, Any], policy: GuardrailPolicy) -> GuardrailDecision:
    """Refuse a response that is too large or still carries a secret."""
    if not policy.enabled:
        return ALLOWED

    encoded = json.dumps(payload, default=str, ensure_ascii=False)
    size = len(encoded.encode("utf-8"))
    if size > policy.max_output_bytes:
        return GuardrailDecision.block(
            GuardrailStage.OUTPUT_SIZE,
            "max_bytes",
            f"response is {size} bytes, limit {policy.max_output_bytes}",
        )

    if not policy.output_secret_scan:
        return ALLOWED

    for rule, pattern in SECRET_PATTERNS.items():
        if pattern.search(encoded):
            return GuardrailDecision.block(
                GuardrailStage.OUTPUT_SECRET,
                rule,
                f"the response matched the {rule} shape after redaction, which means "
                "a field arrived that redaction does not know about",
            )

    if _contains_card_number(encoded):
        return GuardrailDecision.block(
            GuardrailStage.OUTPUT_SECRET,
            "card_number",
            "the response contains a Luhn-valid card number after redaction",
        )
    return ALLOWED


def _contains_card_number(text: str) -> bool:
    """True when a digit run is both card-shaped and Luhn-valid."""
    for match in _CARD_CANDIDATE.finditer(text):
        digits = re.sub(r"\D", "", match.group())
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            return True
    return False


def _luhn_ok(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0
