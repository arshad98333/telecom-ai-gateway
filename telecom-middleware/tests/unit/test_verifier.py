"""Identity is the control everything rests on, so the verifier is attacked, not exercised."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from telecom_middleware.security.permissions import Role, Scope
from telecom_middleware.security.verifier import (
    JwksVerifier,
    LocalVerifier,
    TokenVerificationError,
)

SECRET = "verifier-test-secret-long-enough-for-hs256"
AUDIENCE = "https://api.telecom.example/v1"
ISSUER = "https://tenant.example.invalid/"
NAMESPACE = "https://telecom.example/"


def claims(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "sub": "auth0|customer-1",
        "aud": AUDIENCE,
        "iss": ISSUER,
        "exp": int((datetime.now(UTC) + timedelta(minutes=10)).timestamp()),
        "jti": "tok-1",
        "permissions": ["account:read", "billing:read", "not-a-known-scope"],
        f"{NAMESPACE}tenant_id": "tenant-eu-1",
        f"{NAMESPACE}role": "customer",
        f"{NAMESPACE}cx_id": "CX-1234",
    }
    base.update(overrides)
    return {key: value for key, value in base.items() if value is not None}


def hs256(**overrides: Any) -> str:
    return jwt.encode(claims(**overrides), SECRET, algorithm="HS256")


@pytest.fixture
def local() -> LocalVerifier:
    return LocalVerifier(SECRET, audience=AUDIENCE, namespace=NAMESPACE)


async def test_a_valid_token_produces_the_expected_principal(local: LocalVerifier) -> None:
    principal = await local.verify(hs256())

    assert principal.subject == "auth0|customer-1"
    assert principal.tenant_id == "tenant-eu-1"
    assert principal.role is Role.CUSTOMER
    assert principal.cx_id == "CX-1234"
    assert principal.scopes == frozenset({Scope.ACCOUNT_READ, Scope.BILLING_READ})
    assert principal.is_service is False


async def test_permissions_for_other_apis_are_dropped_not_rejected(
    local: LocalVerifier,
) -> None:
    # A token legitimately carries permissions for other APIs.
    principal = await local.verify(hs256(permissions=["account:read", "crm:admin"]))

    assert principal.scopes == frozenset({Scope.ACCOUNT_READ})


async def test_the_scope_claim_is_read_when_permissions_is_absent(
    local: LocalVerifier,
) -> None:
    principal = await local.verify(hs256(permissions=None, scope="account:read service:read"))

    assert principal.scopes == frozenset({Scope.ACCOUNT_READ, Scope.SERVICE_READ})


@pytest.mark.parametrize("bad", ["", "not-a-token", "a.b.c", "..", "null"])
async def test_garbage_is_refused(local: LocalVerifier, bad: str) -> None:
    with pytest.raises(TokenVerificationError):
        await local.verify(bad)


async def test_a_tampered_payload_is_refused(local: LocalVerifier) -> None:
    head, payload, signature = hs256().split(".")

    with pytest.raises(TokenVerificationError):
        await local.verify(f"{head}.{payload}x.{signature}")


async def test_a_token_signed_with_another_secret_is_refused(local: LocalVerifier) -> None:
    forged = jwt.encode(claims(), "a-different-secret-long-enough-here", algorithm="HS256")

    with pytest.raises(TokenVerificationError):
        await local.verify(forged)


async def test_an_unsigned_token_is_refused(local: LocalVerifier) -> None:
    with pytest.raises(TokenVerificationError):
        await local.verify(jwt.encode(claims(), key="", algorithm="none"))


async def test_an_expired_token_says_so(local: LocalVerifier) -> None:
    expired = hs256(exp=int((datetime.now(UTC) - timedelta(seconds=1)).timestamp()))

    with pytest.raises(TokenVerificationError, match="expired"):
        await local.verify(expired)


async def test_a_token_for_another_audience_is_refused(local: LocalVerifier) -> None:
    with pytest.raises(TokenVerificationError):
        await local.verify(hs256(aud="https://some-other-api/"))


async def test_a_token_with_no_tenant_is_refused(local: LocalVerifier) -> None:
    with pytest.raises(TokenVerificationError, match="no tenant"):
        await local.verify(hs256(**{f"{NAMESPACE}tenant_id": None}))


async def test_an_unknown_role_never_falls_back_to_something_permissive(
    local: LocalVerifier,
) -> None:
    with pytest.raises(TokenVerificationError, match="unknown role"):
        await local.verify(hs256(**{f"{NAMESPACE}role": "superuser"}))


async def test_a_customer_token_without_a_customer_reference_is_refused(
    local: LocalVerifier,
) -> None:
    # Without it there is nothing to check the requested account against.
    with pytest.raises(TokenVerificationError, match="customer reference"):
        await local.verify(hs256(**{f"{NAMESPACE}cx_id": None}))


async def test_a_client_credentials_token_is_recognised_as_a_service(
    local: LocalVerifier,
) -> None:
    principal = await local.verify(
        hs256(
            sub="mcp@clients",
            gty="client-credentials",
            **{f"{NAMESPACE}role": None, f"{NAMESPACE}cx_id": None},
        )
    )

    assert principal.is_service is True
    assert principal.role is Role.SERVICE
    assert principal.scopes == frozenset(), "the service role holds nothing of its own"


async def test_a_subject_ending_in_clients_is_treated_as_a_service_too(
    local: LocalVerifier,
) -> None:
    principal = await local.verify(
        hs256(sub="something@clients", **{f"{NAMESPACE}role": None, f"{NAMESPACE}cx_id": None})
    )

    assert principal.is_service is True


@pytest.mark.parametrize("secret", ["", "short", "x" * 31])
def test_a_weak_local_secret_is_rejected_at_construction(secret: str) -> None:
    with pytest.raises(ValueError, match="at least 32 bytes"):
        LocalVerifier(secret, audience=AUDIENCE, namespace=NAMESPACE)


# --- JWKS, exercised with a locally generated key pair and no network ----------------


@pytest.fixture(scope="module")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwks_document(key: rsa.RSAPrivateKey, kid: str = "key-1") -> dict[str, Any]:
    from jwt.algorithms import RSAAlgorithm

    public = RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    public.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return {"keys": [public]}


def rs256(key: rsa.RSAPrivateKey, kid: str = "key-1", **overrides: Any) -> str:
    return jwt.encode(claims(**overrides), key, algorithm="RS256", headers={"kid": kid})


class FakeMonotonic:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value


def build(
    key: rsa.RSAPrivateKey,
    now: FakeMonotonic,
    *,
    published: dict[str, str] | None = None,
    fail: bool = False,
) -> tuple[JwksVerifier, dict[str, int]]:
    calls = {"count": 0}
    state = published if published is not None else {"kid": "key-1"}

    async def fetch() -> dict[str, Any]:
        calls["count"] += 1
        if fail:
            raise ConnectionError("jwks endpoint unreachable")
        return jwks_document(key, state["kid"])

    verifier = JwksVerifier(
        fetch_jwks=fetch,
        issuer=ISSUER,
        audience=AUDIENCE,
        namespace=NAMESPACE,
        now=now,
        cache_ttl_s=600.0,
    )
    return verifier, calls


async def test_a_correctly_signed_token_verifies(rsa_key: rsa.RSAPrivateKey) -> None:
    verifier, _ = build(rsa_key, FakeMonotonic())

    assert (await verifier.verify(rs256(rsa_key))).cx_id == "CX-1234"


async def test_the_key_set_is_fetched_once_and_cached(rsa_key: rsa.RSAPrivateKey) -> None:
    verifier, calls = build(rsa_key, FakeMonotonic())

    await verifier.verify(rs256(rsa_key))
    await verifier.verify(rs256(rsa_key))

    assert calls["count"] == 1


async def test_the_key_set_is_refetched_once_the_cache_expires(
    rsa_key: rsa.RSAPrivateKey,
) -> None:
    now = FakeMonotonic()
    verifier, calls = build(rsa_key, now)
    await verifier.verify(rs256(rsa_key))

    now.value += 601
    await verifier.verify(rs256(rsa_key))

    assert calls["count"] == 2


async def test_a_rotated_key_is_picked_up_by_one_refresh_not_one_per_call(
    rsa_key: rsa.RSAPrivateKey,
) -> None:
    published = {"kid": "key-1"}
    verifier, calls = build(rsa_key, FakeMonotonic(), published=published)
    await verifier.verify(rs256(rsa_key, kid="key-1"))

    published["kid"] = "key-2"
    await verifier.verify(rs256(rsa_key, kid="key-2"))

    assert calls["count"] == 2


async def test_an_unknown_key_is_refused_after_one_refresh(rsa_key: rsa.RSAPrivateKey) -> None:
    verifier, calls = build(rsa_key, FakeMonotonic())

    with pytest.raises(TokenVerificationError, match="no signing key"):
        await verifier.verify(rs256(rsa_key, kid="never-published"))

    assert calls["count"] == 2, "one refresh, not one fetch per bad token"


async def test_a_token_with_no_key_identifier_is_refused(rsa_key: rsa.RSAPrivateKey) -> None:
    verifier, _ = build(rsa_key, FakeMonotonic())

    with pytest.raises(TokenVerificationError, match="no key identifier"):
        await verifier.verify(jwt.encode(claims(), rsa_key, algorithm="RS256"))


async def test_a_malformed_header_is_refused(rsa_key: rsa.RSAPrivateKey) -> None:
    verifier, _ = build(rsa_key, FakeMonotonic())

    with pytest.raises(TokenVerificationError, match="header is malformed"):
        await verifier.verify("not-a-jwt")


async def test_a_signature_from_another_key_is_refused(rsa_key: rsa.RSAPrivateKey) -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier, _ = build(rsa_key, FakeMonotonic())

    with pytest.raises(TokenVerificationError, match="could not be verified"):
        await verifier.verify(rs256(other, kid="key-1"))


async def test_an_absurdly_long_lived_token_is_refused(rsa_key: rsa.RSAPrivateKey) -> None:
    verifier, _ = build(rsa_key, FakeMonotonic())
    far_future = int((datetime.now(UTC) + timedelta(days=30)).timestamp())

    with pytest.raises(TokenVerificationError, match="lifetime"):
        await verifier.verify(rs256(rsa_key, exp=far_future))


async def test_an_unreachable_key_endpoint_fails_closed_when_nothing_is_cached(
    rsa_key: rsa.RSAPrivateKey,
) -> None:
    verifier, _ = build(rsa_key, FakeMonotonic(), fail=True)

    with pytest.raises(TokenVerificationError, match="unavailable"):
        await verifier.verify(rs256(rsa_key))


async def test_a_provider_outage_does_not_become_ours_until_the_window_closes(
    rsa_key: rsa.RSAPrivateKey,
) -> None:
    now = FakeMonotonic()
    failing = {"now": False}

    async def fetch() -> dict[str, Any]:
        if failing["now"]:
            raise ConnectionError("jwks endpoint unreachable")
        return jwks_document(rsa_key)

    verifier = JwksVerifier(
        fetch_jwks=fetch,
        issuer=ISSUER,
        audience=AUDIENCE,
        namespace=NAMESPACE,
        now=now,
        cache_ttl_s=600.0,
        stale_if_error_s=3600.0,
    )
    await verifier.verify(rs256(rsa_key))

    failing["now"] = True
    now.value += 700  # cache expired, inside the stale-if-error window
    assert (await verifier.verify(rs256(rsa_key))).cx_id == "CX-1234"

    now.value += 4000  # past the window
    with pytest.raises(TokenVerificationError, match="unavailable"):
        await verifier.verify(rs256(rsa_key))
