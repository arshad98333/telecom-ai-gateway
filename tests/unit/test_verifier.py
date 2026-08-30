"""Identity is the control everything else rests on, so it is attacked in tests."""

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from telecom_mcp.domain.permissions import Role, Scope
from telecom_mcp.security.verifier import (
    CX_CLAIM,
    ROLE_CLAIM,
    TENANT_CLAIM,
    JwksVerifier,
    LocalVerifier,
    TokenVerificationError,
)
from tests.fakes import FrozenClock

SECRET = "test-signing-secret-long-enough-for-hs256"
AUDIENCE = "telecom-mcp-tools"
ISSUER = "https://tenant.example.invalid/"


def _claims(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "sub": "auth0|abc",
        CX_CLAIM: "CX-1234",
        TENANT_CLAIM: "tenant-eu-1",
        ROLE_CLAIM: "customer",
        "scope": "account:read service:read",
        "aud": AUDIENCE,
        "iss": ISSUER,
        "jti": "token-1",
        "exp": int((datetime.now(UTC) + timedelta(minutes=10)).timestamp()),
    }
    base.update(overrides)
    return {key: value for key, value in base.items() if value is not None}


def _hs256(**overrides: Any) -> str:
    return jwt.encode(_claims(**overrides), SECRET, algorithm="HS256")


@pytest.fixture
def local() -> LocalVerifier:
    return LocalVerifier(SECRET, clock=FrozenClock(), audience=AUDIENCE)


async def test_a_valid_token_produces_the_expected_identity(local: LocalVerifier) -> None:
    identity = await local.verify(_hs256())

    assert identity.subject == "CX-1234"
    assert identity.tenant_id == "tenant-eu-1"
    assert identity.role is Role.CUSTOMER
    assert identity.scopes == frozenset({Scope.ACCOUNT_READ, Scope.SERVICE_READ})
    assert identity.token_id == "token-1"


async def test_a_tampered_token_is_refused(local: LocalVerifier) -> None:
    token = _hs256()
    head, payload, signature = token.split(".")

    with pytest.raises(TokenVerificationError):
        await local.verify(f"{head}.{payload}x.{signature}")


async def test_a_token_signed_with_another_secret_is_refused(local: LocalVerifier) -> None:
    forged = jwt.encode(_claims(), "another-secret-long-enough-for-hs256", algorithm="HS256")

    with pytest.raises(TokenVerificationError):
        await local.verify(forged)


async def test_an_unsigned_token_is_refused(local: LocalVerifier) -> None:
    # The classic alg=none attack.
    unsigned = jwt.encode(_claims(), key="", algorithm="none")

    with pytest.raises(TokenVerificationError):
        await local.verify(unsigned)


async def test_an_expired_token_is_refused(local: LocalVerifier) -> None:
    expired = _hs256(exp=int((datetime.now(UTC) - timedelta(seconds=1)).timestamp()))

    with pytest.raises(TokenVerificationError, match="expired"):
        await local.verify(expired)


async def test_a_token_for_another_audience_is_refused(local: LocalVerifier) -> None:
    with pytest.raises(TokenVerificationError):
        await local.verify(_hs256(aud="some-other-service"))


@pytest.mark.parametrize("missing", [TENANT_CLAIM, ROLE_CLAIM])
async def test_a_token_missing_a_required_claim_is_refused(
    local: LocalVerifier, missing: str
) -> None:
    with pytest.raises(TokenVerificationError):
        await local.verify(_hs256(**{missing: None}))


async def test_an_unknown_role_never_falls_back_to_a_permissive_default(
    local: LocalVerifier,
) -> None:
    with pytest.raises(TokenVerificationError, match="unknown role"):
        await local.verify(_hs256(**{ROLE_CLAIM: "superuser"}))


async def test_unrecognised_scopes_are_dropped_rather_than_rejected(
    local: LocalVerifier,
) -> None:
    # A token legitimately carries scopes for other services.
    identity = await local.verify(_hs256(scope="account:read some-other-service:admin"))

    assert identity.scopes == frozenset({Scope.ACCOUNT_READ})


async def test_garbage_is_refused_without_raising_something_unexpected(
    local: LocalVerifier,
) -> None:
    for candidate in ["", "not-a-token", "a.b.c", "..", "null"]:
        with pytest.raises(TokenVerificationError):
            await local.verify(candidate)


@pytest.mark.parametrize("secret", ["", "short", "x" * 31])
def test_a_weak_local_secret_is_rejected_at_construction(secret: str) -> None:
    with pytest.raises(ValueError, match="at least 32 bytes"):
        LocalVerifier(secret, clock=FrozenClock())


