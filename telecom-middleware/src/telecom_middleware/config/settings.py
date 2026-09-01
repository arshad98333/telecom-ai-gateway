"""All configuration, loaded once, validated once, in one place.

Same rules as the tools package: everything comes from the environment, validation
happens at startup and names every problem at once, secrets are ``SecretStr``, and the
safe default (in-memory store, local verifier) is the one that needs no database and no
identity provider. Production refuses both.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from telecom_middleware.domain.errors import ConfigurationError

Environment = Literal["local", "staging", "production"]
StoreKind = Literal["memory", "mongodb"]
VerifierKind = Literal["local", "jwks"]
ServiceAuthKind = Literal["unchecked", "shared_secret", "jwks"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    """The complete configuration surface. Every field appears in .env.example."""

    model_config = SettingsConfigDict(
        env_prefix="TELECOM_MW_", env_file=None, extra="forbid", frozen=True
    )

    # --- Runtime ---
    env: Environment = "local"
    log_level: LogLevel = "INFO"
    service_name: str = "telecom-middleware"
    http_host: str = "127.0.0.1"
    http_port: int = Field(default=9000, ge=1, le=65535)

    # --- Storage ---
    store: StoreKind = "memory"
    mongodb_uri: SecretStr | None = None
    mongodb_database: str = "telecom"
    mongodb_max_pool_size: int = Field(default=100, ge=1, le=1000)
    mongodb_timeout_ms: int = Field(default=5000, ge=100, le=60_000)

    # --- Identity ---
    identity_verifier: VerifierKind = "local"
    jwks_url: str | None = None
    jwt_issuer: str | None = None
    jwt_audience: str | None = None
    local_verifier_secret: SecretStr | None = None
    jwks_cache_ttl_s: float = Field(default=600.0, ge=30, le=86_400)
    claim_namespace: str = "https://telecom.example/"

    # --- Service identity ---
    # Which *service* may call, as distinct from which person. See
    # security/service_credential.py for what each mode checks.
    service_auth: ServiceAuthKind = "unchecked"
    service_shared_secret: SecretStr | None = None
    #: Comma-separated Auth0 client ids permitted to call, when service_auth=jwks.
    service_allowed_client_ids: str = ""

    # --- Passcode policy ---
    passcode_max_attempts: int = Field(default=5, ge=1, le=20)
    passcode_lockout_s: int = Field(default=900, ge=60, le=86_400)

    # --- Realtime ---
    change_stream_enabled: bool = True
    sse_heartbeat_s: float = Field(default=15.0, ge=1, le=120)
    sse_max_subscribers: int = Field(default=500, ge=1, le=10_000)
    outbox_batch_size: int = Field(default=100, ge=1, le=1000)
    outbox_poll_interval_s: float = Field(default=1.0, ge=0.05, le=60)

    # --- Data protection ---
    case_retention_days: int = Field(default=90, ge=1, le=3650)
    audit_retention_days: int = Field(default=2555, ge=30, le=36_500)
    idempotency_ttl_s: int = Field(default=86_400, ge=60, le=604_800)

    @model_validator(mode="after")
    def _check_dependent_settings(self) -> Self:
        missing: list[str] = []

        if self.store == "mongodb" and self.mongodb_uri is None:
            missing.append("TELECOM_MW_MONGODB_URI (required when STORE=mongodb)")

        if self.identity_verifier == "jwks":
            for name, value in (
                ("TELECOM_MW_JWKS_URL", self.jwks_url),
                ("TELECOM_MW_JWT_ISSUER", self.jwt_issuer),
                ("TELECOM_MW_JWT_AUDIENCE", self.jwt_audience),
            ):
                if not value:
                    missing.append(f"{name} (required when IDENTITY_VERIFIER=jwks)")
        elif self.local_verifier_secret is None:
            missing.append(
                "TELECOM_MW_LOCAL_VERIFIER_SECRET (required when IDENTITY_VERIFIER=local)"
            )

        if self.service_auth == "shared_secret" and self.service_shared_secret is None:
            missing.append(
                "TELECOM_MW_SERVICE_SHARED_SECRET (required when SERVICE_AUTH=shared_secret)"
            )
        if self.service_auth == "jwks":
            if not self.service_allowed_client_ids.strip():
                missing.append(
                    "TELECOM_MW_SERVICE_ALLOWED_CLIENT_IDS (required when SERVICE_AUTH=jwks); "
                    "a valid token is not enough, the client must be named"
                )
            for name, value in (
                ("TELECOM_MW_JWKS_URL", self.jwks_url),
                ("TELECOM_MW_JWT_ISSUER", self.jwt_issuer),
                ("TELECOM_MW_JWT_AUDIENCE", self.jwt_audience),
            ):
                if not value:
                    missing.append(f"{name} (required when SERVICE_AUTH=jwks)")

        if missing:
            raise ValueError("missing required configuration:\n  - " + "\n  - ".join(missing))

        if self.env == "production":
            unsafe: list[str] = []
            if self.store != "mongodb":
                unsafe.append(
                    "TELECOM_MW_STORE must be 'mongodb' in production; the in-memory store "
                    "loses every write on restart and is not shared between replicas"
                )
            if self.identity_verifier != "jwks":
                unsafe.append(
                    "TELECOM_MW_IDENTITY_VERIFIER must be 'jwks' in production; the local "
                    "verifier trusts a shared secret anyone with the environment can forge"
                )
            if self.service_auth == "unchecked":
                unsafe.append(
                    "TELECOM_MW_SERVICE_AUTH must not be 'unchecked' in production; "
                    "anything that can reach the port would be served, and a stolen "
                    "customer token would work from anywhere"
                )
            if unsafe:
                raise ValueError("unsafe production configuration:\n  - " + "\n  - ".join(unsafe))

        if self.claim_namespace and not self.claim_namespace.endswith("/"):
            raise ValueError("TELECOM_MW_CLAIM_NAMESPACE must end with '/'")

        return self

    @property
    def allowed_service_client_ids(self) -> frozenset[str]:
        """The allowlist, parsed. Empty entries are dropped rather than permitted."""
        return frozenset(
            entry.strip() for entry in self.service_allowed_client_ids.split(",") if entry.strip()
        )

    def describe(self) -> dict[str, object]:
        """Loggable view. Secrets are replaced, never masked in place."""
        data = self.model_dump()
        for name in list(data):
            if isinstance(getattr(self, name), SecretStr):
                data[name] = "***redacted***"
        return data


class _ExplicitSettings(Settings):
    """Validated from exactly the mapping it is given, with no ambient environment.

    ``Settings`` is a ``BaseSettings``, so its environment source runs even under
    ``model_validate`` - a caller that passes an explicit mapping still picks up every
    ``TELECOM_MW_`` variable that happens to be exported. That makes a test suite
    depend on the shell it runs in: the CI job that stands up a replica set exports
    ``TELECOM_MW_MONGODB_URI``, and six tests asserting the safe defaults saw a
    configured database instead.

    Only tests and diagnostics pass a mapping; the process boundary calls
    ``load_settings()`` with nothing and still reads the real environment.
    """

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],  # noqa: ARG003 - the signature is pydantic's
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,  # noqa: ARG003 - dropped on purpose
        dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003 - dropped on purpose
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003 - dropped
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # The mapping, and nothing else. Dropping the other three sources is the point.
        return (init_settings,)


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    """Load and validate, or fail with one message naming every problem."""
    try:
        if environ is None:
            return Settings()
        return _ExplicitSettings.model_validate(_from_environ(environ))
    except ValidationError as exc:
        raise ConfigurationError(_format(exc)) from exc
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from exc


def _from_environ(environ: dict[str, str]) -> dict[str, str]:
    prefix = "TELECOM_MW_"
    return {
        key.removeprefix(prefix).lower(): value
        for key, value in environ.items()
        if key.startswith(prefix)
    }


def _format(exc: ValidationError) -> str:
    lines = []
    for error in exc.errors():
        field = ".".join(str(part) for part in error["loc"])
        message = error["msg"].removeprefix("Value error, ")
        lines.append(f"TELECOM_MW_{field.upper()}: {message}" if field else message)
    return "invalid configuration:\n  - " + "\n  - ".join(lines)
