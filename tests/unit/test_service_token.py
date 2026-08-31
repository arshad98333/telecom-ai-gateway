"""This service's own credential: fetched, cached, refreshed before it dies."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
import respx

from telecom_mcp.domain.errors import BackendError
from telecom_mcp.security.service_token import (
    ClientCredentialsServiceToken,
    StaticServiceToken,
)
from tests.fakes import FrozenClock

TOKEN_URL = "https://tenant.example.invalid/oauth/token"
AUDIENCE = "https://api.telecom.example/v1"


def provider(clock: FrozenClock, **overrides: Any) -> ClientCredentialsServiceToken:
    return ClientCredentialsServiceToken(
        token_url=TOKEN_URL,
        client_id="mcp-client-id",
        client_secret="mcp-client-secret",
        audience=AUDIENCE,
        clock=clock,
        **overrides,
    )


def grant(value: str, expires_in: int = 900) -> httpx.Response:
    return httpx.Response(200, json={"access_token": value, "expires_in": expires_in})


async def test_a_static_credential_is_returned_unchanged() -> None:
    assert await StaticServiceToken("a-shared-secret").token() == "a-shared-secret"


@respx.mock
async def test_the_credential_is_fetched_once_and_reused() -> None:
    route = respx.post(TOKEN_URL).mock(return_value=grant("token-1"))
    clock = FrozenClock()
    tokens = provider(clock)

    assert await tokens.token() == "token-1"
    assert await tokens.token() == "token-1"
    assert await tokens.token() == "token-1"

    assert route.call_count == 1, "a cached credential must not be refetched"


@respx.mock
async def test_the_request_carries_what_the_provider_expects() -> None:
    route = respx.post(TOKEN_URL).mock(return_value=grant("token-1"))
    await provider(FrozenClock()).token()

    body = json.loads(route.calls.last.request.read())
    assert body == {
        "grant_type": "client_credentials",
        "client_id": "mcp-client-id",
        "client_secret": "mcp-client-secret",
        "audience": AUDIENCE,
    }


@respx.mock
async def test_it_refreshes_before_expiry_rather_than_after_a_failure() -> None:
    """Waiting for a 401 to discover the credential died turns every rotation into a
    burst of failed customer calls."""
    respx.post(TOKEN_URL).mock(side_effect=[grant("token-1", 900), grant("token-2", 900)])
    clock = FrozenClock()
    tokens = provider(clock, refresh_margin_s=60.0)

    assert await tokens.token() == "token-1"
    # 900s life, 60s margin: usable until 840s, so 850s is inside the margin while the
    # credential itself is still valid for another fifty seconds.
    clock.advance(850)
    assert await tokens.token() == "token-2"


@respx.mock
async def test_a_credential_still_comfortably_valid_is_not_refreshed() -> None:
    route = respx.post(TOKEN_URL).mock(return_value=grant("token-1", 900))
    clock = FrozenClock()
    tokens = provider(clock, refresh_margin_s=60.0)

    await tokens.token()
    clock.advance(600)
    await tokens.token()

    assert route.call_count == 1


@respx.mock
async def test_concurrent_callers_cause_one_fetch_not_many() -> None:
    """A credential expiring under load would otherwise start as many identical
    fetches as there are requests in flight - and be rate-limited exactly when needed."""
    route = respx.post(TOKEN_URL).mock(return_value=grant("token-1"))
    tokens = provider(FrozenClock())

    results = await asyncio.gather(*(tokens.token() for _ in range(25)))

    assert set(results) == {"token-1"}
    assert route.call_count == 1


@respx.mock
async def test_a_provider_outage_does_not_immediately_become_ours() -> None:
    respx.post(TOKEN_URL).mock(
        side_effect=[grant("token-1", 900), httpx.Response(503), httpx.Response(503)]
    )
    clock = FrozenClock()
    tokens = provider(clock, refresh_margin_s=60.0)

    assert await tokens.token() == "token-1"
    clock.advance(860)  # inside the margin, so a refresh is attempted and fails

    assert await tokens.token() == "token-1", "the current credential is still valid"


@respx.mock
async def test_once_the_credential_has_actually_expired_it_fails_rather_than_lying() -> None:
    respx.post(TOKEN_URL).mock(side_effect=[grant("token-1", 900), httpx.Response(503)])
    clock = FrozenClock()
    tokens = provider(clock)

    await tokens.token()
    clock.advance(1000)  # past expiry

    with pytest.raises(BackendError):
        await tokens.token()


@respx.mock
async def test_a_first_fetch_that_fails_is_an_error_not_an_empty_credential() -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(401))

    with pytest.raises(BackendError):
        await provider(FrozenClock()).token()


@respx.mock
@pytest.mark.parametrize(
    "payload",
    [
        {"expires_in": 900},  # no token
        {"access_token": "", "expires_in": 900},  # empty token
        {"access_token": "t"},  # no lifetime
        {"access_token": "t", "expires_in": "soon"},  # unusable lifetime
        {"access_token": "t", "expires_in": 5},  # so short it would refetch every call
    ],
)
async def test_a_malformed_grant_is_refused_rather_than_used(payload: dict[str, Any]) -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=payload))

    with pytest.raises(BackendError):
        await provider(FrozenClock()).token()
