"""No endpoint may ship unprotected by omission.

This enumerates the application's real routes rather than a list someone maintains by
hand, so adding an endpoint without a permission fails here rather than in production.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from tests.integration.conftest import CUSTOMER, Harness

#: Endpoints that are deliberately open, and why.
PUBLIC_ROUTES = {
    ("/healthz", "GET"),  # liveness, consulted by the orchestrator before any identity exists
    ("/readyz", "GET"),  # readiness, same
    ("/openapi.json", "GET"),
    ("/docs", "GET"),
    ("/docs/oauth2-redirect", "GET"),
    ("/redoc", "GET"),
}


def _routes(app: FastAPI) -> list[APIRoute]:
    return [route for route in app.routes if isinstance(route, APIRoute)]


def test_every_route_declares_the_permission_it_needs(harness: Harness) -> None:
    unprotected: list[str] = []
    for route in _routes(harness.app):
        for method in sorted(route.methods or set()):
            if (route.path, method) in PUBLIC_ROUTES:
                continue
            # The scopes are attached to the dependency callable that `requires` built,
            # which FastAPI records somewhere in the route's dependency tree.
            if not _declares_scope(route):
                unprotected.append(f"{method} {route.path}")

    assert unprotected == [], f"routes with no declared permission: {unprotected}"


def _declares_scope(route: APIRoute) -> bool:
    stack = list(route.dependant.dependencies)
    while stack:
        dependency = stack.pop()
        if dependency.call is not None and hasattr(dependency.call, "__telecom_scopes__"):
            return True
        stack.extend(dependency.dependencies)
    return False


def test_the_only_open_routes_are_the_ones_listed(harness: Harness) -> None:
    # If a future change makes something public, it has to be added to the list above,
    # where a reviewer will see it.
    paths = {
        (route.path, method)
        for route in _routes(harness.app)
        for method in (route.methods or set())
    }
    open_paths = {entry for entry in PUBLIC_ROUTES if entry in paths}

    assert open_paths <= PUBLIC_ROUTES


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", f"/api/v1/customers/{CUSTOMER}"),
        ("GET", f"/api/v1/customers/{CUSTOMER}/services"),
        ("GET", f"/api/v1/customers/{CUSTOMER}/orders"),
        ("GET", f"/api/v1/customers/{CUSTOMER}/invoices"),
        ("GET", f"/api/v1/customers/{CUSTOMER}/network"),
        ("GET", f"/api/v1/customers/{CUSTOMER}/tickets"),
        ("POST", f"/api/v1/customers/{CUSTOMER}/tickets"),
        ("POST", f"/api/v1/customers/{CUSTOMER}/callbacks"),
        ("POST", f"/api/v1/customers/{CUSTOMER}/refund-approvals"),
        ("GET", "/api/v1/approvals"),
        ("PUT", "/api/v1/cases"),
        ("GET", "/api/v1/audit"),
        ("POST", "/api/v1/assignments"),
        ("GET", "/api/v1/stream"),
    ],
)
async def test_no_endpoint_answers_without_a_token(
    client: httpx.AsyncClient, method: str, path: str
) -> None:
    response = await client.request(method, path, json={})

    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"


async def test_a_malformed_authorization_header_is_refused(client: httpx.AsyncClient) -> None:
    for header in (
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer   "},
    ):
        response = await client.get(f"/api/v1/customers/{CUSTOMER}", headers=header)
        assert response.status_code == 401


async def test_a_forged_token_is_refused(client: httpx.AsyncClient) -> None:
    import jwt

    forged = jwt.encode(
        {"sub": "auth0|attacker"}, "another-secret-long-enough-for-hs256", algorithm="HS256"
    )

    response = await client.get(
        f"/api/v1/customers/{CUSTOMER}", headers={"Authorization": f"Bearer {forged}"}
    )

    assert response.status_code == 401
    assert response.json()["code"] in ("token_invalid", "unauthenticated")


async def test_an_expired_token_is_distinguished_so_a_client_knows_to_refresh(
    client: httpx.AsyncClient, harness: Harness
) -> None:
    response = await client.get(
        f"/api/v1/customers/{CUSTOMER}", headers=harness.headers(expires_in_s=-1)
    )

    assert response.status_code == 401
    assert response.json()["code"] == "token_expired"
