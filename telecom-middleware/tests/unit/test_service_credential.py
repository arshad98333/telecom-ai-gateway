"""Which service is calling, checked independently of who the call is for."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from telecom_middleware.security.service_credential import (
    JwtServiceCredentials,
    ServiceCredentialError,
    SharedSecretServiceCredentials,
    UncheckedServiceCredentials,
    strip_bearer,
)
from telecom_middleware.security.verifier import JwksKeyStore

SECRET = "a-service-credential-long-enough"
ISSUER = "https://tenant.example.invalid/"
AUDIENCE = "https://api.telecom.example/v1"
CLIENT = "mcp-tool-server-client-id"


# --- the shared-secret implementation -----------------------------------------------


async def test_the_configured_credential_is_accepted_with_or_without_the_scheme() -> None:
    verifier = SharedSecretServiceCredentials(SECRET)

    assert (await verifier.verify(f"Bearer {SECRET}")).client_id == "telecom-mcp-tools"
    assert (await verifier.verify(SECRET)).verified is True


@pytest.mark.parametrize("presented", [None, "", "Bearer ", "Bearer wrong-credential-entirely"])
async def test_anything_else_is_refused(presented: str | None) -> None:
    verifier = SharedSecretServiceCredentials(SECRET)

    with pytest.raises(ServiceCredentialError):
        await verifier.verify(presented)


def test_a_short_secret_is_refused_at_construction() -> None:
    # A credential short enough to guess is no better than no check at all, and it
    # must fail at startup rather than look like protection.
    with pytest.raises(ValueError, match="at least"):
        SharedSecretServiceCredentials("tooshort")


# --- the disabled implementation ----------------------------------------------------


async def test_when_checking_is_off_the_caller_is_never_reported_as_verified() -> None:
    caller = await UncheckedServiceCredentials().verify(None)

    assert caller.verified is False
    assert caller.client_id == "unverified"


# --- the Auth0 implementation -------------------------------------------------------


def _key_pair() -> tuple[Any, dict[str, Any]]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = private.public_key().public_numbers()

    def encode(value: int) -> str:
        import base64

        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    document = {
        "keys": [
            {
                "kty": "RSA",
                "kid": "key-1",
                "use": "sig",
                "alg": "RS256",
                "n": encode(numbers.n),
                "e": encode(numbers.e),
            }
        ]
    }
    return private, document


def _token(private: Any, **overrides: Any) -> str:
    claims: dict[str, Any] = {
        "sub": f"{CLIENT}@clients",
        "aud": AUDIENCE,
        "iss": ISSUER,
        "azp": CLIENT,
        "gty": "client-credentials",
        "exp": int((datetime.now(UTC) + timedelta(minutes=30)).timestamp()),
    }
    claims.update(overrides)
    return jwt.encode(claims, private, algorithm="RS256", headers={"kid": "key-1"})


def _verifier(document: dict[str, Any], allowed: set[str] | None = None) -> JwtServiceCredentials:
    async def fetch() -> dict[str, Any]:
        return document

    return JwtServiceCredentials(
        keys=JwksKeyStore(fetch_jwks=fetch, now=lambda: 0.0),
        issuer=ISSUER,
        audience=AUDIENCE,
        allowed_client_ids=frozenset(allowed or {CLIENT}),
    )


async def test_an_allowlisted_machine_token_is_accepted() -> None:
    private, document = _key_pair()

    caller = await _verifier(document).verify(f"Bearer {_token(private)}")

    assert caller.client_id == CLIENT
    assert caller.verified is True


async def test_a_valid_token_from_a_client_we_did_not_name_is_refused() -> None:
    """Every machine-to-machine application in the tenant can mint a valid token for
    this API. The signature proves it was not forged; the allowlist is the control."""
    private, document = _key_pair()

    with pytest.raises(ServiceCredentialError, match="not permitted"):
        await _verifier(document, allowed={"some-other-service"}).verify(_token(private))


async def test_a_persons_token_cannot_stand_in_for_a_service_credential() -> None:
    private, document = _key_pair()
    personal = _token(private, sub="auth0|a-real-person", azp=CLIENT, gty=None)

    with pytest.raises(ServiceCredentialError, match="not a service credential"):
        await _verifier(document).verify(personal)


async def test_a_token_for_another_audience_is_refused() -> None:
    private, document = _key_pair()

    with pytest.raises(ServiceCredentialError, match="could not be verified"):
        await _verifier(document).verify(_token(private, aud="https://something.else/"))


async def test_an_expired_credential_is_named_as_expired() -> None:
    private, document = _key_pair()
    stale = _token(private, exp=int((datetime.now(UTC) - timedelta(minutes=1)).timestamp()))

    with pytest.raises(ServiceCredentialError, match="expired"):
        await _verifier(document).verify(stale)


async def test_a_forged_credential_is_refused() -> None:
    _, document = _key_pair()
    other_private, _ = _key_pair()

    with pytest.raises(ServiceCredentialError):
        await _verifier(document).verify(_token(other_private))


# --- the header itself --------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [("Bearer abc", "abc"), ("abc", "abc"), ("Bearer   abc  ", "abc"), (None, ""), ("", "")],
)
def test_the_credential_is_read_with_or_without_the_scheme(
    value: str | None, expected: str
) -> None:
    assert strip_bearer(value) == expected
