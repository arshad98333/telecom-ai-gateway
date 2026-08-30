"""Token verification against Auth0, and a local verifier for development.

``JwksVerifier`` is the production path: RS256, verified against the tenant's published
keys, with the issuer and audience checked rather than assumed. Keys are cached,
refreshed once when an unknown key identifier appears (a rotation) rather than per call,
and served stale through a bounded window so an Auth0 outage does not become ours -
but past that window it fails closed.

``LocalVerifier`` exists so the whole test suite and a developer's laptop need no Auth0
tenant, no account and no network. The settings validator refuses it in production.

Claims come from Auth0 in two places: the standard ones (``sub``, ``aud``, ``iss``,
``exp``, ``azp``, ``gty``) and namespaced custom ones added by the post-login Action in
``infra/auth0``. Permissions arrive in the ``permissions`` claim because RBAC is enabled
on the API; the ``scope`` claim is read as a fallback.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

import jwt
from jwt import PyJWKSet

from telecom_middleware.security.permissions import Role, Scope, parse_scopes
from telecom_middleware.security.principal import Principal

#: Tokens with a longer life than this are refused whatever the issuer signed.
MAX_TOKEN_LIFETIME_S = 3600.0
MIN_LOCAL_SECRET_BYTES = 32


class TokenVerificationError(Exception):
    """Raised inside this module only; translated to a domain error by the caller."""


class TokenVerifier(Protocol):
    async def verify(self, token: str) -> Principal: ...


class ClaimReader:
    """Reads the claims this service depends on, from one configured namespace."""

    __slots__ = ("_namespace",)

    def __init__(self, namespace: str) -> None:
        self._namespace = namespace

    def tenant_id(self, claims: dict[str, Any]) -> str | None:
        return self._string(claims, "tenant_id")

    def cx_id(self, claims: dict[str, Any]) -> str | None:
        return self._string(claims, "cx_id")

    def role(self, claims: dict[str, Any]) -> str | None:
        return self._string(claims, "role")

    def _string(self, claims: dict[str, Any], name: str) -> str | None:
        value = claims.get(f"{self._namespace}{name}")
        return value if isinstance(value, str) and value else None


def principal_from_claims(claims: dict[str, Any], reader: ClaimReader) -> Principal:
    """Build a principal, refusing anything ambiguous rather than guessing a default."""
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise TokenVerificationError("token carries no subject")

    expires = claims.get("exp")
    if expires is None:
        raise TokenVerificationError("token carries no expiry")

    # A client-credentials token has no user behind it. Auth0 marks it with gty and by
    # the subject ending in @clients; either signal is enough to refuse to treat it as
    # a person.
    is_service = claims.get("gty") == "client-credentials" or subject.endswith("@clients")

    tenant_id = reader.tenant_id(claims)
    if not tenant_id:
        raise TokenVerificationError("token carries no tenant")

    role_value = reader.role(claims) or (Role.SERVICE.value if is_service else None)
    try:
        role = Role(str(role_value))
    except ValueError as exc:
        # An unrecognised role must never fall back to something permissive.
        raise TokenVerificationError("token carries an unknown role") from exc

    cx_id = reader.cx_id(claims)
    if role is Role.CUSTOMER and not cx_id:
        raise TokenVerificationError("a customer token must carry a customer reference")

    granted: frozenset[Scope] = parse_scopes(claims.get("permissions")) or parse_scopes(
        claims.get("scope")
    )

    return Principal(
        subject=subject,
        tenant_id=tenant_id,
        role=role,
        granted_scopes=granted,
        expires_at=datetime.fromtimestamp(float(expires), tz=UTC),
        cx_id=cx_id,
        token_id=claims.get("jti"),
        is_service=is_service,
    )


class LocalVerifier:
    """HS256 verification with a shared secret. Development and tests only."""

    def __init__(self, secret: str, *, audience: str, namespace: str) -> None:
        if len(secret.encode("utf-8")) < MIN_LOCAL_SECRET_BYTES:
            raise ValueError(
                f"local verifier secret must be at least {MIN_LOCAL_SECRET_BYTES} bytes"
            )
        self._secret = secret
        self._audience = audience
        self._reader = ClaimReader(namespace)

    async def verify(self, token: str) -> Principal:
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                audience=self._audience,
                options={"require": ["exp", "aud", "sub"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenVerificationError("token expired") from exc
        except jwt.PyJWTError as exc:
            raise TokenVerificationError("token could not be verified") from exc
        return principal_from_claims(claims, self._reader)


JwksFetcher = Callable[[], Awaitable[dict[str, Any]]]


class JwksVerifier:
    """RS256 verification against Auth0's published keys."""

    def __init__(
        self,
        *,
        fetch_jwks: JwksFetcher,
        issuer: str,
        audience: str,
        namespace: str,
        now: Callable[[], float],
        cache_ttl_s: float = 600.0,
        stale_if_error_s: float = 3600.0,
    ) -> None:
        self._fetch_jwks = fetch_jwks
        self._issuer = issuer
        self._audience = audience
        self._reader = ClaimReader(namespace)
        self._now = now
        self._cache_ttl_s = cache_ttl_s
        self._stale_if_error_s = stale_if_error_s
        self._keys: PyJWKSet | None = None
        self._fetched_at = 0.0

    async def verify(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise TokenVerificationError("token header is malformed") from exc

        kid = header.get("kid")
        if not kid:
            raise TokenVerificationError("token has no key identifier")

        key = await self._signing_key(str(kid))
        try:
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "aud", "iss", "sub"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenVerificationError("token expired") from exc
        except jwt.PyJWTError as exc:
            raise TokenVerificationError("token could not be verified") from exc

        principal = principal_from_claims(claims, self._reader)
        lifetime = (principal.expires_at - datetime.now(UTC)).total_seconds()
        if lifetime > MAX_TOKEN_LIFETIME_S:
            raise TokenVerificationError("token lifetime exceeds the permitted maximum")
        return principal

    async def _signing_key(self, kid: str) -> Any:
        await self._ensure_keys()
        found = self._find(kid)
        if found is not None:
            return found
        # An unknown key identifier is the signal that keys rotated. Refresh once,
        # not per call, or a bad token becomes a denial-of-service against Auth0.
        self._fetched_at = 0.0
        await self._ensure_keys()
        found = self._find(kid)
        if found is None:
            raise TokenVerificationError("no signing key matches the token")
        return found

    def _find(self, kid: str) -> Any | None:
        if self._keys is None:
            return None
        for key in self._keys.keys:
            if key.key_id == kid:
                return key
        return None

    async def _ensure_keys(self) -> None:
        age = self._now() - self._fetched_at
        if self._keys is not None and age < self._cache_ttl_s:
            return
        try:
            document = await self._fetch_jwks()
            self._keys = PyJWKSet.from_dict(document)
            self._fetched_at = self._now()
        except Exception as exc:
            if self._keys is not None and age < self._stale_if_error_s:
                return  # serve through a provider outage, but only for a bounded window
            raise TokenVerificationError("signing keys are unavailable") from exc
