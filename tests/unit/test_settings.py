"""Configuration is the most common source of production incidents, so it is tested hard."""

import pytest

from telecom_mcp.config.settings import Settings, load_settings
from telecom_mcp.domain.errors import ConfigurationError, ErrorCode

LOCAL_ENV = {
    "TELECOM_MCP_LOCAL_VERIFIER_SECRET": "dummy-secret",
}

PRODUCTION_ENV = {
    "TELECOM_MCP_ENV": "production",
    "TELECOM_MCP_BACKEND": "http",
    "TELECOM_MCP_BACKEND_BASE_URL": "https://middleware.example.invalid/api/v1",
    "TELECOM_MCP_BACKEND_API_KEY": "dummy-key",
    "TELECOM_MCP_IDENTITY_VERIFIER": "jwks",
    "TELECOM_MCP_JWKS_URL": "https://tenant.example.invalid/.well-known/jwks.json",
    "TELECOM_MCP_JWT_ISSUER": "https://tenant.example.invalid/",
    "TELECOM_MCP_JWT_AUDIENCE": "telecom-mcp-tools",
    "TELECOM_MCP_IDEMPOTENCY_STORE": "redis",
    "TELECOM_MCP_REDIS_URL": "redis://localhost:6379/0",
}


def test_the_safe_defaults_need_no_network_and_no_account() -> None:
    settings = load_settings(LOCAL_ENV)

    assert settings.backend == "fake"
    assert settings.identity_verifier == "local"
    assert settings.idempotency_store == "memory"


def test_an_empty_environment_names_the_missing_variable() -> None:
    with pytest.raises(ConfigurationError) as caught:
        load_settings({})

    assert "TELECOM_MCP_LOCAL_VERIFIER_SECRET" in str(caught.value)
    assert caught.value.code is ErrorCode.CONFIGURATION_ERROR


def test_every_missing_variable_is_reported_at_once_not_one_per_restart() -> None:
    with pytest.raises(ConfigurationError) as caught:
        load_settings({"TELECOM_MCP_IDENTITY_VERIFIER": "jwks", "TELECOM_MCP_BACKEND": "http"})

    message = str(caught.value)
    for variable in (
        "TELECOM_MCP_BACKEND_BASE_URL",
        "TELECOM_MCP_BACKEND_API_KEY",
        "TELECOM_MCP_JWKS_URL",
        "TELECOM_MCP_JWT_ISSUER",
        "TELECOM_MCP_JWT_AUDIENCE",
    ):
        assert variable in message


def test_production_refuses_the_developer_conveniences() -> None:
    env = dict(
        PRODUCTION_ENV,
        TELECOM_MCP_BACKEND="fake",
        TELECOM_MCP_IDENTITY_VERIFIER="local",
        TELECOM_MCP_IDEMPOTENCY_STORE="memory",
        TELECOM_MCP_LOCAL_VERIFIER_SECRET="dummy-secret",
    )

    with pytest.raises(ConfigurationError) as caught:
        load_settings(env)

    assert "must be 'http' in production" in str(caught.value)
    assert "must be 'jwks' in production" in str(caught.value)
    assert "cannot deduplicate across replicas" in str(caught.value)


def test_a_valid_production_environment_loads() -> None:
    settings = load_settings(PRODUCTION_ENV)

    assert settings.env == "production"
    assert settings.backend_api_key is not None
    assert settings.backend_api_key.get_secret_value() == "dummy-key"


def test_an_impossible_timeout_budget_is_rejected_at_startup() -> None:
    env = dict(
        LOCAL_ENV,
        TELECOM_MCP_TOOL_TIMEOUT_S="3",
        TELECOM_MCP_BACKEND_CONNECT_TIMEOUT_S="2",
        TELECOM_MCP_BACKEND_READ_TIMEOUT_S="8",
    )

    with pytest.raises(ConfigurationError, match="timeout budget is impossible"):
        load_settings(env)


def test_an_unknown_variable_is_rejected_rather_than_silently_ignored() -> None:
    with pytest.raises(ConfigurationError):
        load_settings(dict(LOCAL_ENV, TELECOM_MCP_TYPO_SETTING="1"))


@pytest.mark.parametrize(
    "override",
    [
        {"TELECOM_MCP_HTTP_PORT": "0"},
        {"TELECOM_MCP_HTTP_PORT": "70000"},
        {"TELECOM_MCP_RETRY_ATTEMPTS": "-1"},
        {"TELECOM_MCP_LOG_LEVEL": "TRACE"},
        {"TELECOM_MCP_ENV": "prod"},
        {"TELECOM_MCP_TOOL_TIMEOUT_S": "0"},
        {"TELECOM_MCP_IDEMPOTENCY_TTL_S": "30"},
    ],
)
def test_out_of_range_and_misspelled_values_fail_loudly(override: dict[str, str]) -> None:
    with pytest.raises(ConfigurationError):
        load_settings(dict(LOCAL_ENV, **override))


def test_boolean_like_and_numeric_strings_are_parsed_as_their_real_type() -> None:
    settings = load_settings(dict(LOCAL_ENV, TELECOM_MCP_RETRY_ATTEMPTS="0"))

    assert settings.retry_attempts == 0
    assert isinstance(settings.retry_attempts, int)


def test_describing_the_settings_cannot_leak_a_secret() -> None:
    settings = load_settings(PRODUCTION_ENV)
    described = settings.describe()

    assert described["backend_api_key"] == "***redacted***"
    assert "dummy-key" not in repr(settings)
    assert "dummy-key" not in str(described)


def test_settings_are_frozen_so_nothing_can_mutate_them_at_runtime() -> None:
    settings = load_settings(LOCAL_ENV)

    with pytest.raises(Exception, match=r"frozen|immutable"):
        settings.backend = "http"  # type: ignore[misc]


def test_the_example_environment_file_documents_every_setting() -> None:
    # A variable added to the code but not to .env.example works locally and fails
    # on every other machine. This test makes that impossible.
    from pathlib import Path

    example = Path(__file__).resolve().parents[2] / ".env.example"
    text = example.read_text(encoding="utf-8")
    documented = {
        line.split("=", 1)[0].strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#") and "=" in line
    }
    expected = {f"TELECOM_MCP_{name.upper()}" for name in Settings.model_fields}

    assert expected - documented == set(), "settings missing from .env.example"
    assert documented - expected == set(), "stale entries in .env.example"
