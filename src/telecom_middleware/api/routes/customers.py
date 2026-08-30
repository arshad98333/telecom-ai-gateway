"""Customer reads, and the passcode authentication the voice agent depends on.

Every handler answers the same three questions in the same order: who is calling
(the dependency), what may they do (the ``requires`` dependency), and which account may
they touch (``require_account_access``). Skipping the third is the mistake this layout
is arranged to make visible.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from telecom_middleware.api.context import AppContext
from telecom_middleware.api.dependencies import AppContextDep, CorrelationIdDep, requires
from telecom_middleware.api.schemas import (
    MAX_PAGE_SIZE,
    AccountResponse,
    AuthenticateRequest,
    AuthenticateResponse,
    InvoiceResponse,
    InvoicesResponse,
    NetworkStatusResponse,
    OrderResponse,
    OrdersResponse,
    ServiceResponse,
    ServicesResponse,
)
from telecom_middleware.domain.errors import NotFoundError
from telecom_middleware.domain.events import EventType
from telecom_middleware.domain.models import Customer
from telecom_middleware.domain.money import Currency
from telecom_middleware.security.access import require_account_access
from telecom_middleware.security.permissions import Scope
from telecom_middleware.security.principal import Principal
from telecom_middleware.services.passcode import (
    AuthenticationOutcome,
    burn_equivalent_time,
    check_lockout,
    result_or_raise,
    verify_passcode,
)

router = APIRouter(prefix="/customers", tags=["customer"])

_OPEN_STATES = frozenset({"open", "queued", "in_progress"})

CxIdPath = Annotated[str, Path(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")]
LimitQuery = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]


async def _account(context: AppContext, principal: Principal, cx_id: str) -> Customer:
    """Load an account the caller is entitled to, or refuse indistinguishably."""
    await require_account_access(principal, cx_id, context.store.assignments)
    customer: Customer | None = await context.store.customers.get(principal.tenant_id, cx_id)
    if customer is None:
        # Same error, same wording as a permission failure: whether a customer exists
        # is itself information.
        raise NotFoundError("no such customer")
    return customer


@router.post(
    "/{cx_id}/authenticate",
    response_model=AuthenticateResponse,
    summary="Verify a customer's four-digit account passcode",
)
async def authenticate(
    cx_id: CxIdPath,
    body: AuthenticateRequest,
    context: AppContextDep,
    correlation_id: CorrelationIdDep,
    principal: Annotated[Principal, Depends(requires(Scope.ACCOUNT_READ))],
) -> AuthenticateResponse:
    """Authenticate a customer. The passcode is never logged, stored or echoed."""
    await require_account_access(principal, cx_id, context.store.assignments)
    now = context.clock.now()
    customer = await context.store.customers.get(principal.tenant_id, cx_id)

    if customer is None:
        # Spend the same work as a real verification, so an unknown customer does not
        # answer faster than a known one and become enumerable.
        burn_equivalent_time()
        await _record_failure(context, principal, cx_id, correlation_id, "unknown_customer")
        result_or_raise(AuthenticationOutcome(authenticated=False))

    assert customer is not None
    check_lockout(customer.passcode.locked_until, now)

    ok = verify_passcode(customer.passcode.hash, body.passcode)
    updated = await context.store.customers.record_passcode_attempt(
        principal.tenant_id,
        cx_id,
        success=ok,
        now=now,
        max_attempts=context.settings.passcode_max_attempts,
        lockout_s=context.settings.passcode_lockout_s,
    )

    if not ok:
        await _record_failure(context, principal, cx_id, correlation_id, "wrong_passcode")
        if updated is not None and updated.passcode.locked_until is not None:
            await context.recorder.emit(
                event_type=EventType.ACCOUNT_LOCKED,
                principal=principal,
                subject=f"customers/{cx_id}",
                correlation_id=correlation_id,
                cx_id=cx_id,
                payload={"locked_until": updated.passcode.locked_until.isoformat()},
            )
        result_or_raise(AuthenticationOutcome(authenticated=False))

    await context.recorder.audit(
        principal=principal,
        action="authenticate",
        resource=f"customers/{cx_id}",
        decision="accepted",
        outcome="authenticated",
        correlation_id=correlation_id,
        cx_id=cx_id,
    )
    return AuthenticateResponse(
        authenticated=True, cx_id=cx_id, account_status=customer.account_status
    )


async def _record_failure(
    context: AppContext, principal: Principal, cx_id: str, correlation_id: str, reason: str
) -> None:
    await context.recorder.audit(
        principal=principal,
        action="authenticate",
        resource=f"customers/{cx_id}",
        decision="rejected",
        outcome="not_authenticated",
        correlation_id=correlation_id,
        cx_id=cx_id,
        failure_reason=reason,
    )
    await context.recorder.emit(
        event_type=EventType.AUTHENTICATION_FAILED,
        principal=principal,
        subject=f"customers/{cx_id}",
        correlation_id=correlation_id,
        cx_id=cx_id,
        payload={"reason": reason},
    )


@router.get("/{cx_id}", response_model=AccountResponse, summary="Account details")
async def get_account(
    cx_id: CxIdPath,
    context: AppContextDep,
    principal: Annotated[Principal, Depends(requires(Scope.ACCOUNT_READ))],
) -> AccountResponse:
    customer = await _account(context, principal, cx_id)
    open_tickets, _ = await context.store.tickets.list_for_customer(
        principal.tenant_id, cx_id, limit=MAX_PAGE_SIZE
    )
    return AccountResponse(
        cx_id=customer.cx_id,
        account_status=customer.account_status,
        account_type=customer.account_type,
        display_name=customer.display_name,
        customer_since=customer.customer_since,
        billing_postcode_suffix=customer.billing_postcode_suffix,
        open_case_count=sum(1 for ticket in open_tickets if ticket.state in _OPEN_STATES),
    )


@router.get("/{cx_id}/services", response_model=ServicesResponse, summary="Active services")
async def get_services(
    cx_id: CxIdPath,
    context: AppContextDep,
    principal: Annotated[Principal, Depends(requires(Scope.SERVICE_READ))],
    limit: LimitQuery = 5,
) -> ServicesResponse:
    await require_account_access(principal, cx_id, context.store.assignments)
    found, total = await context.store.services.list_for_customer(
        principal.tenant_id, cx_id, limit=limit
    )
    return ServicesResponse(
        services=[ServiceResponse.model_validate(s.model_dump()) for s in found],
        total_count=total,
        truncated=len(found) < total,
    )


@router.get("/{cx_id}/orders", response_model=OrdersResponse, summary="Order status")
async def get_orders(
    cx_id: CxIdPath,
    context: AppContextDep,
    principal: Annotated[Principal, Depends(requires(Scope.ORDER_READ))],
    limit: LimitQuery = 5,
    order_id: Annotated[str | None, Query(max_length=64)] = None,
) -> OrdersResponse:
    await require_account_access(principal, cx_id, context.store.assignments)
    found, total = await context.store.orders.list_for_customer(
        principal.tenant_id, cx_id, limit=limit, order_id=order_id
    )
    if order_id is not None and not found:
        raise NotFoundError("no such order")
    return OrdersResponse(
        orders=[OrderResponse.model_validate(o.model_dump()) for o in found],
        total_count=total,
        truncated=len(found) < total,
    )


@router.get("/{cx_id}/invoices", response_model=InvoicesResponse, summary="Invoice summary")
async def get_invoices(
    cx_id: CxIdPath,
    context: AppContextDep,
    principal: Annotated[Principal, Depends(requires(Scope.BILLING_READ))],
    limit: LimitQuery = 5,
    invoice_id: Annotated[str | None, Query(max_length=64)] = None,
) -> InvoicesResponse:
    await require_account_access(principal, cx_id, context.store.assignments)
    found, total = await context.store.invoices.list_for_customer(
        principal.tenant_id, cx_id, limit=limit, invoice_id=invoice_id
    )
    if invoice_id is not None and not found:
        raise NotFoundError("no such invoice")
    currency = found[0].currency if found else Currency.GBP
    return InvoicesResponse(
        invoices=[InvoiceResponse.model_validate(i.model_dump()) for i in found],
        total_outstanding_minor=sum(i.outstanding_minor for i in found),
        currency=currency,
        total_count=total,
        truncated=len(found) < total,
    )


@router.get("/{cx_id}/network", response_model=NetworkStatusResponse, summary="Network status")
async def get_network(
    cx_id: CxIdPath,
    context: AppContextDep,
    principal: Annotated[Principal, Depends(requires(Scope.NETWORK_READ))],
    service_id: Annotated[str | None, Query(max_length=64)] = None,
) -> NetworkStatusResponse:
    del service_id  # accepted for forward compatibility; the area is the unit today
    await require_account_access(principal, cx_id, context.store.assignments)
    services, _ = await context.store.services.list_for_customer(
        principal.tenant_id, cx_id, limit=1
    )
    # The area comes from the customer's own service record, never from the caller: a
    # caller-supplied area would let anyone read any area's incident detail.
    area_ref = services[0].service_id if services else "AREA-DEFAULT"
    status = await context.store.network.get_for_area(principal.tenant_id, area_ref)
    if status is None:
        status = await context.store.network.get_for_area(principal.tenant_id, "AREA-DEFAULT")
    if status is None:
        raise NotFoundError("no network status for this area")
    return NetworkStatusResponse(
        state=status.state,
        area_reference=status.area_ref,
        incident_id=status.incident_id,
        started_at=status.started_at,
        estimated_resolution=status.estimated_resolution,
        affected_services=list(status.affected_services),
        message=status.message,
    )
