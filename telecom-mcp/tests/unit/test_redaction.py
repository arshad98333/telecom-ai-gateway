"""Redaction is a security control, so it is tested as one: by trying to defeat it."""

import pytest

from telecom_mcp.observability.redaction import (
    REDACTED,
    REMOVED,
    Redactor,
    derive_pseudonym_key,
)


@pytest.fixture
def redactor() -> Redactor:
    return Redactor(derive_pseudonym_key("telecom-mcp-tools", "test-secret"))


def test_the_passcode_is_removed_entirely_not_masked(redactor: Redactor) -> None:
    result = redactor.redact({"cx_id": "CX-1234", "passcode": "4821"})

    assert result["passcode"] == REMOVED
    assert "4821" not in str(result)


@pytest.mark.parametrize(
    "field",
    ["password", "api_key", "access_token", "refresh_token", "cvv", "card_number", "private_key"],
)
def test_every_never_disclosed_field_is_removed(redactor: Redactor, field: str) -> None:
    assert redactor.redact({field: "sensitive-value"})[field] == REMOVED


def test_the_cx_id_becomes_a_stable_reference_so_logs_stay_correlatable(
    redactor: Redactor,
) -> None:
    first = redactor.redact({"cx_id": "CX-1234"})["cx_id"]
    second = redactor.redact({"cx_id": "CX-1234"})["cx_id"]
    other = redactor.redact({"cx_id": "CX-9999"})["cx_id"]

    assert first == second
    assert first != other
    assert "CX-1234" not in first
    assert first.startswith("ref_")


def test_the_reference_differs_per_key_so_it_cannot_be_correlated_across_services() -> None:
    a = Redactor(derive_pseudonym_key("svc", "key-a")).pseudonym("CX-1234")
    b = Redactor(derive_pseudonym_key("svc", "key-b")).pseudonym("CX-1234")

    assert a != b


def test_contact_details_are_redacted_in_logs_but_allowed_in_a_customers_own_response(
    redactor: Redactor,
) -> None:
    record = {"email": "customer@example.com", "phone": "+44 7700 900123"}

    assert redactor.redact(record, in_logs=True) == {"email": REDACTED, "phone": REDACTED}
    assert redactor.redact(record, in_logs=False) == record


def test_secrets_hidden_inside_free_text_are_still_caught(redactor: Redactor) -> None:
    text = (
        "customer said their email is jo@example.com, called from +44 7700 900123, "
        "card 4111 1111 1111 1111, header Bearer abcdef0123456789"
    )

    scrubbed = redactor.redact({"note": text})["note"]

    assert "jo@example.com" not in scrubbed
    assert "4111 1111 1111 1111" not in scrubbed
    assert "abcdef0123456789" not in scrubbed
    assert "+44 7700 900123" not in scrubbed


def test_a_jwt_pasted_into_a_message_is_removed(redactor: Redactor) -> None:
    token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJDWC0xMjM0In0.c2lnbmF0dXJl"

    assert token not in str(redactor.redact({"note": f"token {token}"}))


def test_nested_structures_are_redacted_at_every_level(redactor: Redactor) -> None:
    payload = {"outer": {"inner": [{"password": "hunter2"}, {"email": "a@b.co"}]}}

    result = redactor.redact(payload)

    assert result["outer"]["inner"][0]["password"] == REMOVED
    assert result["outer"]["inner"][1]["email"] == REDACTED


def test_field_names_are_matched_regardless_of_case_and_spacing(redactor: Redactor) -> None:
    assert redactor.redact({" PassCode ": "4821"})[" PassCode "] == REMOVED


def test_absurd_nesting_is_truncated_rather_than_exhausting_the_stack(
    redactor: Redactor,
) -> None:
    deep: dict[str, object] = {"password": "hunter2"}
    for _ in range(50):
        deep = {"level": deep}

    assert "hunter2" not in str(redactor.redact(deep))
    assert "nesting too deep" in str(redactor.redact(deep))


def test_a_huge_list_is_truncated_so_one_record_cannot_flood_the_log(
    redactor: Redactor,
) -> None:
    result = redactor.redact({"items": list(range(500))})["items"]

    assert len(result) == 201
    assert "300 more items" in result[-1]


def test_bytes_are_never_logged_because_their_contents_are_unknown(redactor: Redactor) -> None:
    assert redactor.redact({"blob": b"\x00secret"})["blob"] == REMOVED


def test_redaction_returns_a_copy_and_leaves_the_input_untouched(redactor: Redactor) -> None:
    original = {"passcode": "4821"}

    redactor.redact(original)

    assert original == {"passcode": "4821"}


def test_an_empty_pseudonym_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        Redactor(b"")


def test_non_string_values_pass_through_unchanged(redactor: Redactor) -> None:
    assert redactor.redact({"count": 3, "ok": True, "nothing": None}) == {
        "count": 3,
        "ok": True,
        "nothing": None,
    }


def test_a_passcode_spoken_aloud_in_free_text_is_removed(redactor: Redactor) -> None:
    # Voice transcription puts the passcode straight into the conversation text.
    text = "My passcode is 4821, and the PIN on the account is 0000."

    scrubbed = redactor.redact({"note": text})["note"]

    assert "4821" not in scrubbed
    assert "0000" not in scrubbed
    assert "passcode" in scrubbed  # the context stays, so the log is still readable


def test_an_unrelated_four_digit_number_survives(redactor: Redactor) -> None:
    # A blanket four-digit scrub would destroy invoice numbers, years and amounts.
    assert redactor.redact({"note": "Invoice 2026 for 1234 units"})["note"] == (
        "Invoice 2026 for 1234 units"
    )


def test_a_ticket_subject_is_not_mistaken_for_a_token_subject(redactor: Redactor) -> None:
    assert redactor.redact({"subject": "Bill looks wrong"})["subject"] == "Bill looks wrong"
    assert str(redactor.redact({"sub": "CX-1234"})["sub"]).startswith("ref_")


def test_a_customer_gets_their_own_reference_back_in_their_own_response(
    redactor: Redactor,
) -> None:
    # A tool result that disagrees with the request the agent just made is worse than
    # useless to it, so pseudonymisation is a telemetry rule, not an output rule.
    assert redactor.redact({"cx_id": "CX-1234"}, in_logs=False) == {"cx_id": "CX-1234"}
    assert str(redactor.redact({"cx_id": "CX-1234"}, in_logs=True)["cx_id"]).startswith("ref_")
