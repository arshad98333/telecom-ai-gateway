"""A policy that validates itself, so a bad threshold fails at startup not at 3am."""

from __future__ import annotations

import pytest

from telecom_mcp.domain.errors import ConfigurationError
from telecom_mcp.guardrails.policy import GuardrailPolicy


def test_the_defaults_are_the_production_defaults() -> None:
    policy = GuardrailPolicy()
    assert policy.enabled is True
    assert policy.injection_scan is True
    assert policy.output_secret_scan is True
    assert policy == GuardrailPolicy.strict()


@pytest.mark.parametrize(
    "field",
    [
        "max_argument_bytes",
        "max_argument_depth",
        "max_string_length",
        "max_array_items",
        "max_object_keys",
        "rate_limit_per_minute",
        "write_actions_per_case",
        "max_output_bytes",
    ],
)
def test_a_non_positive_limit_is_refused(field: str) -> None:
    with pytest.raises(ConfigurationError, match="must be positive"):
        GuardrailPolicy(**{field: 0})


def test_a_string_limit_above_the_payload_limit_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="max_string_length"):
        GuardrailPolicy(max_argument_bytes=1_024, max_string_length=2_048)


def test_a_negative_burst_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="rate_limit_burst"):
        GuardrailPolicy(rate_limit_burst=-1)


def test_every_problem_is_reported_at_once() -> None:
    with pytest.raises(ConfigurationError) as caught:
        GuardrailPolicy(max_argument_depth=0, max_array_items=-3)
    message = str(caught.value)
    assert "max_argument_depth" in message
    assert "max_array_items" in message


def test_tightening_revalidates() -> None:
    policy = GuardrailPolicy()
    assert policy.tightened(max_argument_bytes=1_024, max_string_length=512).max_argument_bytes == 1_024
    with pytest.raises(ConfigurationError):
        policy.tightened(max_array_items=0)


def test_the_disabled_policy_turns_the_scans_off() -> None:
    policy = GuardrailPolicy.disabled()
    assert policy.enabled is False
    assert policy.injection_scan is False
    assert policy.output_secret_scan is False


def test_the_description_covers_every_field() -> None:
    described = GuardrailPolicy().describe()
    assert set(described) == set(GuardrailPolicy.__slots__)
