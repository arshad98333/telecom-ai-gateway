"""The composition root: the one place implementations are chosen from configuration.

Every "which implementation" decision lives here, made once at startup from validated
settings, rather than as conditionals scattered through the code. That is what makes
the fake and the real backend interchangeable without a single `if` in the domain.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

import httpx

from telecom_mcp._version import __version__
from telecom_mcp.adapters.backend import TelecomBackend
from telecom_mcp.adapters.fake_backend import FakeTelecomBackend
from telecom_mcp.adapters.http_backend import HttpTelecomBackend, build_client
from telecom_mcp.adapters.idempotency import (
    IdempotencyStore,
    MemoryIdempotencyStore,
    RedisIdempotencyStore,
)
from telecom_mcp.adapters.reliability import CircuitBreaker, RetryPolicy
from telecom_mcp.api.executor import ToolExecutor
from telecom_mcp.config.settings import Settings
from telecom_mcp.domain.errors import ConfigurationError
from telecom_mcp.domain.ports import (
    Clock,
    IdGenerator,
    Jitter,
    SystemClock,
    SystemJitter,
    UUIDGenerator,
)
from telecom_mcp.guardrails.pipeline import GuardrailPipeline
from telecom_mcp.observability.health import HealthChecker
from telecom_mcp.observability.logging import configure_logging, get_logger
from telecom_mcp.observability.metrics import Metrics
from telecom_mcp.observability.redaction import Redactor, derive_pseudonym_key
from telecom_mcp.security.audit import AuditLog, AuditSink, FileSink, StdoutSink
from telecom_mcp.security.authorization import Authorizer, OwnershipChecker
from telecom_mcp.security.service_token import (
    ClientCredentialsServiceToken,
    ServiceTokenProvider,
    StaticServiceToken,
)
from telecom_mcp.security.verifier import JwksVerifier, LocalVerifier, TokenVerifier

logger = get_logger(__name__)


@dataclass(slots=True)
class Application:
    """Everything a transport needs, already wired and validated."""

    settings: Settings
    executor: ToolExecutor
    health: HealthChecker
    metrics: Metrics
    backend: TelecomBackend
    idempotency: IdempotencyStore
    guardrails: GuardrailPipeline

    async def aclose(self) -> None:
        closer = getattr(self.backend, "aclose", None)
        if closer is not None:
            await closer()


def build_application(
    settings: Settings,
    *,
    clock: Clock | None = None,
    id_generator: IdGenerator | None = None,
    jitter: Jitter | None = None,
    ownership: OwnershipChecker | None = None,
    backend: TelecomBackend | None = None,
    audit_sink: AuditSink | None = None,
) -> Application:
    """Build the application graph. Overrides exist for tests, not for production paths."""
    clock = clock or SystemClock()
    id_generator = id_generator or UUIDGenerator()
    jitter = jitter or SystemJitter()

    redactor = _build_redactor(settings)
    configure_logging(
        level=settings.log_level, service_name=settings.service_name, redactor=redactor
    )

    chosen_backend = backend or _build_backend(settings, clock, id_generator)
    idempotency = _build_idempotency(settings, clock)
    metrics = Metrics()

    audit = AuditLog(
        sink=audit_sink or _build_audit_sink(settings),
        clock=clock,
        redactor=redactor,
        id_generator=id_generator,
    )
    authorizer = Authorizer(verifier=_build_verifier(settings, clock), ownership=ownership)
    guardrails = GuardrailPipeline(settings.guardrail_policy(), clock)
    executor = ToolExecutor(
        authorizer=authorizer,
        backend=chosen_backend,
        idempotency=idempotency,
        audit=audit,
        metrics=metrics,
        redactor=redactor,
        clock=clock,
        jitter=jitter,
        retry_policy=RetryPolicy(
            attempts=settings.retry_attempts, base_delay_s=settings.retry_base_delay_s
        ),
        breaker=CircuitBreaker(
            clock=clock,
            failure_threshold=settings.breaker_failure_threshold,
            reset_timeout_s=settings.breaker_reset_timeout_s,
            name="telecom_middleware",
        ),
        guardrails=guardrails,
        max_concurrent_calls=settings.max_concurrent_tool_calls,
        tool_timeout_s=settings.tool_timeout_s,
    )

    health = HealthChecker(version=__version__, clock=clock)
    health.register("telecom_middleware", chosen_backend.ping)
    health.register("idempotency_store", idempotency.ping)

    logger.info("guardrail_policy_loaded", **guardrails.policy.describe())

    return Application(
        settings=settings,
        executor=executor,
        health=health,
        metrics=metrics,
        backend=chosen_backend,
        idempotency=idempotency,
        guardrails=guardrails,
    )


def _build_redactor(settings: Settings) -> Redactor:
    # The pseudonym key is derived from a secret that already exists, so no new secret
    # has to be managed for logging to be safe.
    secret = (
        settings.backend_api_key.get_secret_value()
        if settings.backend_api_key is not None
        else settings.local_verifier_secret.get_secret_value()
        if settings.local_verifier_secret is not None
        else settings.service_name
    )
    return Redactor(derive_pseudonym_key(settings.service_name, secret))


def _build_backend(settings: Settings, clock: Clock, id_generator: IdGenerator) -> TelecomBackend:
    if settings.backend == "fake":
        return FakeTelecomBackend(clock=clock, id_generator=id_generator)
    if settings.backend_base_url is None:
        raise ConfigurationError("backend=http requires a base URL")
    client: httpx.AsyncClient = build_client(
        base_url=settings.backend_base_url,
        connect_timeout_s=settings.backend_connect_timeout_s,
        read_timeout_s=settings.backend_read_timeout_s,
        max_connections=settings.backend_max_connections,
    )
    return HttpTelecomBackend(client, _build_service_token(settings, clock))


def _build_service_token(settings: Settings, clock: Clock) -> ServiceTokenProvider:
    """How this service proves its own identity to the middleware."""
    if settings.service_identity_source == "static":
        if settings.backend_api_key is None:
            raise ConfigurationError("service_identity_source=static requires an API key")
        return StaticServiceToken(settings.backend_api_key.get_secret_value())

    audience = settings.effective_service_token_audience
    if not (
        settings.service_token_url
        and settings.service_client_id
        and settings.service_client_secret
        and audience
    ):
        raise ConfigurationError(
            "service_identity_source=client_credentials requires a token URL, client id, "
            "client secret and audience"
        )
    return ClientCredentialsServiceToken(
        token_url=settings.service_token_url,
        client_id=settings.service_client_id,
        client_secret=settings.service_client_secret.get_secret_value(),
        audience=audience,
        clock=clock,
    )


def _build_idempotency(settings: Settings, clock: Clock) -> IdempotencyStore:
    if settings.idempotency_store == "memory":
        return MemoryIdempotencyStore(clock=clock, ttl_s=settings.idempotency_ttl_s)
    if settings.redis_url is None:
        raise ConfigurationError("idempotency_store=redis requires a Redis URL")
    try:
        import redis.asyncio as redis
    except ImportError as exc:  # pragma: no cover - exercised by the packaging tests
        raise ConfigurationError(
            "the redis extra is not installed; install telecom-mcp-tools[redis]"
        ) from exc
    client: Any = redis.from_url(settings.redis_url, decode_responses=True)
    return RedisIdempotencyStore(client, ttl_s=settings.idempotency_ttl_s)


def _build_verifier(settings: Settings, clock: Clock) -> TokenVerifier:
    if settings.identity_verifier == "local":
        if settings.local_verifier_secret is None:
            raise ConfigurationError("identity_verifier=local requires a signing secret")
        return LocalVerifier(
            settings.local_verifier_secret.get_secret_value(),
            clock=clock,
            audience=settings.jwt_audience or "telecom-mcp-tools",
            namespace=settings.claim_namespace,
        )
    if not (settings.jwks_url and settings.jwt_issuer and settings.jwt_audience):
        raise ConfigurationError("identity_verifier=jwks requires a URL, issuer and audience")

    jwks_url = settings.jwks_url

    async def fetch_jwks() -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            response = await client.get(jwks_url)
            response.raise_for_status()
            document: dict[str, Any] = response.json()
            return document

    return JwksVerifier(
        fetch_jwks=fetch_jwks,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        clock=clock,
        cache_ttl_s=settings.jwks_cache_ttl_s,
        namespace=settings.claim_namespace,
    )


def _build_audit_sink(settings: Settings) -> AuditSink:
    if settings.audit_sink == "file":
        return FileSink(settings.audit_file_path)
    # stderr, not stdout: the stdio transport owns stdout for the MCP protocol.
    return StdoutSink(sys.stderr)
