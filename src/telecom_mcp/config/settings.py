"""All configuration, loaded once, validated once, in one place.

Rules enforced here:

* every setting comes from the environment, nothing is hardcoded elsewhere;
* validation happens at startup and fails loudly, naming every problem at once
  rather than one per restart;
* secrets are ``SecretStr`` so printing the settings object cannot leak them;
* defaults exist for everything that is not a secret, so a developer sets as
  little as possible, and the safe default (``fake`` backend, ``local`` verifier)
  is the one that needs no network and no account.

The same object is used by the stdio entry point, the HTTP entry point and any
worker, so none of them can start with configuration the others rejected.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from telecom_mcp.domain.errors import ConfigurationError

Environment = Literal["local", "staging", "production"]
BackendKind = Literal["fake", "http"]
VerifierKind = Literal["local", "jwks"]
IdempotencyStoreKind = Literal["memory", "redis"]
AuditSinkKind = Literal["stdout", "file"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    """The complete configuration surface. Every field appears in .env.example."""

    model_config = SettingsConfigDict(
        env_prefix="TELECOM_MCP_",
        env_file=None,  # the process environment is the only source; no per-developer files
        extra="forbid",
        frozen=True,
    )

    # --- Runtime ---
    env: Environment = "local"
    log_level: LogLevel = "INFO"
    service_name: str = "telecom-mcp-tools"

    # --- HTTP transport ---
    http_host: str = "127.0.0.1"
    http_port: int = Field(default=8080, ge=1, le=65535)

    # --- Telecom middleware API ---
    backend: BackendKind = "fake"
    backend_base_url: str | None = None
    backend_api_key: SecretStr | None = None
    backend_connect_timeout_s: float = Field(default=2.0, gt=0, le=30)
    backend_read_timeout_s: float = Field(default=8.0, gt=0, le=60)
    backend_max_connections: int = Field(default=50, ge=1, le=1000)

    # --- Identity ---
    identity_verifier: VerifierKind = "local"
    jwks_url: str | None = None
    jwt_issuer: str | None = None
    jwt_audience: str | None = None
    local_verifier_secret: SecretStr | None = None
    jwks_cache_ttl_s: float = Field(default=600.0, ge=30, le=86400)

    # --- Reliability ---
    tool_timeout_s: float = Field(default=10.0, gt=0, le=60)
    retry_attempts: int = Field(default=2, ge=0, le=5)
    retry_base_delay_s: float = Field(default=0.2, gt=0, le=5)
    breaker_failure_threshold: int = Field(default=5, ge=1, le=100)
    breaker_reset_timeout_s: float = Field(default=30.0, gt=0, le=600)
    max_concurrent_tool_calls: int = Field(default=100, ge=1, le=10_000)

    # --- Idempotency ---
    idempotency_store: IdempotencyStoreKind = "memory"
    idempotency_ttl_s: int = Field(default=86_400, ge=60, le=604_800)
    redis_url: str | None = None

    # --- Audit ---
    audit_sink: AuditSinkKind = "stdout"
    audit_file_path: str = "./audit.log"

    @model_validator(mode="after")
    def _check_dependent_settings(self) -> Self:
        """Cross-field rules. A choice that needs companions must declare them."""
        missing: list[str] = []

        if self.backend == "http":
            if not self.backend_base_url:
                missing.append("TELECOM_MCP_BACKEND_BASE_URL (required when BACKEND=http)")
            if self.backend_api_key is None:
                missing.append("TELECOM_MCP_BACKEND_API_KEY (required when BACKEND=http)")

        if self.identity_verifier == "jwks":
            if not self.jwks_url:
                missing.append("TELECOM_MCP_JWKS_URL (required when IDENTITY_VERIFIER=jwks)")
            if not self.jwt_issuer:
                missing.append("TELECOM_MCP_JWT_ISSUER (required when IDENTITY_VERIFIER=jwks)")
            if not self.jwt_audience:
                missing.append("TELECOM_MCP_JWT_AUDIENCE (required when IDENTITY_VERIFIER=jwks)")
        elif self.local_verifier_secret is None:
            missing.append(
                "TELECOM_MCP_LOCAL_VERIFIER_SECRET (required when IDENTITY_VERIFIER=local)"
            )

        if self.idempotency_store == "redis" and not self.redis_url:
            missing.append("TELECOM_MCP_REDIS_URL (required when IDEMPOTENCY_STORE=redis)")

        if missing:
            raise ValueError("missing required configuration:\n  - " + "\n  - ".join(missing))

        # Production must not run on the developer conveniences.
        if self.env == "production":
            unsafe: list[str] = []
            if self.backend != "http":
                unsafe.append("TELECOM_MCP_BACKEND must be 'http' in production")
            if self.identity_verifier != "jwks":
                unsafe.append("TELECOM_MCP_IDENTITY_VERIFIER must be 'jwks' in production")
            if self.idempotency_store != "redis":
                unsafe.append(
                    "TELECOM_MCP_IDEMPOTENCY_STORE must be 'redis' in production, "
                    "because an in-memory store cannot deduplicate across replicas"
                )
            if unsafe:
                raise ValueError("unsafe production configuration:\n  - " + "\n  - ".join(unsafe))

        # A single attempt must fit inside the total budget, or the budget is a lie.
        attempts = self.retry_attempts + 1
        single_attempt = self.backend_connect_timeout_s + self.backend_read_timeout_s
        if single_attempt > self.tool_timeout_s:
            raise ValueError(
                "timeout budget is impossible: one attempt costs up to "
                f"{single_attempt:.1f}s but TELECOM_MCP_TOOL_TIMEOUT_S is "
                f"{self.tool_timeout_s:.1f}s (attempts configured: {attempts})"
            )
        return self

    def describe(self) -> dict[str, object]:
        """Loggable view of the settings. Secrets are replaced, never masked in place."""
        data = self.model_dump()
        for name in list(data):
            if isinstance(getattr(self, name), SecretStr):
                data[name] = "***redacted***"
        return data


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    """Load and validate settings, or fail with one message naming every problem.

    Raises:
        ConfigurationError: with a human-readable list of everything wrong. The caller
            at the process boundary prints it and exits non-zero; nothing starts halfway.
    """
    try:
        if environ is None:
            return Settings()
        return Settings.model_validate(_from_environ(environ))
    except ValidationError as exc:
        raise ConfigurationError(_format_validation_error(exc), operation="load_settings") from exc
    except ValueError as exc:
        raise ConfigurationError(str(exc), operation="load_settings") from exc


def _from_environ(environ: dict[str, str]) -> dict[str, str]:
    prefix = "TELECOM_MCP_"
    return {
        key.removeprefix(prefix).lower(): value
        for key, value in environ.items()
        if key.startswith(prefix)
    }


def _format_validation_error(exc: ValidationError) -> str:
    lines = []
    for error in exc.errors():
        field = ".".join(str(part) for part in error["loc"])
        message = error["msg"].removeprefix("Value error, ")
        # Model-level rules already name their own variables; field errors do not.
        lines.append(f"TELECOM_MCP_{field.upper()}: {message}" if field else message)
    return "invalid configuration:\n  - " + "\n  - ".join(lines)
