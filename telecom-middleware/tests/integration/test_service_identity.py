"""The API refuses a caller it cannot identify, whatever user token that caller holds.

Two credentials arrive on every request and each answers a different question. These
tests hold one constant while varying the other, because the property worth having is
that neither substitutes for the other.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from telecom_middleware.api.app import build_app
from telecom_middleware.api.container import build_context
from telecom_middleware.config.settings import load_settings
from telecom_middleware.repositories.memory import MemoryStore
from telecom_middleware.security.permissions import Role
from telecom_middleware.services.seed import seed_demo_data
from tests.integration.conftest import (
    BASE_ENV,
    CUSTOMER,
    TENANT,
    MovableClock,
    SequentialIds,
    auth,
    make_token,
)

SERVICE_CREDENTIAL = "the-tool-servers-own-credential"
SERVICE_HEADER = "X-Service-Authorization"

GUARDED_ENV = {
    **BASE_ENV,
    "TELECOM_MW_SERVICE_AUTH": "shared_secret",
    "TELECOM_MW_SERVICE_SHARED_SECRET": SERVICE_CREDENTIAL,
}


@pytest.fixture
async def guarded_client() -> AsyncIterator[httpx.AsyncClient]:
    """The application with service-credential checking switched on."""
    settings = load_settings(GUARDED_ENV)
    store = MemoryStore()
    clock = MovableClock()
    context = build_context(
        settings, store=store, clock=clock, ids=SequentialIds("id"), configure_logs=False
    )
    app = build_app(context, start_realtime=False)
    async with app.router.lifespan_context(app):
        await seed_demo_data(store, tenant_id=TENANT, clock=clock)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client


def _user() -> dict[str, str]:
    return auth(make_token(role=Role.CUSTOMER, cx_id=CUSTOMER))


def _service(credential: str = SERVICE_CREDENTIAL) -> dict[str, str]:
    return {SERVICE_HEADER: f"Bearer {credential}"}


PATH = f"/api/v1/customers/{CUSTOMER}"


async def test_a_known_service_with_a_valid_user_token_is_served(
    guarded_client: httpx.AsyncClient,
) -> None:
    response = await guarded_client.get(PATH, headers={**_user(), **_service()})

    assert response.status_code == 200
    assert response.json()["cx_id"] == CUSTOMER


async def test_a_valid_user_token_from_an_unknown_caller_is_refused(
    guarded_client: httpx.AsyncClient,
) -> None:
    """The point of the whole exercise: a stolen customer token is worth nothing
    replayed from somewhere the API does not recognise."""
    response = await guarded_client.get(PATH, headers={**_user(), **_service("not-our-credential")})

    assert response.status_code == 401
    assert response.json()["code"] == "service_not_recognised"


async def test_omitting_the_service_credential_says_which_header_is_missing(
    guarded_client: httpx.AsyncClient,
) -> None:
    """Still refused, but with the one code an operator can act on immediately.

    Sending nothing and sending the wrong thing are the same refusal to an attacker
    and completely different incidents to whoever is on call, so they carry different
    codes. This one names the header; the wrong-credential case below stays opaque.
    """
    response = await guarded_client.get(PATH, headers=_user())

    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "service_credential_missing"
    assert body["detail"]["header"] == SERVICE_HEADER


async def test_a_wrong_credential_is_not_told_apart_from_any_other_wrong_credential(
    guarded_client: httpx.AsyncClient,
) -> None:
    """The absence is named; the value never is. Otherwise this is a guessing oracle."""
    first = await guarded_client.get(PATH, headers={**_user(), **_service("wrong-a")})
    second = await guarded_client.get(PATH, headers={**_user(), **_service("wrong-b-longer")})

    assert first.json()["code"] == second.json()["code"] == "service_not_recognised"
    assert "detail" not in first.json()


async def test_a_known_service_still_needs_a_user_token(
    guarded_client: httpx.AsyncClient,
) -> None:
    """The service credential authenticates the robot, never the person. This is the
    property that makes a leaked service credential insufficient on its own."""
    response = await guarded_client.get(PATH, headers=_service())

    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"


async def test_the_service_is_checked_before_the_user_token_is_read(
    guarded_client: httpx.AsyncClient,
) -> None:
    """An unknown caller is turned away without the API looking at, or reporting on,
    the token it presented - so it cannot be used as a token-validity oracle."""
    response = await guarded_client.get(
        PATH,
        headers={"Authorization": "Bearer utter-nonsense", **_service("also-nonsense")},
    )

    assert response.json()["code"] == "service_not_recognised"


async def test_liveness_and_readiness_carry_no_credential_of_any_kind(
    guarded_client: httpx.AsyncClient,
) -> None:
    """An orchestrator probes before any identity exists. A probe that can fail on
    configuration reports a healthy service as down."""
    assert (await guarded_client.get("/healthz")).status_code == 200
    assert (await guarded_client.get("/readyz")).status_code in (200, 503)


async def test_every_api_route_is_behind_the_service_check(
    guarded_client: httpx.AsyncClient,
) -> None:
    """Applied to the routers, not to each endpoint, so a new endpoint inherits it
    rather than needing to remember it."""
    for method, path in (
        ("GET", f"/api/v1/customers/{CUSTOMER}/invoices"),
        ("POST", f"/api/v1/customers/{CUSTOMER}/tickets"),
        ("GET", "/api/v1/approvals"),
        ("PUT", "/api/v1/cases"),
        ("GET", "/api/v1/audit"),
        ("POST", "/api/v1/assignments"),
        ("GET", "/api/v1/stream"),
    ):
        response = await guarded_client.request(method, path, headers=_user(), json={})
        assert response.json()["code"] == "service_credential_missing", f"{method} {path}"


async def test_the_default_configuration_does_not_require_a_service_credential(
    client: httpx.AsyncClient, seeded: object
) -> None:
    """A single-host deployment on loopback should not have to provision a second
    credential before it can start. Production refuses this default; the validator
    covers that."""
    del seeded
    response = await client.get(PATH, headers=_user())

    assert response.status_code == 200
