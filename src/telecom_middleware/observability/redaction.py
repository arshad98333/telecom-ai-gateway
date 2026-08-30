"""Redaction for telemetry, applied as a processor so no call site can forget it.

The same rules as the tools package, restated here because this service holds the data
rather than passing it through: the never-disclosed fields are removed outright, the
customer reference becomes a stable keyed pseudonym so an investigator can still follow
one customer through a day of logs, and contact details are removed from telemetry
while remaining in the response to the customer who owns them.
"""

from __future__ import annotations

import hmac
import re
from collections.abc import Mapping, Sequence
from hashlib import blake2s, sha256
from typing import Any, Final

REMOVED: Final = "***removed***"
REDACTED: Final = "***redacted***"

NEVER_DISCLOSED: Final[frozenset[str]] = frozenset(
    {
        "passcode",
        "hash",
        "pin",
        "password",
        "secret",
        "client_secret",
        "api_key",
        "access_token",
        "refresh_token",
        "id_token",
        "authorization",
        "bearer",
        "card_number",
        "cvv",
        "iban",
        "account_number",
        "private_key",
        "mongodb_uri",
    }
)

PSEUDONYMISED_IN_LOGS: Final[frozenset[str]] = frozenset(
    {"cx_id", "customer_id", "sub", "actor_sub"}
)

REDACTED_IN_LOGS: Final[frozenset[str]] = frozenset(
    {"email", "phone", "address", "billing_address", "date_of_birth", "national_id"}
)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_JWT = re.compile(r"\beyJ[A-Za-z0-9._-]{10,}")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
_NAMED_SHORT_SECRET = re.compile(
    r"(?i)\b(passcode|pass code|pin|otp|one[- ]time code|security code|access code)\b"
    r"(.{0,30}?)\d{3,8}"
)

_MAX_DEPTH: Final = 12
_MAX_ITEMS: Final = 200


class Redactor:
    """Applies the rules. One instance per process, built from settings."""

    __slots__ = ("_key",)

    def __init__(self, pseudonym_key: bytes) -> None:
        if not pseudonym_key:
            raise ValueError("pseudonym_key must not be empty")
        self._key = pseudonym_key

    def pseudonym(self, value: str) -> str:
        """A stable, non-reversible reference. Correlatable, not identifying."""
        return f"ref_{hmac.new(self._key, value.encode('utf-8'), sha256).hexdigest()[:16]}"

    def redact(self, value: Any, *, in_logs: bool = True) -> Any:
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
            items = list(value)[:_MAX_ITEMS]
            walked = [self._walk(item, depth=depth + 1, in_logs=in_logs) for item in items]
            if len(value) > _MAX_ITEMS:
                walked.append(f"***truncated: {len(value) - _MAX_ITEMS} more items***")
            return walked
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
        value = _BEARER.sub(f"Bearer {REMOVED}", value)
        value = _JWT.sub(REMOVED, value)
        value = _CARD.sub(REMOVED, value)
        value = _NAMED_SHORT_SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}{REMOVED}", value)
        if not in_logs:
            return value
        value = _EMAIL.sub(REDACTED, value)
        return _PHONE.sub(REDACTED, value)


def derive_pseudonym_key(service_name: str, secret: str) -> bytes:
    """Derive the key from a secret that already exists, so no new one is managed."""
    return blake2s(f"{service_name}:{secret}".encode(), digest_size=32).digest()
