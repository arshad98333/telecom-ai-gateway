"""Token verification. Two implementations, one interface, chosen by configuration.

``LocalVerifier`` signs and verifies with a shared secret. It exists so the whole test
suite and a developer's laptop need no identity provider, no account and no network.
It is refused in production by the settings validator.

``JwksVerifier`` verifies RS256 tokens against a JWKS document, which is how Auth0 and
every comparable provider work. The document is fetched through an injected callable,
so tests exercise real verification with a locally generated key pair and still touch
no network. Keys are cached with a stale-if-error window: a provider outage must not
become our outage, but a rotated key must still be picked up.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

import jwt
from jwt import PyJWKSet

from telecom_mcp.domain.permissions import Role, parse_scopes
from telecom_mcp.domain.ports import Clock
from telecom_mcp.security.identity import Identity

#: Claim names. Namespaced claims are how Auth0 carries application data.
TENANT_CLAIM = "https://telecom.example/tenant_id"
CX_CLAIM = "https://telecom.example/cx_id"
ROLE_CLAIM = "https://telecom.example/role"
SCOPE_CLAIM = "scope"

#: Tokens older than this are refused even if the provider signed a longer life.
MAX_TOKEN_LIFETIME_S = 3600.0

#: Minimum length of the shared secret used by the local verifier.
MIN_LOCAL_SECRET_BYTES = 32


class TokenVerifier(Protocol):
    """Turns a bearer token into an identity, or raises."""

    async def verify(self, token: str) -> Identity: ...


class TokenVerificationError(Exception):
    """Raised inside this module only; translated to a domain error by the caller."""


def _identity_from_claims(claims: dict[str, Any]) -> Identity:
    subject = claims.get(CX_CLAIM) or claims.get("sub")
    tenant_id = claims.get(TENANT_CLAIM)
    role_value = claims.get(ROLE_CLAIM)
    expires = claims.get("exp")

    if not subject or not isinstance(subject, str):
        raise TokenVerificationError("token carries no usable subject")
    if not tenant_id or not isinstance(tenant_id, str):
        raise TokenVerificationError("token carries no tenant")
    if expires is None:
        raise TokenVerificationError("token carries no expiry")
    try:
        role = Role(str(role_value))
    except ValueError as exc:
        # An unrecognised role must not fall back to a permissive default.
        raise TokenVerificationError("token carries an unknown role") from exc

    return Identity(
        subject=subject,
        tenant_id=tenant_id,
        role=role,
        granted_scopes=parse_scopes(claims.get(SCOPE_CLAIM)),
        expires_at=datetime.fromtimestamp(float(expires), tz=UTC),
        token_id=claims.get("jti"),
    )


class LocalVerifier:
    """HS256 verification with a shared secret. Development and tests only."""

    def __init__(self, secret: str, *, clock: Clock, audience: str = "telecom-mcp-tools") -> None:
        # RFC 7518 section 3.2: an HMAC key shorter than the digest is weak. A short
        # secret here would let a development convenience become a real forgery risk
        # if the local verifier ever escaped a laptop.
        if len(secret.encode("utf-8")) < MIN_LOCAL_SECRET_BYTES:
            raise ValueError(
                f"local verifier secret must be at least {MIN_LOCAL_SECRET_BYTES} bytes"
            )
        self._secret = secret
        self._clock = clock
        self._audience = audience

    async def verify(self, token: str) -> Identity:
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                audience=self._audience,
                options={"require": ["exp", "aud"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenVerificationError("token expired") from exc
        except jwt.PyJWTError as exc:
            raise TokenVerificationError("token could not be verified") from exc
        return _identity_from_claims(claims)


JwksFetcher = Callable[[], Awaitable[dict[str, Any]]]


class JwksVerifier:
    """RS256 verification against a cached JWKS document."""

    def __init__(
        self,
        *,
        fetch_jwks: JwksFetcher,
        issuer: str,
        audience: str,
        clock: Clock,
        cache_ttl_s: float = 600.0,
        stale_if_error_s: float = 3600.0,
    ) -> None:
        self._fetch_jwks = fetch_jwks
        self._issuer = issuer
        self._audience = audience
        self._clock = clock
        self._cache_ttl_s = cache_ttl_s
        self._stale_if_error_s = stale_if_error_s
        self._keys: PyJWKSet | None = None
        self._fetched_at: float = 0.0

    async def verify(self, token: str) -> Identity:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise TokenVerificationError("token header is malformed") from exc

        kid = header.get("kid")
        if not kid:
            raise TokenVerificationError("token has no key identifier")

        key = await self._signing_key(kid, allow_refresh=True)
        try:
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "aud", "iss"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenVerificationError("token expired") from exc
        except jwt.PyJWTError as exc:
            raise TokenVerificationError("token could not be verified") from exc

        identity = _identity_from_claims(claims)
        lifetime = (identity.expires_at - self._clock.now()).total_seconds()
        if lifetime > MAX_TOKEN_LIFETIME_S:
            raise TokenVerificationError("token lifetime exceeds the permitted maximum")
        return identity

    async def _signing_key(self, kid: str, *, allow_refresh: bool) -> Any:
        await self._ensure_keys()
        if self._keys is not None:
            for key in self._keys.keys:
                if key.key_id == kid:
                    return key
        if allow_refresh:
            # An unknown kid is the signal that keys rotated. Refresh once, not per call.
            self._fetched_at = 0.0
            await self._ensure_keys()
            if self._keys is not None:
                for key in self._keys.keys:
                    if key.key_id == kid:
                        return key
        raise TokenVerificationError("no signing key matches the token")

    async def _ensure_keys(self) -> None:
        age = self._clock.monotonic() - self._fetched_at
        if self._keys is not None and age < self._cache_ttl_s:
            return
        try:
            document = await self._fetch_jwks()
            self._keys = PyJWKSet.from_dict(document)
            self._fetched_at = self._clock.monotonic()
        except Exception as exc:
            # Serve the cached keys through a provider outage rather than failing every
            # request, but only for a bounded window.
            if self._keys is not None and age < self._stale_if_error_s:
                return
            raise TokenVerificationError("signing keys are unavailable") from exc
