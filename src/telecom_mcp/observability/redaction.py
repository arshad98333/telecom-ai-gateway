"""Redaction of sensitive data, applied to every log line and every tool response.

Two separate rules, from the operating SOP, are enforced here.

* **Never send to the model.** Passcodes, passwords, payment secrets and access
  tokens have no legitimate place in a prompt or a tool result. These are removed
  entirely; there is no partial form.
* **Redact in logs.** CX ID, phone number, email, address and payment information
  must not appear in telemetry. Identifiers that operations genuinely needs to
  correlate on are replaced by a stable pseudonym rather than deleted, so a support
  engineer can still follow one customer through a day of logs without the log
  holding the customer's identity.

Redaction is by field name first, because that is exact, and by pattern second, so
a secret that arrives inside free text is still caught.
"""

from __future__ import annotations

import hmac
import re
from collections.abc import Mapping, Sequence
from hashlib import blake2s, sha256
from typing import Any, Final

REMOVED: Final = "***removed***"
REDACTED: Final = "***redacted***"

#: Fields that must never reach a model, a log or a tool response, in any form.
NEVER_DISCLOSED: Final[frozenset[str]] = frozenset(
    {
        "passcode",
        "account_passcode",
        "pin",
        "password",
        "secret",
        "client_secret",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "id_token",
        "authorization",
        "auth",
        "bearer",
        "card_number",
        "pan",
        "cvv",
        "cvc",
        "iban",
        "sort_code",
        "account_number",
        "payment_token",
        "private_key",
        "signature",
    }
)

#: Fields replaced by a stable pseudonym on the telemetry path, so correlation survives
#: redaction. A customer asking about their own account still receives their own
#: reference back, because a tool result that disagrees with the request the agent just
#: made is worse than useless to it.
#: "sub" is the token subject. "subject" is deliberately absent: a support ticket has a
#: subject too, and pseudonymising it would destroy the one field an agent needs to read.
PSEUDONYMISED_IN_LOGS: Final[frozenset[str]] = frozenset({"cx_id", "customer_id", "sub"})

#: Fields removed from telemetry but legitimately present in a tool response the
#: customer themselves asked for.
REDACTED_IN_LOGS: Final[frozenset[str]] = frozenset(
    {
        "phone",
        "phone_number",
        "msisdn",
        "email",
        "email_address",
        "address",
        "postal_address",
        "billing_address",
        "date_of_birth",
        "dob",
        "national_id",
    }
)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_JWT = re.compile(r"\beyJ[A-Za-z0-9._-]{10,}")
# Card-like: 13 to 19 digits, optionally separated. Checked before phone numbers.
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
# International or national phone shapes, at least 9 digits.
_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
# A short numeric secret named in the surrounding text: "my passcode is 4821", "pin 0000".
# A blanket four-digit scrub would destroy legitimate numbers, so the word has to be
# there, within a short distance of the digits.
_NAMED_SHORT_SECRET = re.compile(
    r"(?i)\b(passcode|pass code|pin|otp|one[- ]time code|security code|access code)\b"
    r"(.{0,30}?)\d{3,8}"
)

_MAX_DEPTH: Final = 12
_MAX_SEQUENCE_ITEMS: Final = 200


class Redactor:
    """Applies the redaction rules. One instance per process, built from settings."""

    __slots__ = ("_pseudonym_key", "_scrub_free_text")

    def __init__(self, pseudonym_key: bytes, *, scrub_free_text: bool = True) -> None:
        if not pseudonym_key:
            raise ValueError("pseudonym_key must not be empty")
        self._pseudonym_key = pseudonym_key
        self._scrub_free_text = scrub_free_text

    def pseudonym(self, value: str) -> str:
        """A stable, non-reversible reference to an identifier.

        The same CX ID always produces the same reference for a given key, so logs
        remain correlatable, and the reference cannot be turned back into the CX ID
        without the key.
        """
        digest = hmac.new(self._pseudonym_key, value.encode("utf-8"), sha256).hexdigest()
        return f"ref_{digest[:16]}"

    def redact(self, value: Any, *, in_logs: bool = True) -> Any:
        """Return a redacted copy. The input is never modified in place."""
        return self._walk(value, depth=0, in_logs=in_logs)

    def _walk(self, value: Any, *, depth: int, in_logs: bool) -> Any:
        if depth > _MAX_DEPTH:
            return "***truncated: nesting too deep***"
        if isinstance(value, Mapping):
            return {
                key: self._field(str(key), item, depth=depth, in_logs=in_logs)
                for key, item in value.items()
            }
        if isinstance(value, str):
            return self._text(value, in_logs=in_logs)
        if isinstance(value, (bytes, bytearray)):
            return REMOVED
        if isinstance(value, Sequence):
            items = list(value)[:_MAX_SEQUENCE_ITEMS]
            redacted = [self._walk(item, depth=depth + 1, in_logs=in_logs) for item in items]
            if len(value) > _MAX_SEQUENCE_ITEMS:
                redacted.append(f"***truncated: {len(value) - _MAX_SEQUENCE_ITEMS} more items***")
            return redacted
        return value

    def _field(self, name: str, value: Any, *, depth: int, in_logs: bool) -> Any:
        key = name.strip().lower()
        if key in NEVER_DISCLOSED:
            return REMOVED
        if in_logs and key in PSEUDONYMISED_IN_LOGS:
            return self.pseudonym(str(value)) if value is not None else None
        if in_logs and key in REDACTED_IN_LOGS:
            return REDACTED
        return self._walk(value, depth=depth + 1, in_logs=in_logs)

    def _text(self, value: str, *, in_logs: bool) -> str:
        """Scrub secrets hiding inside free text.

        Credentials and card numbers are scrubbed everywhere, because they are never
        legitimate content. Contact details are scrubbed only on the telemetry path;
        a customer asking for their own phone number must still receive it.
        """
        if not self._scrub_free_text:
            return value
        value = _BEARER.sub(f"Bearer {REMOVED}", value)
        value = _JWT.sub(REMOVED, value)
        value = _CARD.sub(REMOVED, value)
        value = _NAMED_SHORT_SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}{REMOVED}", value)
        if not in_logs:
            return value
        value = _EMAIL.sub(REDACTED, value)
        return _PHONE.sub(REDACTED, value)


def derive_pseudonym_key(service_name: str, secret: str) -> bytes:
    """Derive the pseudonym key from a configured secret.

    Kept separate from the redactor so the secret itself never has to be held by a
    component that also formats log lines.
    """
    return blake2s(f"{service_name}:{secret}".encode(), digest_size=32).digest()
