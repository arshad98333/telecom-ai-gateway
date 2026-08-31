"""This service's own credential, and keeping it current.

The credential proves *which service* is calling; the customer's token, carried
separately, proves who the call is for. In production it is an Auth0
client-credentials access token with a short life - fifteen minutes, in this tenant -
so a value pasted into configuration stops working shortly after it is pasted. That is
the whole reason this module exists rather than a string in ``Settings``.

``StaticServiceToken`` returns a configured string. Development, and any deployment
where the credential is a long-lived shared secret rather than a token.

``ClientCredentialsServiceToken`` fetches from the identity provider and holds the
result until shortly before it expires. Three properties matter and each is tested:

* one refresh at a time. Under load, a token expiring would otherwise start as many
  identical fetches as there are requests in flight, which is how a dependency gets
  rate-limited at exactly the moment it is needed.
* refreshed *before* expiry, not after a failure. Waiting for a 401 to discover the
  token died turns every rotation into a burst of failed customer requests.
* an outage does not immediately become ours: a fetch that fails while the current
  token is still valid is logged and the current token is used. Once it has actually
  expired, this fails rather than sending a credential known to be dead.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

import httpx

from telecom_mcp.domain.errors import BackendError
from telecom_mcp.domain.ports import Clock
from telecom_mcp.observability.logging import get_logger

logger = get_logger(__name__)

#: Refresh this long before expiry, so a request in flight never carries a token that
#: expires between leaving here and being verified there.
DEFAULT_REFRESH_MARGIN_S = 60.0

#: A provider that returns something absurdly short is misconfigured; treating it as
#: valid would mean fetching on every request.
MIN_USABLE_LIFETIME_S = 30.0

TOKEN_REQUEST_TIMEOUT_S = 10.0


class ServiceTokenProvider(Protocol):
    """Returns the credential to present as this service's own identity."""

    async def token(self) -> str: ...


class StaticServiceToken:
    """A configured string, returned unchanged."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    async def token(self) -> str:
        return self._value


class ClientCredentialsServiceToken:
    """An OAuth2 client-credentials token, cached until shortly before it expires."""

    def __init__(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        audience: str,
        clock: Clock,
        client: httpx.AsyncClient | None = None,
        refresh_margin_s: float = DEFAULT_REFRESH_MARGIN_S,
    ) -> None:
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._audience = audience
        self._clock = clock
        self._client = client
        self._refresh_margin_s = refresh_margin_s
        self._lock = asyncio.Lock()
        self._value: str | None = None
        self._expires_at: float = 0.0

    async def token(self) -> str:
        if self._value is not None and self._clock.monotonic() < self._usable_until():
            return self._value

        async with self._lock:
            # Re-check inside the lock: while waiting, another caller may have
            # refreshed, and a second fetch would be pure waste.
            if self._value is not None and self._clock.monotonic() < self._usable_until():
                return self._value
            return await self._refresh()

    def _usable_until(self) -> float:
        return self._expires_at - self._refresh_margin_s

    async def _refresh(self) -> str:
        try:
            payload = await self._fetch()
        except Exception as exc:
            # The token we hold may still be valid even though the refresh failed.
            # Serving it beats failing every customer request through a provider blip.
            if self._value is not None and self._clock.monotonic() < self._expires_at:
                logger.warning(
                    "service_token_refresh_failed_using_current", error=type(exc).__name__
                )
                return self._value
            logger.error("service_token_unavailable", error=type(exc).__name__)
            raise BackendError(operation="service_token") from exc

        value = payload.get("access_token")
        expires_in = payload.get("expires_in")
        if not isinstance(value, str) or not value:
            raise BackendError(operation="service_token")
        if not isinstance(expires_in, (int, float, str)) or isinstance(expires_in, bool):
            raise BackendError(operation="service_token")
        try:
            lifetime = float(expires_in)
        except ValueError as exc:
            raise BackendError(operation="service_token") from exc
        if lifetime < MIN_USABLE_LIFETIME_S:
            raise BackendError(operation="service_token")

        self._value = value
        self._expires_at = self._clock.monotonic() + lifetime
        logger.info("service_token_refreshed", lifetime_s=int(lifetime))
        return value

    async def _fetch(self) -> dict[str, Any]:
        body = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "audience": self._audience,
        }
        if self._client is not None:
            response = await self._client.post(self._token_url, json=body)
        else:
            async with httpx.AsyncClient(timeout=httpx.Timeout(TOKEN_REQUEST_TIMEOUT_S)) as client:
                response = await client.post(self._token_url, json=body)
        response.raise_for_status()
        document: dict[str, Any] = response.json()
        return document
