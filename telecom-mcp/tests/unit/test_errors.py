"""The error taxonomy is part of the public contract, so it is tested like one."""

import pytest

from telecom_mcp.domain.errors import (
    SAFE_FAILURE_MESSAGE,
    AuthorizationError,
    BackendBadResponseError,
    BackendTimeoutError,
    CrossAccountAccessError,
    ErrorCode,
    InvalidInputError,
    TelecomMCPError,
    TokenExpiredError,
)


def test_envelope_never_leaks_the_internal_message() -> None:
    err = InvalidInputError(
        "cx_id 'CX-9001' does not match token subject 'CX-1234'",
        operation="get_customer_account",
    )
    envelope = err.envelope(correlation_id="corr-1")

    assert "CX-9001" not in envelope.message
    assert "CX-1234" not in envelope.message
    assert envelope.message == InvalidInputError.public_message
    assert envelope.code is ErrorCode.INVALID_INPUT
    assert envelope.operation == "get_customer_account"
    assert envelope.correlation_id == "corr-1"


def test_cross_account_denial_is_indistinguishable_from_a_plain_denial() -> None:
    # A different message would let a caller probe whether another account exists.
    assert CrossAccountAccessError.public_message == AuthorizationError.public_message


def test_backend_failure_tells_the_caller_nothing_happened() -> None:
    assert BackendTimeoutError().envelope("c").message == SAFE_FAILURE_MESSAGE


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (TokenExpiredError(), True),
        (BackendTimeoutError(), True),
        (BackendBadResponseError(), False),
        (InvalidInputError(), False),
        (CrossAccountAccessError(), False),
    ],
)
def test_retryability_is_declared_not_guessed(error: TelecomMCPError, retryable: bool) -> None:
    assert error.envelope("c").retryable is retryable


def test_envelope_serialises_to_the_documented_shape() -> None:
    payload = InvalidInputError(operation="op", details={"field": "cx_id"}).envelope("c").to_dict()

    assert payload == {
        "error": {
            "code": "invalid_input",
            "message": InvalidInputError.public_message,
            "operation": "op",
            "correlation_id": "c",
            "retryable": False,
            "details": {"field": "cx_id"},
        }
    }


def test_every_error_code_value_is_unique_and_snake_case() -> None:
    values = [str(code) for code in ErrorCode]
    assert len(values) == len(set(values))
    assert all(v.islower() and " " not in v for v in values)
