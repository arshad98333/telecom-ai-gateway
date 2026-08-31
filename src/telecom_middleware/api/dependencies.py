"""Request-scoped dependencies: identity, permission, and the correlation identifier.

Two rules make this file worth reading.

**Every route declares the permission it needs**, as a dependency, and a test enumerates
the application's routes and fails if any route lacks one. A new endpoint therefore
cannot ship unprotected by forgetting - it has to be forgotten *and* the test deleted.

**Authentication and authorization are separate steps.** ``current_principal`` proves
who is calling; ``requires`` proves what they may do; the access module proves which
records they may touch. Collapsing them is how an endpoint ends up checking the first
two and quietly skipping the third.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Header, Request

from telecom_middleware.api.context import AppContext
from telecom_middleware.domain.errors import (
    AuthenticationError,
    ServiceCredentialMissingError,
    ServiceNotRecognisedError,
    TokenExpiredError,
    TokenInvalidError,
)
from telecom_middleware.observability.logging import get_logger
from telecom_middleware.security.access import require_scope
from telecom_middleware.security.permissions import Scope
from telecom_middleware.security.principal import Principal
from telecom_middleware.security.service_credential import (
    SERVICE_CREDENTIAL_HEADER,
    MissingServiceCredentialError,
    ServiceCaller,
    ServiceCredentialError,
)
from telecom_middleware.security.verifier import TokenVerificationError

logger = get_logger(__name__)

BEARER_PREFIX = "Bearer "
CORRELATION_HEADER = "X-Correlation-Id"
IDEMPOTENCY_HEADER = "Idempotency-Key"
MAX_CORRELATION_LENGTH = 128


def get_context(request: Request) -> AppContext:
    context: AppContext = request.app.state.context
    return context


AppContextDep = Annotated[AppContext, Depends(get_context)]


def get_correlation_id(request: Request) -> str:
    """The identifier that ties every log line, audit record and event together.

    Taken from the caller when they supply one - so a trace crosses the MCP server and
    this service as a single story - and generated when they do not. Bounded and
    sanitised, because it is echoed into logs and headers.
    """
    supplied = request.headers.get(CORRELATION_HEADER, "")
    cleaned = "".join(c for c in supplied if c.isalnum() or c in "-_.")[:MAX_CORRELATION_LENGTH]
    if cleaned:
        return cleaned
    context: AppContext = request.app.state.context
    return str(context.ids.new_id())


CorrelationIdDep = Annotated[str, Depends(get_correlation_id)]


async def current_principal(
    request: Request,
    context: AppContextDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """Verify the bearer token and return who is calling. Never trusts a claim alone.

    The principal is also attached to the request, so the error handler can audit a
    refusal without every handler having to remember to do it.
    """
    if not authorization or not authorization.startswith(BEARER_PREFIX):
        raise AuthenticationError("no bearer token presented")
    token = authorization[len(BEARER_PREFIX) :].strip()
    if not token:
        raise AuthenticationError("empty bearer token")
    try:
        principal = await context.verifier.verify(token)
    except TokenVerificationError as exc:
        if "expired" in str(exc):
            raise TokenExpiredError(str(exc)) from exc
        raise TokenInvalidError(str(exc)) from exc
    request.state.principal = principal
    return principal


PrincipalDep = Annotated[Principal, Depends(current_principal)]


async def verified_service(
    request: Request,
    context: AppContextDep,
    credential: Annotated[str | None, Header(alias=SERVICE_CREDENTIAL_HEADER)] = None,
) -> ServiceCaller:
    """Prove which service is calling, before asking who the call is for.

    Applied to every API router rather than to each endpoint, because "which service"
    is a property of the connection, not of the operation - and a per-route version
    would be one more thing a new endpoint could forget.

    A person's token and a service's credential are checked independently and neither
    substitutes for the other: this refuses an unknown caller carrying a perfectly
    valid user token, and ``require_human`` refuses a service credential presented as
    a person.
    """
    try:
        caller = await context.service_credentials.verify(credential)
    except MissingServiceCredentialError as exc:
        # An absent header is a configuration mistake, not an attack, and saying so
        # costs nothing: the caller learns a header exists, which the OpenAPI document
        # already says. A *wrong* credential still falls through to the opaque refusal
        # below, so this remains useless for guessing the secret.
        logger.warning(
            "service_credential_missing",
            path=request.url.path,
            header=SERVICE_CREDENTIAL_HEADER,
        )
        raise ServiceCredentialMissingError(
            str(exc),
            detail={
                "header": SERVICE_CREDENTIAL_HEADER,
                "hint": (
                    "This API expects two credentials: the caller's own token in "
                    "Authorization, and the calling service's credential in "
                    f"{SERVICE_CREDENTIAL_HEADER}. Neither substitutes for the other."
                ),
            },
        ) from exc
    except ServiceCredentialError as exc:
        # Logged rather than audited: there is no verified identity to attribute it to
        # at this point, and inventing one would put a fiction in the audit trail.
        logger.warning("service_credential_refused", path=request.url.path, reason=str(exc))
        raise ServiceNotRecognisedError(str(exc)) from exc
    request.state.service_caller = caller
    return caller


ServiceCallerDep = Annotated[ServiceCaller, Depends(verified_service)]


def requires(*scopes: Scope) -> Callable[[Principal], Awaitable[Principal]]:
    """A dependency that refuses unless the caller holds every listed permission.

    Returned rather than written inline so the route decorator reads as the permission
    it needs, and so the route-coverage test can find it.
    """

    async def dependency(principal: PrincipalDep) -> Principal:
        for scope in scopes:
            require_scope(principal, scope)
        return principal

    dependency.__telecom_scopes__ = tuple(scopes)  # type: ignore[attr-defined]
    return dependency


def idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias=IDEMPOTENCY_HEADER)] = None,
) -> str | None:
    """The caller's deduplication key, when they sent one.

    Required by the write endpoints themselves rather than here, so the error names the
    operation the caller was attempting.
    """
    return idempotency_key


IdempotencyKeyDep = Annotated[str | None, Depends(idempotency_key)]
