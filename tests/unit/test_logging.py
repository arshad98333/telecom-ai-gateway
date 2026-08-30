"""Logs must be machine-readable, correlated, and incapable of carrying a secret."""

import io
import json
from collections.abc import Iterator

import pytest

from telecom_mcp.observability.logging import (
    configure_logging,
    current_correlation_id,
    get_logger,
    request_context,
)
from telecom_mcp.observability.redaction import REMOVED, Redactor, derive_pseudonym_key


@pytest.fixture
def captured() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    configure_logging(
        level="DEBUG",
        service_name="telecom-mcp-tools",
        redactor=Redactor(derive_pseudonym_key("svc", "test-secret")),
        stream=stream,
    )
    yield stream


def _lines(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def test_every_line_is_json_with_a_level_and_a_utc_timestamp(captured: io.StringIO) -> None:
    get_logger("test").info("tool_call_started", tool="get_customer_account")

    (line,) = _lines(captured)
    assert line["event"] == "tool_call_started"
    assert line["level"] == "info"
    assert str(line["timestamp"]).endswith("Z")
    assert line["service"] == "telecom-mcp-tools"


def test_the_correlation_identifier_is_attached_without_the_caller_passing_it(
    captured: io.StringIO,
) -> None:
    with request_context("corr-123", case_id="case-9"):
        get_logger("test").info("tool_call_started")

    (line,) = _lines(captured)
    assert line["correlation_id"] == "corr-123"
    assert line["case_id"] == "case-9"


def test_context_is_restored_rather_than_cleared_when_a_nested_scope_ends() -> None:
    with request_context("outer"):
        with request_context("inner"):
            assert current_correlation_id() == "inner"
        assert current_correlation_id() == "outer"
    assert current_correlation_id() is None


def test_a_secret_passed_to_the_logger_never_reaches_the_stream(captured: io.StringIO) -> None:
    get_logger("test").info(
        "auth_attempt", passcode="4821", access_token="abcdef0123456789", cx_id="CX-1234"
    )

    raw = captured.getvalue()
    assert "4821" not in raw
    assert "abcdef0123456789" not in raw
    assert "CX-1234" not in raw
    (line,) = _lines(captured)
    assert line["passcode"] == REMOVED
    assert str(line["cx_id"]).startswith("ref_")


def test_a_secret_nested_inside_a_logged_structure_is_also_removed(
    captured: io.StringIO,
) -> None:
    get_logger("test").warning(
        "backend_rejected", request={"headers": {"authorization": "Bearer x"}}
    )

    assert "Bearer x" not in captured.getvalue()


def test_debug_lines_are_suppressed_when_the_level_is_higher() -> None:
    stream = io.StringIO()
    configure_logging(
        level="WARNING",
        service_name="svc",
        redactor=Redactor(derive_pseudonym_key("svc", "s")),
        stream=stream,
    )

    get_logger("test").info("not_important")
    get_logger("test").error("important")

    assert [line["event"] for line in _lines(stream)] == ["important"]
