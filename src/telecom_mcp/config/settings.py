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

from decimal import Decimal
from typing import Literal, Self

from pydantic import Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from telecom_mcp._version import __version__
from telecom_mcp.domain.errors import ConfigurationError
from telecom_mcp.guardrails.policy import GuardrailPolicy
from telecom_mcp.observability.tracing import Exporter, TracingConfig

Environment = Literal["local", "staging", "production"]
BackendKind = Literal["fake", "http"]
VerifierKind = Literal["local", "jwks"]
ServiceIdentitySource = Literal["static", "client_credentials"]
IdempotencyStoreKind = Literal["memory", "redis"]
AuditSinkKind = Literal["stdout", "file"]
TraceExporter = Literal["none", "otlp", "azure_monitor"]
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

    # --- This service's own credential ---
    # static             the API key below, sent unchanged. Development, or a
    #                    deployment where the credential is a long-lived secret.
    # client_credentials fetched from the identity provider and refreshed before it
    #                    expires. Required in production: an Auth0 access token lives
    #                    minutes, so a pasted one stops working almost immediately.
    service_identity_source: ServiceIdentitySource = "static"
    service_token_url: str | None = None
    service_client_id: str | None = None
    service_client_secret: SecretStr | None = None
    #: Defaults to jwt_audience: the credential is minted for the API being called.
    service_token_audience: str | None = None

    # --- Identity ---
    identity_verifier: VerifierKind = "local"
    jwks_url: str | None = None
    jwt_issuer: str | None = None
    jwt_audience: str | None = None
    local_verifier_secret: SecretStr | None = None
    jwks_cache_ttl_s: float = Field(default=600.0, ge=30, le=86400)
    #: Prefix on the custom claims that carry tenant, role and customer reference.
    #: Must match the namespace the identity provider writes and the backing API reads;
    #: three services disagreeing about this is a whole afternoon.
    claim_namespace: str = "https://telecom.example/"

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

    # --- Guardrails ---
    # Defaults match GuardrailPolicy, which is the strict posture. Every value here
    # exists so an environment can tighten it; loosening one is a deliberate act that
    # shows up in a diff of the environment, not a forgotten variable.
    guardrails_enabled: bool = True
    guardrail_max_argument_bytes: int = Field(default=8_192, ge=256, le=1_048_576)
    guardrail_max_argument_depth: int = Field(default=6, ge=1, le=32)
    guardrail_max_string_length: int = Field(default=4_096, ge=16, le=1_048_576)
    guardrail_max_array_items: int = Field(default=100, ge=1, le=10_000)
    guardrail_max_object_keys: int = Field(default=50, ge=1, le=1_000)
    guardrail_injection_scan: bool = True
    guardrail_rate_limit_per_minute: int = Field(default=120, ge=1, le=100_000)
    guardrail_rate_limit_burst: int = Field(default=30, ge=0, le=10_000)
    guardrail_write_actions_per_case: int = Field(default=5, ge=1, le=1_000)
    guardrail_action_budget_window_s: float = Field(default=3_600.0, gt=0, le=86_400)
    guardrail_callback_horizon_days: int = Field(default=90, ge=1, le=730)
    guardrail_refund_ceiling: Decimal = Field(default=Decimal("5.00"), gt=Decimal("0"))
    guardrail_max_output_bytes: int = Field(default=262_144, ge=1_024, le=8_388_608)
    guardrail_output_secret_scan: bool = True

    # --- Telemetry ---
    # Off by default so a laptop needs no collector. Production is refused without it:
    # a deployment that believes it is being traced and is not is worse than one that
    # knows it is not.
    tracing_enabled: bool = False
    trace_exporter: TraceExporter = "none"
    #: OTLP/HTTP endpoint, when trace_exporter=otlp.
    otlp_endpoint: str | None = None
    #: Application Insights connection string, when trace_exporter=azure_monitor. It
    #: contains the instrumentation key, so it is a secret and comes from Key Vault.
    applicationinsights_connection_string: SecretStr | None = None
    #: Head sampling. Parent-based, so a sampled request stays sampled downstream.
    trace_sample_ratio: float = Field(default=1.0, ge=0.0, le=1.0)

    # --- Azure ---
    # The subscription and tenant the deployment lives in. The application itself
    # authenticates with a managed identity in Azure and needs none of these at run
    # time; they are here so that a developer running against the real resources, and
    # the scripts in scripts/, read one file rather than three.
    azure_tenant_id: str | None = None
    azure_subscription_id: str | None = None
    #: Client id of the app registration or user-assigned managed identity.
    azure_client_id: str | None = None
    #: Only ever set on a developer machine. In Azure the identity is managed and there
    #: is no secret; in CI the pipeline federates through OIDC and there is no secret.
    azure_client_secret: SecretStr | None = None
    azure_key_vault_name: str | None = None

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
            if self.service_identity_source == "static" and self.backend_api_key is None:
                missing.append(
                    "TELECOM_MCP_BACKEND_API_KEY (required when BACKEND=http "
                    "and SERVICE_IDENTITY_SOURCE=static)"
                )

        if self.service_identity_source == "client_credentials":
            for name, value in (
                ("TELECOM_MCP_SERVICE_TOKEN_URL", self.service_token_url),
                ("TELECOM_MCP_SERVICE_CLIENT_ID", self.service_client_id),
                ("TELECOM_MCP_SERVICE_CLIENT_SECRET", self.service_client_secret),
            ):
                if not value:
                    missing.append(
                        f"{name} (required when SERVICE_IDENTITY_SOURCE=client_credentials)"
                    )
            if not (self.service_token_audience or self.jwt_audience):
                missing.append(
                    "TELECOM_MCP_SERVICE_TOKEN_AUDIENCE or TELECOM_MCP_JWT_AUDIENCE "
                    "(the credential must be minted for the API being called)"
                )

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

        if self.claim_namespace and not self.claim_namespace.endswith("/"):
            raise ValueError(
                "TELECOM_MCP_CLAIM_NAMESPACE must end with '/', or every claim key "
                "silently changes and no token verifies"
            )

        if self.idempotency_store == "redis" and not self.redis_url:
            missing.append("TELECOM_MCP_REDIS_URL (required when IDEMPOTENCY_STORE=redis)")

        if self.tracing_enabled:
            if self.trace_exporter == "otlp" and not self.otlp_endpoint:
                missing.append("TELECOM_MCP_OTLP_ENDPOINT (required when TRACE_EXPORTER=otlp)")
            if (
                self.trace_exporter == "azure_monitor"
                and self.applicationinsights_connection_string is None
            ):
                missing.append(
                    "TELECOM_MCP_APPLICATIONINSIGHTS_CONNECTION_STRING "
                    "(required when TRACE_EXPORTER=azure_monitor)"
                )
            if self.trace_exporter == "none":
                missing.append(
                    "TELECOM_MCP_TRACE_EXPORTER (tracing is enabled but no exporter is "
                    "configured, so every span would be built and thrown away)"
                )

        if missing:
            raise ValueError("missing required configuration:\n  - " + "\n  - ".join(missing))

        # Production must not run on the developer conveniences.
        if self.env == "production":
            unsafe: list[str] = []
            if self.backend != "http":
                unsafe.append("TELECOM_MCP_BACKEND must be 'http' in production")
            if self.identity_verifier != "jwks":
                unsafe.append("TELECOM_MCP_IDENTITY_VERIFIER must be 'jwks' in production")
            if self.service_identity_source != "client_credentials":
                unsafe.append(
                    "TELECOM_MCP_SERVICE_IDENTITY_SOURCE must be 'client_credentials' in "
                    "production; a pasted access token expires in minutes and a "
                    "long-lived shared secret cannot be rotated without a restart"
                )
            if self.idempotency_store != "redis":
                unsafe.append(
                    "TELECOM_MCP_IDEMPOTENCY_STORE must be 'redis' in production, "
                    "because an in-memory store cannot deduplicate across replicas"
                )
            if not self.guardrails_enabled:
                unsafe.append(
                    "TELECOM_MCP_GUARDRAILS_ENABLED must be true in production; the "
                    "switch exists for a developer reproducing a payload, not for a "
                    "deployment"
                )
            if not self.guardrail_injection_scan:
                unsafe.append("TELECOM_MCP_GUARDRAIL_INJECTION_SCAN must be true in production")
            if not self.guardrail_output_secret_scan:
                unsafe.append(
                    "TELECOM_MCP_GUARDRAIL_OUTPUT_SECRET_SCAN must be true in production"
                )
            if not self.tracing_enabled:
                unsafe.append(
                    "TELECOM_MCP_TRACING_ENABLED must be true in production; an "
                    "incident without traces is an incident investigated by guessing"
                )
            if unsafe:
                raise ValueError("unsafe production configuration:\n  - " + "\n  - ".join(unsafe))

        # Building the policy validates it; a bad threshold must fail here rather than
        # on the first request that happens to trip it.
        self.guardrail_policy()

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

    def tracing_config(self) -> TracingConfig:
        """The tracing configuration, with the secret unwrapped exactly once."""
        return TracingConfig(
            enabled=self.tracing_enabled,
            exporter=Exporter(self.trace_exporter),
            service_name=self.service_name,
            service_version=__version__,
            environment=self.env,
            endpoint=self.otlp_endpoint,
            connection_string=(
                self.applicationinsights_connection_string.get_secret_value()
                if self.applicationinsights_connection_string is not None
                else None
            ),
            sample_ratio=self.trace_sample_ratio,
        )

    def guardrail_policy(self) -> GuardrailPolicy:
        """The policy object the guardrail pipeline runs on.

        Built here rather than in the composition root so that the policy is validated
        by the same startup that validates everything else. A threshold that cannot be
        satisfied fails before the first request, not on the request that trips it.
        """
        return GuardrailPolicy(
            enabled=self.guardrails_enabled,
            max_argument_bytes=self.guardrail_max_argument_bytes,
            max_argument_depth=self.guardrail_max_argument_depth,
            max_string_length=self.guardrail_max_string_length,
            max_array_items=self.guardrail_max_array_items,
            max_object_keys=self.guardrail_max_object_keys,
            injection_scan=self.guardrail_injection_scan,
            rate_limit_per_minute=self.guardrail_rate_limit_per_minute,
            rate_limit_burst=self.guardrail_rate_limit_burst,
            write_actions_per_case=self.guardrail_write_actions_per_case,
            action_budget_window_s=self.guardrail_action_budget_window_s,
            callback_horizon_days=self.guardrail_callback_horizon_days,
            refund_ceiling=self.guardrail_refund_ceiling,
            max_output_bytes=self.guardrail_max_output_bytes,
            output_secret_scan=self.guardrail_output_secret_scan,
        )

    @property
    def effective_service_token_audience(self) -> str | None:
        """The audience the service credential is minted for."""
        return self.service_token_audience or self.jwt_audience

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
