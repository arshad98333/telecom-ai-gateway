"""The four-digit account passcode: hashing, verification, lockout.

A four-digit secret has ten thousand possibilities. It is weak by construction, so the
controls around it carry the load rather than the secret itself:

* it is stored only as an Argon2id hash, never in plaintext, never recoverable;
* verification is constant time, and a wrong CX ID costs the same as a wrong passcode,
  so timing cannot be used to enumerate which customers exist;
* attempts are counted atomically and the account locks at the limit;
* a failure never says how many attempts remain, and never distinguishes "no such
  customer" from "wrong passcode".

It is a second factor beside a CX ID the caller must already know, not a password, and
this module is written on that assumption.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from telecom_middleware.domain.errors import AccountLockedError, PasscodeIncorrectError

# [0-9] rather than \d: Python's \d also matches Arabic-Indic and other digit systems,
# and a passcode a telephone keypad cannot produce is one the customer can never enter.
PASSCODE_PATTERN: Final = re.compile(r"^[0-9]{4}$")

#: Deliberately not the library defaults. A four-digit secret has a tiny search space,
#: so the cost of a single guess is the only thing standing between an attacker with the
#: database and every passcode in it.
_HASHER: Final = PasswordHasher(time_cost=3, memory_cost=65_536, parallelism=4, hash_len=32)

#: Verified once at import against a known-bad value, so a wrong CX ID can be made to
#: cost the same as a wrong passcode without hashing something new each time.
_DUMMY_HASH: Final = _HASHER.hash("0000")


def hash_passcode(passcode: str) -> str:
    """Hash a passcode for storage. Raises if it is not four digits."""
    if not PASSCODE_PATTERN.match(passcode):
        raise ValueError("a passcode must be exactly four digits")
    return _HASHER.hash(passcode)


def verify_passcode(stored_hash: str, candidate: str) -> bool:
    """Constant-time verification. Never raises for a wrong answer."""
    try:
        return _HASHER.verify(stored_hash, candidate)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def burn_equivalent_time() -> None:
    """Spend the same work as a real verification, for a customer that does not exist.

    Without this, an unknown CX ID answers faster than a known one, and the difference
    is enough to enumerate which customers exist.
    """
    verify_passcode(_DUMMY_HASH, "9999")


@dataclass(frozen=True, slots=True)
class AuthenticationOutcome:
    authenticated: bool
    locked_until: datetime | None = None


def check_lockout(locked_until: datetime | None, now: datetime) -> None:
    """Refuse while a lockout is in force."""
    if locked_until is not None and locked_until > now:
        raise AccountLockedError("authentication is locked")


def result_or_raise(outcome: AuthenticationOutcome) -> None:
    """Turn a failed attempt into the one error a caller ever sees."""
    if not outcome.authenticated:
        raise PasscodeIncorrectError("authentication failed")
