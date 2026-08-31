"""Telemetry configuration: off by default, mandatory in production, never guessed."""

from __future__ import annotations

import pytest

from telecom_mcp.config.settings import load_settings
from telecom_mcp.domain.errors import ConfigurationError
from telecom_mcp.observability.tracing import Exporter

BASE = {"TELECOM_MCP_LOCAL_VERIFIER_SECRET": "a-signing-secret-long-enough-for-hs256"}

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
    "TELECOM_MCP_APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=abc;IngestionEndpoint=https://x/",
}


def test_tracing_is_off_by_default() -> None:
    config = load_settings(BASE).tracing_config()
    assert config.enabled is False
    assert config.exporter is Exporter.NONE


def test_tracing_on_with_no_exporter_is_refused_rather_than_silently_wasted() -> None:
    with pytest.raises(ConfigurationError, match="TRACE_EXPORTER"):
        load_settings(BASE | {"TELECOM_MCP_TRACING_ENABLED": "true"})


def test_otlp_needs_an_endpoint() -> None:
    with pytest.raises(ConfigurationError, match="OTLP_ENDPOINT"):
        load_settings(
            BASE | {"TELECOM_MCP_TRACING_ENABLED": "true", "TELECOM_MCP_TRACE_EXPORTER": "otlp"}
        )


def test_azure_monitor_needs_a_connection_string() -> None:
    with pytest.raises(ConfigurationError, match="APPLICATIONINSIGHTS"):
        load_settings(
            BASE
            | {
                "TELECOM_MCP_TRACING_ENABLED": "true",
                "TELECOM_MCP_TRACE_EXPORTER": "azure_monitor",
            }
        )


def test_production_refuses_to_start_untraced() -> None:
    environment = dict(BASE | PRODUCTION)
    environment["TELECOM_MCP_TRACING_ENABLED"] = "false"
    environment["TELECOM_MCP_TRACE_EXPORTER"] = "none"
    with pytest.raises(ConfigurationError, match="TRACING_ENABLED"):
        load_settings(environment)


def test_a_production_configuration_produces_an_azure_monitor_tracer_config() -> None:
    config = load_settings(BASE | PRODUCTION).tracing_config()
    assert config.enabled is True
    assert config.exporter is Exporter.AZURE_MONITOR
    assert config.environment == "production"
    assert config.connection_string is not None


def test_the_connection_string_is_never_in_the_described_settings() -> None:
    settings = load_settings(BASE | PRODUCTION)
    described = settings.describe()
    assert described["applicationinsights_connection_string"] == "***redacted***"
    assert "InstrumentationKey" not in str(described)


def test_the_azure_client_secret_is_a_secret() -> None:
    settings = load_settings(BASE | {"TELECOM_MCP_AZURE_CLIENT_SECRET": "not-in-a-log"})
    assert settings.describe()["azure_client_secret"] == "***redacted***"


def test_the_azure_identifiers_default_to_absent() -> None:
    settings = load_settings(BASE)
    assert settings.azure_tenant_id is None
    assert settings.azure_subscription_id is None
    assert settings.azure_key_vault_name is None


def test_the_sample_ratio_is_bounded() -> None:
    with pytest.raises(ConfigurationError):
        load_settings(BASE | {"TELECOM_MCP_TRACE_SAMPLE_RATIO": "1.5"})
