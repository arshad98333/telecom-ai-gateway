"""Input validation happens once, at the edge. These tests are that edge."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from telecom_mcp.domain.schemas import (
    MAX_PAGE_SIZE,
    CreateSupportTicketInput,
    GetActiveServicesInput,
    GetCustomerAccountInput,
    RequestRefundApprovalInput,
    ScheduleCallbackInput,
)


def test_the_normal_case_validates() -> None:
    assert GetCustomerAccountInput(cx_id="CX-1234").cx_id == "CX-1234"


@pytest.mark.parametrize(
    "cx_id",
    [
        "",  # empty
        "CX",  # one under the minimum length
        "C" * 33,  # one over the maximum length
        "CX 1234",  # space
        "CX-1234; drop table",  # attempted injection
        "CX/../1234",  # traversal shape
        "CX-12\n34",  # embedded control character
        "CX-‮1234",  # right-to-left override, a classic display spoof
    ],
)
def test_a_malformed_customer_reference_is_rejected(cx_id: str) -> None:
    with pytest.raises(ValidationError):
        GetCustomerAccountInput(cx_id=cx_id)


def test_surrounding_whitespace_is_stripped_rather_than_rejected() -> None:
    # Voice transcription and copy-paste both add whitespace; stripping it is kinder
    # than a validation error, and the stripped value still has to pass the pattern.
    assert GetCustomerAccountInput(cx_id="  CX-1234\n").cx_id == "CX-1234"


def test_a_boundary_length_reference_is_accepted() -> None:
    assert GetCustomerAccountInput(cx_id="C" * 32)
    assert GetCustomerAccountInput(cx_id="CX1")


def test_unknown_fields_are_rejected_rather_than_ignored() -> None:
    # Silently ignoring an unknown field is how a caller believes it set something.
    with pytest.raises(ValidationError):
        GetCustomerAccountInput(cx_id="CX-1234", admin=True)


def test_a_caller_cannot_request_an_unbounded_page() -> None:
    assert GetActiveServicesInput(cx_id="CX-1", limit=MAX_PAGE_SIZE).limit == MAX_PAGE_SIZE
    with pytest.raises(ValidationError):
        GetActiveServicesInput(cx_id="CX-1", limit=MAX_PAGE_SIZE + 1)
    with pytest.raises(ValidationError):
        GetActiveServicesInput(cx_id="CX-1", limit=0)


def test_inputs_are_immutable_once_validated() -> None:
    validated = GetCustomerAccountInput(cx_id="CX-1234")
    with pytest.raises(ValidationError):
        validated.cx_id = "CX-9999"


def test_text_with_other_scripts_and_emoji_is_accepted_and_counted_in_characters() -> None:
    ticket = CreateSupportTicketInput(
        cx_id="CX-1234",
        category="billing",
        subject="बिल में गड़बड़ी 📶",
        description="Le montant facturé est incorrect.",
        idempotency_key="idem-0000-0001",
    )

    assert ticket.subject.startswith("बिल")


def test_a_description_one_character_over_the_limit_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CreateSupportTicketInput(
            cx_id="CX-1234",
            category="billing",
            subject="s",
            description="x" * 2001,
            idempotency_key="idem-0000-0001",
        )


def test_a_write_without_an_idempotency_key_cannot_even_be_constructed() -> None:
    with pytest.raises(ValidationError):
        CreateSupportTicketInput(cx_id="CX-1234", category="billing", subject="s", description="d")


def test_a_naive_datetime_is_rejected_because_it_means_nothing_without_a_zone() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        ScheduleCallbackInput(
            cx_id="CX-1234",
            preferred_date=datetime(2026, 9, 1, 10, 0),  # noqa: DTZ001 - deliberately naive
            window="morning",
            reason="Discuss the bill",
            idempotency_key="idem-0000-0002",
        )


def test_an_aware_datetime_is_accepted() -> None:
    assert ScheduleCallbackInput(
        cx_id="CX-1234",
        preferred_date=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
        window="morning",
        reason="Discuss the bill",
        idempotency_key="idem-0000-0002",
    )


def test_money_is_decimal_and_never_float() -> None:
    request = RequestRefundApprovalInput(
        cx_id="CX-1234",
        invoice_id="INV-1",
        amount="4.99",
        currency="GBP",
        reason="billing_error",
        justification="Charged twice for the same month.",
        idempotency_key="idem-0000-0003",
    )

    assert request.amount == Decimal("4.99")
    assert isinstance(request.amount, Decimal)


@pytest.mark.parametrize("amount", ["0", "-1.00", "5.01", "4.999"])
def test_amounts_outside_the_autonomous_limit_or_precision_are_rejected(amount: str) -> None:
    with pytest.raises(ValidationError):
        RequestRefundApprovalInput(
            cx_id="CX-1234",
            invoice_id="INV-1",
            amount=amount,
            currency="GBP",
            reason="billing_error",
            justification="j",
            idempotency_key="idem-0000-0003",
        )


def test_exactly_the_maximum_amount_is_allowed() -> None:
    request = RequestRefundApprovalInput(
        cx_id="CX-1234",
        invoice_id="INV-1",
        amount="5.00",
        currency="GBP",
        reason="billing_error",
        justification="j",
        idempotency_key="idem-0000-0003",
    )

    assert request.amount == Decimal("5.00")


def test_an_enumerated_field_rejects_free_text() -> None:
    with pytest.raises(ValidationError):
        CreateSupportTicketInput(
            cx_id="CX-1234",
            category="anything the model felt like",
            subject="s",
            description="d",
            idempotency_key="idem-0000-0001",
        )


@pytest.mark.parametrize("key", ["short", "with space", "x" * 129, "semi;colon"])
def test_a_malformed_idempotency_key_is_rejected(key: str) -> None:
    with pytest.raises(ValidationError):
        CreateSupportTicketInput(
            cx_id="CX-1234",
            category="billing",
            subject="s",
            description="d",
            idempotency_key=key,
        )
