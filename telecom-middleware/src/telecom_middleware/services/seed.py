"""A small, realistic demo dataset, committed so any developer gets a usable system.

One command produces a database a person can actually explore: two customers in one
tenant, one of them suspended with an overdue bill, an area incident, an agent with an
assignment, and a pending approval waiting for a supervisor. Enough to see every
stakeholder's view without inventing data by hand.

The passcode for every seeded customer is 4821, hashed the same way a real one is. It
is a demo credential in a demo dataset and nothing else uses it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from telecom_middleware.domain.models import (
    AccountStatus,
    ApprovalAction,
    ApprovalRequest,
    ApprovalState,
    Callback,
    CallbackWindow,
    Customer,
    Invoice,
    InvoiceState,
    NetworkState,
    NetworkStatus,
    Order,
    OrderState,
    PasscodeState,
    RefundReason,
    Service,
    ServiceKind,
    Ticket,
    TicketCategory,
    TicketPriority,
    TicketState,
)
from telecom_middleware.domain.money import Currency
from telecom_middleware.services.passcode import hash_passcode

DEMO_PASSCODE = "4821"
DEMO_AGENT = "auth0|agent-7"
DEMO_SUPERVISOR = "auth0|supervisor-1"


async def seed_demo_data(store: Any, *, tenant_id: str, clock: Any) -> dict[str, int]:
    """Load the dataset. Idempotent: running it twice leaves the same rows."""
    now = clock.now()
    passcode = PasscodeState(hash=hash_passcode(DEMO_PASSCODE), updated_at=now)

    await store.customers.upsert(
        Customer(
            tenant_id=tenant_id,
            cx_id="CX-1234",
            account_status=AccountStatus.ACTIVE,
            account_type="consumer",
            display_name="J. Okonkwo",
            customer_since=now - timedelta(days=1900),
            billing_postcode_suffix="4AB",
            email="jo@example.com",
            phone="+44 7700 900123",
            passcode=passcode,
            created_at=now,
            updated_at=now,
        )
    )
    await store.customers.upsert(
        Customer(
            tenant_id=tenant_id,
            cx_id="CX-5555",
            account_status=AccountStatus.SUSPENDED,
            account_type="business",
            display_name="Rivera Logistics Ltd",
            customer_since=now - timedelta(days=2400),
            billing_postcode_suffix="9ZQ",
            email="accounts@rivera.example",
            phone="+44 7700 900555",
            passcode=passcode,
            created_at=now,
            updated_at=now,
        )
    )

    for service_id, kind, plan, price in (
        ("AREA-EDI-04", ServiceKind.MOBILE, "Unlimited 5G", 2400),
        ("SVC-002", ServiceKind.BROADBAND, "Fibre 500", 3900),
    ):
        await store.services.upsert(
            Service(
                tenant_id=tenant_id,
                cx_id="CX-1234",
                service_id=service_id,
                kind=kind,
                plan_name=plan,
                status="active",
                monthly_price_minor=price,
                currency=Currency.GBP,
                contract_end_date=now + timedelta(days=200),
            )
        )

    await store.orders.upsert(
        Order(
            tenant_id=tenant_id,
            cx_id="CX-1234",
            order_id="ORD-9001",
            state=OrderState.DISPATCHED,
            placed_at=now - timedelta(days=10),
            expected_by=now + timedelta(days=3),
            summary="Replacement router",
        )
    )
    await store.invoices.upsert(
        Invoice(
            tenant_id=tenant_id,
            cx_id="CX-1234",
            invoice_id="INV-2026-08",
            state=InvoiceState.DUE,
            issued_on=now - timedelta(days=29),
            due_on=now + timedelta(days=2),
            total_minor=6300,
            outstanding_minor=6300,
            currency=Currency.GBP,
        )
    )
    await store.invoices.upsert(
        Invoice(
            tenant_id=tenant_id,
            cx_id="CX-5555",
            invoice_id="INV-2026-06",
            state=InvoiceState.OVERDUE,
            issued_on=now - timedelta(days=90),
            due_on=now - timedelta(days=60),
            total_minor=41_000,
            outstanding_minor=41_000,
            currency=Currency.GBP,
        )
    )

    await store.network.upsert(
        NetworkStatus(
            tenant_id=tenant_id,
            area_ref="AREA-EDI-04",
            state=NetworkState.DEGRADED,
            incident_id="INC-5512",
            started_at=now - timedelta(hours=6),
            estimated_resolution=now + timedelta(hours=6),
            affected_services=[ServiceKind.BROADBAND],
            message="Engineers are working on a fault affecting broadband in this area.",
            updated_at=now,
        )
    )
    await store.network.upsert(
        NetworkStatus(
            tenant_id=tenant_id,
            area_ref="AREA-DEFAULT",
            state=NetworkState.OPERATIONAL,
            message="No known issues in this area.",
            updated_at=now,
        )
    )

    # The agent is assigned one account and not the other, so a denial is demonstrable
    # rather than theoretical.
    await store.assignments.assign(tenant_id, DEMO_AGENT, "CX-5555", by=DEMO_SUPERVISOR, now=now)

    await store.tickets.insert(
        Ticket(
            tenant_id=tenant_id,
            ticket_id="TCK-seed-0001",
            cx_id="CX-1234",
            category=TicketCategory.NETWORK,
            subject="Broadband drops every evening",
            description="The connection drops around eight most evenings.",
            priority=TicketPriority.NORMAL,
            state=TicketState.OPEN,
            created_at=now - timedelta(days=1),
            created_by="auth0|customer-1234",
            updated_at=now - timedelta(days=1),
        )
    )
    await store.callbacks.insert(
        Callback(
            tenant_id=tenant_id,
            callback_id="CB-seed-0001",
            cx_id="CX-1234",
            scheduled_for=now + timedelta(days=1),
            window=CallbackWindow.MORNING,
            reason="Discuss the outage credit",
            state="scheduled",
            created_at=now,
            created_by="auth0|customer-1234",
            cancellable_until=now + timedelta(hours=20),
        )
    )
    await store.approvals.insert(
        ApprovalRequest(
            tenant_id=tenant_id,
            request_id="APR-seed-0001",
            cx_id="CX-1234",
            action=ApprovalAction.REFUND,
            amount_minor=450,
            currency=Currency.GBP,
            reason=RefundReason.SERVICE_OUTAGE,
            justification="Broadband unavailable for three days in August.",
            evidence={
                "invoice_id": "INV-2026-08",
                "invoice_total_minor": 6300,
                "incident_id": "INC-5512",
            },
            state=ApprovalState.PENDING,
            requested_by="auth0|customer-1234",
            requested_by_role="customer",
            created_at=now - timedelta(hours=2),
            expires_at=now + timedelta(days=2),
        )
    )

    return {"customers": 2, "services": 2, "invoices": 2, "tickets": 1, "approvals": 1}
