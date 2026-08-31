"""Configuration fails loudly, at startup, naming every problem at once."""

import pytest

from telecom_middleware.config.settings import Settings, load_settings
from telecom_middleware.domain.errors import ConfigurationError, ErrorCode

LOCAL = {"TELECOM_MW_LOCAL_VERIFIER_SECRET": "dummy-secret-long-enough-for-hs256"}

PRODUCTION = {
    "TELECOM_MW_ENV": "production",
    "TELECOM_MW_STORE": "mongodb",
    "TELECOM_MW_MONGODB_URI": "mongodb://mongo:27017/?replicaSet=rs0",
    "TELECOM_MW_IDENTITY_VERIFIER": "jwks",
    "TELECOM_MW_JWKS_URL": "https://tenant.example.invalid/.well-known/jwks.json",
    "TELECOM_MW_JWT_ISSUER": "https://tenant.example.invalid/",
    "TELECOM_MW_JWT_AUDIENCE": "https://api.telecom.example/v1",
    # Production must also prove which service is calling, not only which person.
    "TELECOM_MW_SERVICE_AUTH": "jwks",
    "TELECOM_MW_SERVICE_ALLOWED_CLIENT_IDS": "mcp-tool-server-client-id",
}


def test_the_safe_defaults_need_no_database_and_no_identity_provider() -> None:
    settings = load_settings(LOCAL)

    assert settings.store == "memory"
    assert settings.identity_verifier == "local"


def test_an_empty_environment_names_the_missing_variable() -> None:
    with pytest.raises(ConfigurationError) as caught:
        load_settings({})

    assert "TELECOM_MW_LOCAL_VERIFIER_SECRET" in str(caught.value)
    assert caught.value.code is ErrorCode.CONFIGURATION_ERROR


def test_every_missing_variable_is_reported_at_once() -> None:
    with pytest.raises(ConfigurationError) as caught:
        load_settings({"TELECOM_MW_IDENTITY_VERIFIER": "jwks", "TELECOM_MW_STORE": "mongodb"})

    message = str(caught.value)
    for variable in (
        "TELECOM_MW_MONGODB_URI",
        "TELECOM_MW_JWKS_URL",
        "TELECOM_MW_JWT_ISSUER",
        "TELECOM_MW_JWT_AUDIENCE",
    ):
        assert variable in message


def test_production_refuses_the_in_memory_store_and_the_local_verifier() -> None:
    env = dict(
        PRODUCTION,
        TELECOM_MW_STORE="memory",
        TELECOM_MW_IDENTITY_VERIFIER="local",
        TELECOM_MW_LOCAL_VERIFIER_SECRET="dummy-secret-long-enough-for-hs256",
    )

    with pytest.raises(ConfigurationError) as caught:
        load_settings(env)

    assert "loses every write on restart" in str(caught.value)
    assert "anyone with the environment can forge" in str(caught.value)


def test_a_valid_production_environment_loads() -> None:
    settings = load_settings(PRODUCTION)

    assert settings.env == "production"
    assert settings.mongodb_uri is not None
    assert "replicaSet" in settings.mongodb_uri.get_secret_value()


def test_a_claim_namespace_without_a_trailing_slash_is_rejected() -> None:
    # Auth0 namespaced claims are URLs; a missing slash silently changes every claim key.
    with pytest.raises(ConfigurationError, match="must end with"):
        load_settings(dict(LOCAL, TELECOM_MW_CLAIM_NAMESPACE="https://telecom.example"))


@pytest.mark.parametrize(
    "override",
    [
        {"TELECOM_MW_HTTP_PORT": "0"},
        {"TELECOM_MW_HTTP_PORT": "70000"},
        {"TELECOM_MW_LOG_LEVEL": "TRACE"},
        {"TELECOM_MW_STORE": "postgres"},
        {"TELECOM_MW_PASSCODE_MAX_ATTEMPTS": "0"},
        {"TELECOM_MW_SSE_HEARTBEAT_S": "0"},
        {"TELECOM_MW_AUDIT_RETENTION_DAYS": "5"},
        {"TELECOM_MW_TYPO": "1"},
    ],
)
def test_out_of_range_and_unknown_values_fail_loudly(override: dict[str, str]) -> None:
    with pytest.raises(ConfigurationError):
        load_settings(dict(LOCAL, **override))


def test_the_mongodb_uri_is_a_secret_and_cannot_be_printed() -> None:
    # A connection string carries credentials; it must not appear in a settings dump.
    settings = load_settings(PRODUCTION)

    assert settings.describe()["mongodb_uri"] == "***redacted***"
    assert "mongo:27017" not in repr(settings)


def test_settings_are_frozen() -> None:
    settings = load_settings(LOCAL)

    with pytest.raises(Exception, match=r"frozen|immutable"):
        settings.store = "mongodb"


def test_the_example_environment_file_documents_every_setting() -> None:
    from pathlib import Path

    example = Path(__file__).resolve().parents[2] / ".env.example"
    documented = {
        line.split("=", 1)[0].strip()
        for line in example.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#") and "=" in line
    }
    expected = {f"TELECOM_MW_{name.upper()}" for name in Settings.model_fields}

    assert expected - documented == set(), "settings missing from .env.example"
    assert documented - expected == set(), "stale entries in .env.example"
