"""Which *service* is calling, as distinct from which person.

Two credentials arrive on a request from the MCP tool server and they answer different
questions. ``Authorization`` carries the customer's own token and answers "who is this
for". ``X-Service-Authorization`` carries the tool server's own machine credential and
answers "what is calling". Neither is sufficient alone, and that is the point: a
compromised service credential reads no customer record because ``require_human``
refuses a service principal on customer data, and a stolen customer token is useless
from anywhere this module does not recognise.

Three implementations, chosen by configuration, mirroring the token verifier above it:

``unchecked``
    Accepts anything, including nothing. The default, so a single-host deployment with
    the API bound to loopback is not made to provision a second credential before it
    can start. The settings validator refuses it in production.

``shared_secret``
    Constant-time comparison against a configured string. Development, and the smallest
    thing that makes the header mean something.

``jwks``
    The credential is an Auth0 client-credentials access token for this same API,
    verified against the tenant's published keys. Holding a valid token is not enough:
    every machine-to-machine application in the tenant can obtain one, so the client
    must also appear in a configured allowlist. That allowlist is the actual control;
    the signature only proves the token was not forged.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Any, Protocol

import jwt

from telecom_middleware.security.verifier import JwksKeyStore, TokenVerificationError

SERVICE_CREDENTIAL_HEADER = "X-Service-Authorization"
BEARER_PREFIX = "Bearer "

#: The client name recorded when the check is switched off, so an audit record never
#: implies a caller was verified when nothing verified it.
UNVERIFIED_CALLER = "unverified"

#: Short enough to guess is no better than no check at all.
MIN_SHARED_SECRET_LENGTH = 16


@dataclass(frozen=True, slots=True)
class ServiceCaller:
    """The service behind a request, once its credential has been checked."""

    client_id: str
    #: False only when checking is disabled. Never assume a caller was proven.
    verified: bool = True


class ServiceCredentialError(Exception):
    """Raised inside this module only; the dependency translates it."""


class MissingServiceCredentialError(ServiceCredentialError):
    """Nothing was presented in the service slot.

    A subclass rather than a flag, so the dependency can answer "you did not send the
    header" with a different code from "we do not know you" without inspecting strings.
    Only the absence is distinguished: a wrong credential and an expired one both stay
    behind the single opaque refusal, because telling those apart *would* help a
    guesser.
    """


class ServiceCredentialVerifier(Protocol):
    async def verify(self, credential: str | None) -> ServiceCaller: ...


def strip_bearer(value: str | None) -> str:
    """Take the credential out of a header value, with or without the scheme."""
    if not value:
        return ""
    if value.startswith(BEARER_PREFIX):
        return value[len(BEARER_PREFIX) :].strip()
    # A bare credential is accepted so a misconfigured caller fails on the credential
    # itself, with a reason, rather than looking like it sent nothing.
    return value.strip()


class UncheckedServiceCredentials:
    """Accepts every caller. The default, and refused in production."""

    __slots__ = ()

    async def verify(self, credential: str | None) -> ServiceCaller:
        del credential
        return ServiceCaller(client_id=UNVERIFIED_CALLER, verified=False)


class SharedSecretServiceCredentials:
    """Constant-time comparison against one configured string."""

    __slots__ = ("_client_id", "_secret")

    def __init__(self, secret: str, *, client_id: str = "telecom-mcp-tools") -> None:
        if len(secret) < MIN_SHARED_SECRET_LENGTH:
            raise ValueError(
                f"the service shared secret must be at least {MIN_SHARED_SECRET_LENGTH} characters"
            )
        self._secret = secret
        self._client_id = client_id

    async def verify(self, credential: str | None) -> ServiceCaller:
        presented = strip_bearer(credential)
        if not presented:
            raise MissingServiceCredentialError("no service credential presented")
        # compare_digest rather than ==, so a wrong guess takes the same time as a
        # right one and the secret cannot be recovered a character at a time.
        if not hmac.compare_digest(presented, self._secret):
            raise ServiceCredentialError("service credential not recognised")
        return ServiceCaller(client_id=self._client_id)


class JwtServiceCredentials:
    """An Auth0 client-credentials token, verified, from an allowlisted client."""

    __slots__ = ("_allowed", "_audience", "_issuer", "_keys")

    def __init__(
        self,
        *,
        keys: JwksKeyStore,
        issuer: str,
        audience: str,
        allowed_client_ids: frozenset[str],
    ) -> None:
        if not allowed_client_ids:
            raise ValueError("at least one service client id must be permitted")
        self._keys = keys
        self._issuer = issuer
        self._audience = audience
        self._allowed = allowed_client_ids

    async def verify(self, credential: str | None) -> ServiceCaller:
        presented = strip_bearer(credential)
        if not presented:
            raise MissingServiceCredentialError("no service credential presented")

        try:
            header = jwt.get_unverified_header(presented)
        except jwt.PyJWTError as exc:
            raise ServiceCredentialError("service credential is malformed") from exc

        kid = header.get("kid")
        if not kid:
            raise ServiceCredentialError("service credential has no key identifier")

        try:
            key = await self._keys.signing_key(str(kid))
        except TokenVerificationError as exc:
            raise ServiceCredentialError("signing keys are unavailable") from exc

        try:
            claims: dict[str, Any] = jwt.decode(
                presented,
                key.key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "aud", "iss", "sub"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise ServiceCredentialError("service credential expired") from exc
        except jwt.PyJWTError as exc:
            raise ServiceCredentialError("service credential could not be verified") from exc

        subject = str(claims.get("sub") or "")
        if claims.get("gty") != "client-credentials" and not subject.endswith("@clients"):
            # A person's access token would otherwise pass every check above. Presenting
            # one here is either a bug or an attempt to borrow a user's authority for
            # the service slot.
            raise ServiceCredentialError("a user token is not a service credential")

        client_id = str(claims.get("azp") or subject.removesuffix("@clients"))
        if client_id not in self._allowed:
            # Every machine-to-machine application in the tenant can mint a valid token
            # for this API. Only the ones named in configuration may call it.
            raise ServiceCredentialError("service is not permitted to call this API")

        return ServiceCaller(client_id=client_id)
