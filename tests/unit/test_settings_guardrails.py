"""Guardrail configuration: strict by default, and impossible to loosen in production."""

from __future__ import annotations

from decimal import Decimal

import pytest

from telecom_mcp.config.settings import load_settings
from telecom_mcp.domain.errors import ConfigurationError
from telecom_mcp.guardrails.policy import GuardrailPolicy

BASE = {
    "TELECOM_MCP_LOCAL_VERIFIER_SECRET": "a-signing-secret-long-enough-for-hs256",
}

PRODUCTION = {
    "TELECOM_MCP_ENV": "production",
    "TELECOM_MCP_BACKEND": "http",
    "TELECOM_MCP_BACKEND_BASE_URL": "https://mw.example/api/v1",
    "TELECOM_MCP_IDENTITY_VERIFIER": "jwks",
    "TELECOM_MCP_JWKS_URL": "https://t.example/.well-known/jwks.json",
    "TELECOM_MCP_JWT_ISSUER": "https://t.example/",
    "TELECOM_MCP_JWT_AUDIENCE": "https://api.example/v1",
    "TELECOM_MCP_SERVICE_IDENTITY_SOURCE": "client_credentials",
    "TELECOM_MCP_SERVICE_TOKEN_URL": "https://t.example/oauth/token",
    "TELECOM_MCP_SERVICE_CLIENT_ID": "cid",
    "TELECOM_MCP_SERVICE_CLIENT_SECRET": "csecret",
    "TELECOM_MCP_IDEMPOTENCY_STORE": "redis",
    "TELECOM_MCP_REDIS_URL": "redis://cache:6379/0",
    "TELECOM_MCP_TRACING_ENABLED": "true",
    "TELECOM_MCP_TRACE_EXPORTER": "azure_monitor",
    "TELECOM_MCP_APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=abc;Ingestion=x",
}


def test_the_default_policy_is_the_strict_one() -> None:
    assert load_settings(BASE).guardrail_policy() == GuardrailPolicy.strict()


def test_every_threshold_is_settable_from_the_environment() -> None:
    settings = load_settings(
        BASE
        | {
            "TELECOM_MCP_GUARDRAIL_MAX_ARGUMENT_BYTES": "2048",
            "TELECOM_MCP_GUARDRAIL_MAX_STRING_LENGTH": "512",
            "TELECOM_MCP_GUARDRAIL_RATE_LIMIT_PER_MINUTE": "30",
            "TELECOM_MCP_GUARDRAIL_WRITE_ACTIONS_PER_CASE": "2",
            "TELECOM_MCP_GUARDRAIL_REFUND_CEILING": "2.50",
        }
    )
    policy = settings.guardrail_policy()
    assert policy.max_argument_bytes == 2_048
    assert policy.max_string_length == 512
    assert policy.rate_limit_per_minute == 30
    assert policy.write_actions_per_case == 2
    assert policy.refund_ceiling == Decimal("2.50")


def test_a_threshold_out_of_range_fails_at_startup() -> None:
    with pytest.raises(ConfigurationError):
        load_settings(BASE | {"TELECOM_MCP_GUARDRAIL_MAX_ARGUMENT_DEPTH": "0"})


def test_an_impossible_combination_fails_at_startup_not_on_the_first_request() -> None:
    with pytest.raises(ConfigurationError, match="max_string_length"):
        load_settings(
            BASE
            | {
                "TELECOM_MCP_GUARDRAIL_MAX_ARGUMENT_BYTES": "1024",
                "TELECOM_MCP_GUARDRAIL_MAX_STRING_LENGTH": "4096",
            }
        )


@pytest.mark.parametrize(
    "variable",
    [
        "TELECOM_MCP_GUARDRAILS_ENABLED",
        "TELECOM_MCP_GUARDRAIL_INJECTION_SCAN",
        "TELECOM_MCP_GUARDRAIL_OUTPUT_SECRET_SCAN",
    ],
)
def test_production_refuses_to_start_with_a_guardrail_switched_off(variable: str) -> None:
    with pytest.raises(ConfigurationError, match=variable):
        load_settings(BASE | PRODUCTION | {variable: "false"})


def test_production_starts_with_the_guardrails_on() -> None:
    settings = load_settings(BASE | PRODUCTION)
    assert settings.guardrail_policy().enabled is True


def test_guardrails_may_be_switched_off_locally() -> None:
    settings = load_settings(BASE | {"TELECOM_MCP_GUARDRAILS_ENABLED": "false"})
    assert settings.guardrail_policy().enabled is False
