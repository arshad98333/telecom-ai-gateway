"""A four-digit secret is weak by construction, so its controls are tested hard."""

from datetime import UTC, datetime, timedelta

import pytest

from telecom_middleware.domain.errors import AccountLockedError, PasscodeIncorrectError
from telecom_middleware.services.passcode import (
    AuthenticationOutcome,
    burn_equivalent_time,
    check_lockout,
    hash_passcode,
    result_or_raise,
    verify_passcode,
)

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


def test_a_passcode_hashes_and_verifies() -> None:
    stored = hash_passcode("4821")

    assert verify_passcode(stored, "4821") is True
    assert verify_passcode(stored, "4822") is False


def test_the_stored_hash_never_contains_the_passcode() -> None:
    assert "4821" not in hash_passcode("4821")


def test_the_same_passcode_hashes_differently_every_time() -> None:
    # A per-hash salt: otherwise the database tells an attacker which customers share
    # a passcode, and 10,000 possibilities makes that a very short list.
    assert hash_passcode("4821") != hash_passcode("4821")


def test_the_hash_uses_argon2id_rather_than_a_general_purpose_digest() -> None:
    assert hash_passcode("4821").startswith("$argon2id$")


@pytest.mark.parametrize("bad", ["", "123", "12345", "abcd", "12 4", "١٢٣٤", "0x12"])
def test_anything_that_is_not_four_digits_is_refused(bad: str) -> None:
    with pytest.raises(ValueError, match="four digits"):
        hash_passcode(bad)


def test_all_zeros_and_all_nines_are_valid_passcodes() -> None:
    # Boundary values of the space. Weak, but the policy is enforced elsewhere; the
    # hashing layer must not silently reject them.
    for edge in ("0000", "9999"):
        assert verify_passcode(hash_passcode(edge), edge)


def test_a_corrupt_stored_hash_fails_closed_rather_than_raising() -> None:
    assert verify_passcode("not-a-hash", "4821") is False
    assert verify_passcode("", "4821") is False


def test_an_unknown_customer_can_be_made_to_cost_the_same_as_a_wrong_passcode() -> None:
    # Not a timing assertion - those are flaky - but the code path must exist and run.
    burn_equivalent_time()


def test_a_lockout_in_force_refuses_before_anything_is_verified() -> None:
    with pytest.raises(AccountLockedError):
        check_lockout(NOW + timedelta(minutes=5), NOW)


def test_an_expired_lockout_does_not_refuse() -> None:
    check_lockout(NOW - timedelta(seconds=1), NOW)
    check_lockout(None, NOW)


def test_a_lockout_expiring_exactly_now_is_over() -> None:
    check_lockout(NOW, NOW)


def test_a_failed_attempt_raises_the_one_error_a_caller_ever_sees() -> None:
    with pytest.raises(PasscodeIncorrectError) as caught:
        result_or_raise(AuthenticationOutcome(authenticated=False))

    # No hint about attempts remaining, and nothing that distinguishes a wrong passcode
    # from a customer that does not exist.
    assert caught.value.title == "Authentication failed."
    assert "attempt" not in caught.value.title.lower()


def test_a_successful_attempt_raises_nothing() -> None:
    result_or_raise(AuthenticationOutcome(authenticated=True))
