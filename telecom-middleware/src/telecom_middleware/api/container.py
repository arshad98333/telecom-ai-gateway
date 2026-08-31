"""Choosing implementations from configuration — the only place that decision is made."""

from __future__ import annotations

import time
from typing import Any

import httpx

from telecom_middleware.api.context import AppContext, SystemClock, UUIDGenerator
from telecom_middleware.config.settings import Settings
from telecom_middleware.domain.errors import ConfigurationError
from telecom_middleware.observability.logging import configure_logging
from telecom_middleware.observability.redaction import Redactor, derive_pseudonym_key
from telecom_middleware.security.service_credential import (
    JwtServiceCredentials,
    ServiceCredentialVerifier,
    SharedSecretServiceCredentials,
    UncheckedServiceCredentials,
)
from telecom_middleware.security.verifier import (
    JwksKeyStore,
    JwksVerifier,
    LocalVerifier,
    TokenVerifier,
)
from telecom_middleware.services.recording import Recorder


def build_context(
    settings: Settings,
    *,
    store: Any | None = None,
    clock: Any | None = None,
    ids: Any | None = None,
    configure_logs: bool = True,
) -> AppContext:
    """Wire the application. The overrides exist for tests, not for production paths."""
    clock = clock or SystemClock()
    ids = ids or UUIDGenerator()
    redactor = _build_redactor(settings)
    if configure_logs:
        configure_logging(
            level=settings.log_level, service_name=settings.service_name, redactor=redactor
        )

    chosen_store = store if store is not None else _build_store(settings)
    # One key store, shared by the person verifier and the service check: a tenant
    # publishes one key set, and two caches would refetch twice on every rotation.
    keys = _build_key_store(settings)
    return AppContext(
        settings=settings,
        store=chosen_store,
        verifier=_build_verifier(settings, keys),
        service_credentials=_build_service_credentials(settings, keys),
        redactor=redactor,
        recorder=Recorder(store=chosen_store, redactor=redactor, clock=clock, ids=ids),
        clock=clock,
        ids=ids,
    )


def _build_redactor(settings: Settings) -> Redactor:
    # Derived from a secret that already exists, so logging safely needs no new secret
    # to be provisioned, rotated and leaked.
    secret = (
        settings.local_verifier_secret.get_secret_value()
        if settings.local_verifier_secret is not None
        else settings.mongodb_uri.get_secret_value()
        if settings.mongodb_uri is not None
        else settings.service_name
    )
    return Redactor(derive_pseudonym_key(settings.service_name, secret))


def _build_store(settings: Settings) -> Any:
    if settings.store == "memory":
        from telecom_middleware.repositories.memory import MemoryStore

        return MemoryStore()

    if settings.mongodb_uri is None:
        raise ConfigurationError("store=mongodb requires a connection string")

    from motor.motor_asyncio import AsyncIOMotorClient

    from telecom_middleware.repositories.mongo import MongoStore

    client: Any = AsyncIOMotorClient(
        settings.mongodb_uri.get_secret_value(),
        maxPoolSize=settings.mongodb_max_pool_size,
        serverSelectionTimeoutMS=settings.mongodb_timeout_ms,
        connectTimeoutMS=settings.mongodb_timeout_ms,
        uuidRepresentation="standard",
        # Reads and writes must survive a primary election; the defaults here are what
        # make a rolling restart of the replica set invisible to callers.
        retryWrites=True,
        retryReads=True,
        w="majority",
    )
    return MongoStore(client, settings.mongodb_database)


def _build_key_store(settings: Settings) -> JwksKeyStore | None:
    """The tenant's signing keys, or None when nothing here needs them."""
    if settings.identity_verifier != "jwks" and settings.service_auth != "jwks":
        return None
    if not settings.jwks_url:
        raise ConfigurationError("verifying against a tenant requires TELECOM_MW_JWKS_URL")

    jwks_url = settings.jwks_url

    async def fetch_jwks() -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            response = await client.get(jwks_url)
            response.raise_for_status()
            document: dict[str, Any] = response.json()
            return document

    return JwksKeyStore(
        fetch_jwks=fetch_jwks, now=time.monotonic, cache_ttl_s=settings.jwks_cache_ttl_s
    )


def _build_service_credentials(
    settings: Settings, keys: JwksKeyStore | None
) -> ServiceCredentialVerifier:
    if settings.service_auth == "unchecked":
        return UncheckedServiceCredentials()

    if settings.service_auth == "shared_secret":
        if settings.service_shared_secret is None:
            raise ConfigurationError("service_auth=shared_secret requires a shared secret")
        return SharedSecretServiceCredentials(settings.service_shared_secret.get_secret_value())

    if keys is None or not (settings.jwt_issuer and settings.jwt_audience):
        raise ConfigurationError("service_auth=jwks requires a JWKS URL, issuer and audience")
    return JwtServiceCredentials(
        keys=keys,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        allowed_client_ids=settings.allowed_service_client_ids,
    )


def _build_verifier(settings: Settings, keys: JwksKeyStore | None = None) -> TokenVerifier:
    if settings.identity_verifier == "local":
        if settings.local_verifier_secret is None:
            raise ConfigurationError("identity_verifier=local requires a signing secret")
        return LocalVerifier(
            settings.local_verifier_secret.get_secret_value(),
            audience=settings.jwt_audience or "https://api.telecom.example/v1",
            namespace=settings.claim_namespace,
        )

    if not (settings.jwks_url and settings.jwt_issuer and settings.jwt_audience):
        raise ConfigurationError("identity_verifier=jwks requires a URL, issuer and audience")

    return JwksVerifier(
        keys=keys or _build_key_store(settings),
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        namespace=settings.claim_namespace,
        now=time.monotonic,
        cache_ttl_s=settings.jwks_cache_ttl_s,
    )
