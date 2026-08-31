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

#: Namespaced claims are how Auth0 - and every provider that follows the same
#: convention - carries application data. The namespace is configuration, not a
#: constant: a different tenant uses a different one, and it must match the namespace
#: the identity provider's post-login action writes and the one the backing API reads.
DEFAULT_CLAIM_NAMESPACE = "https://telecom.example/"

#: The default names, kept as module constants because tests and the token-minting
#: script name them directly. Anything reading a real token goes through ClaimReader.
TENANT_CLAIM = f"{DEFAULT_CLAIM_NAMESPACE}tenant_id"
CX_CLAIM = f"{DEFAULT_CLAIM_NAMESPACE}cx_id"
ROLE_CLAIM = f"{DEFAULT_CLAIM_NAMESPACE}role"
#: Auth0 puts granted permissions here when RBAC is enabled on the API with
#: "Add Permissions in the Access Token" (token_dialect = access_token_authz). The
#: standard `scope` claim then carries only what the client requested - typically
#: "openid profile email" - so reading `scope` alone sees no permissions at all and
#: refuses every call. Read this first, fall back to `scope`, exactly as the
#: middleware does; the two services must agree about where permissions live.
PERMISSIONS_CLAIM = "permissions"
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


class ClaimReader:
    """Reads the claims this service depends on, from one configured namespace."""

    __slots__ = ("_namespace",)

    def __init__(self, namespace: str = DEFAULT_CLAIM_NAMESPACE) -> None:
        self._namespace = namespace

    def tenant_id(self, claims: dict[str, Any]) -> Any:
        return claims.get(f"{self._namespace}tenant_id")

    def cx_id(self, claims: dict[str, Any]) -> Any:
        return claims.get(f"{self._namespace}cx_id")

    def role(self, claims: dict[str, Any]) -> Any:
        return claims.get(f"{self._namespace}role")


_DEFAULT_READER = ClaimReader()


def _identity_from_claims(claims: dict[str, Any], reader: ClaimReader | None = None) -> Identity:
    reader = reader or _DEFAULT_READER
    subject = reader.cx_id(claims) or claims.get("sub")
    tenant_id = reader.tenant_id(claims)
    role_value = reader.role(claims)
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
        granted_scopes=(
            parse_scopes(claims.get(PERMISSIONS_CLAIM)) or parse_scopes(claims.get(SCOPE_CLAIM))
        ),
        expires_at=datetime.fromtimestamp(float(expires), tz=UTC),
        token_id=claims.get("jti"),
    )


class LocalVerifier:
    """HS256 verification with a shared secret. Development and tests only."""

    def __init__(
        self,
        secret: str,
        *,
        clock: Clock,
        audience: str = "telecom-mcp-tools",
        namespace: str = DEFAULT_CLAIM_NAMESPACE,
    ) -> None:
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
        self._reader = ClaimReader(namespace)

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
        return _identity_from_claims(claims, self._reader)


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
        namespace: str = DEFAULT_CLAIM_NAMESPACE,
    ) -> None:
        self._reader = ClaimReader(namespace)
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

        identity = _identity_from_claims(claims, self._reader)
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