def test_a_secret_of_exactly_the_minimum_length_is_accepted() -> None:
    assert LocalVerifier("x" * 32, clock=FrozenClock())


# --- JWKS verification, exercised with a locally generated key pair, no network ------


@pytest.fixture(scope="module")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks_document(key: rsa.RSAPrivateKey, kid: str = "key-1") -> dict[str, Any]:
    from jwt.algorithms import RSAAlgorithm

    public = RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    public.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return {"keys": [public]}


def _rs256(key: rsa.RSAPrivateKey, kid: str = "key-1", **overrides: Any) -> str:
    return jwt.encode(_claims(**overrides), key, algorithm="RS256", headers={"kid": kid})


def _anchored_clock() -> FrozenClock:
    """A frozen clock at the real current time, so token expiry maths lines up."""
    return FrozenClock(datetime.now(UTC))


def _verifier(
    key: rsa.RSAPrivateKey, clock: FrozenClock, *, kid: str = "key-1", fail: bool = False
) -> tuple[JwksVerifier, dict[str, int]]:
    calls = {"count": 0}

    async def fetch() -> dict[str, Any]:
        calls["count"] += 1
        if fail:
            raise ConnectionError("jwks endpoint unreachable")
        return _jwks_document(key, kid)

    verifier = JwksVerifier(
        fetch_jwks=fetch, issuer=ISSUER, audience=AUDIENCE, clock=clock, cache_ttl_s=600.0
    )
    return verifier, calls


async def test_a_correctly_signed_rs256_token_verifies(rsa_key: rsa.RSAPrivateKey) -> None:
    verifier, _ = _verifier(rsa_key, _anchored_clock())

    identity = await verifier.verify(_rs256(rsa_key))

    assert identity.subject == "CX-1234"


async def test_the_key_set_is_fetched_once_and_then_cached(rsa_key: rsa.RSAPrivateKey) -> None:
    verifier, calls = _verifier(rsa_key, _anchored_clock())

    await verifier.verify(_rs256(rsa_key))
    await verifier.verify(_rs256(rsa_key))

    assert calls["count"] == 1


async def test_the_key_set_is_refetched_once_the_cache_expires(
    rsa_key: rsa.RSAPrivateKey,
) -> None:
    clock = _anchored_clock()
    verifier, calls = _verifier(rsa_key, clock)

    await verifier.verify(_rs256(rsa_key))
    clock.advance(601)
    await verifier.verify(_rs256(rsa_key))

    assert calls["count"] == 2


async def test_a_token_signed_by_an_unknown_key_is_refused(
    rsa_key: rsa.RSAPrivateKey,
) -> None:
    verifier, _ = _verifier(rsa_key, _anchored_clock())

    with pytest.raises(TokenVerificationError, match="no signing key"):
        await verifier.verify(_rs256(rsa_key, kid="rotated-away"))


async def test_an_absurdly_long_lived_token_is_refused(rsa_key: rsa.RSAPrivateKey) -> None:
    clock = _anchored_clock()
    verifier, _ = _verifier(rsa_key, clock)
    far_future = int((clock.now() + timedelta(days=30)).timestamp())

    with pytest.raises(TokenVerificationError, match="lifetime"):
        await verifier.verify(_rs256(rsa_key, exp=far_future))


async def test_an_unreachable_key_endpoint_fails_closed_when_nothing_is_cached(
    rsa_key: rsa.RSAPrivateKey,
) -> None:
    verifier, _ = _verifier(rsa_key, _anchored_clock(), fail=True)

    with pytest.raises(TokenVerificationError, match="unavailable"):
        await verifier.verify(_rs256(rsa_key))


async def test_a_provider_outage_does_not_become_our_outage_while_keys_are_cached(
    rsa_key: rsa.RSAPrivateKey,
) -> None:
    clock = _anchored_clock()
    failing = {"now": False}

    async def fetch() -> dict[str, Any]:
        if failing["now"]:
            raise ConnectionError("jwks endpoint unreachable")
        return _jwks_document(rsa_key)

    verifier = JwksVerifier(
        fetch_jwks=fetch,
        issuer=ISSUER,
        audience=AUDIENCE,
        clock=clock,
        cache_ttl_s=600.0,
        stale_if_error_s=3600.0,
    )
    await verifier.verify(_rs256(rsa_key))

    failing["now"] = True
    clock.advance(700)  # cache expired, but inside the stale-if-error window

    assert (await verifier.verify(_rs256(rsa_key))).subject == "CX-1234"

    clock.advance(4000)  # past the window: fail closed
    with pytest.raises(TokenVerificationError, match="unavailable"):
        await verifier.verify(_rs256(rsa_key))
