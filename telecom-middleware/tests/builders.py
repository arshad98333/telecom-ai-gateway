"""Builders for test data. Every test creates and destroys its own."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from telecom_middleware.domain.models import (
    ApprovalRequest,
    ApprovalState,
    AuditRecord,
    Callback,
    Case,
    CaseStatus,
    Customer,
    Invoice,
    NetworkStatus,
    Order,
    PasscodeState,
    Service,
    Ticket,
)
from telecom_middleware.domain.money import Currency

TENANT = "tenant-eu-1"
OTHER_TENANT = "tenant-us-9"
CUSTOMER = "CX-1234"
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)

#: Argon2id hash of "4821", generated once and committed so tests need no key derivation.
PASSCODE_PLACEHOLDER_HASH = "$argon2id$v=19$m=65536,t=3,p=4$placeholder$placeholder"


def customer(**overrides: Any) -> Customer:
    base: dict[str, Any] = {
        "tenant_id": TENANT,
        "cx_id": CUSTOMER,
        "account_status": "active",
        "account_type": "consumer",
        "display_name": "J. Okonkwo",
        "customer_since": NOW - timedelta(days=1900),
        "billing_postcode_suffix": "4AB",
        "email": "jo@example.com",
        "phone": "+44 7700 900123",
        "passcode": PasscodeState(hash=PASSCODE_PLACEHOLDER_HASH, updated_at=NOW),
        "created_at": NOW,
        "updated_at": NOW,
    }
    base.update(overrides)
    return Customer.model_validate(base)


def service(**overrides: Any) -> Service:
    base: dict[str, Any] = {
        "tenant_id": TENANT,
        "cx_id": CUSTOMER,
        "service_id": "SVC-001",
        "kind": "mobile",
        "plan_name": "Unlimited 5G",
        "status": "active",
        "monthly_price_minor": 2400,
        "currency": Currency.GBP,
        "contract_end_date": None,
    }
    base.update(overrides)
    return Service.model_validate(base)


def order(**overrides: Any) -> Order:
    base: dict[str, Any] = {
        "tenant_id": TENANT,
        "cx_id": CUSTOMER,
        "order_id": "ORD-9001",
        "state": "dispatched",
        "placed_at": NOW - timedelta(days=10),
        "expected_by": NOW + timedelta(days=3),
        "summary": "Replacement router",
    }
    base.update(overrides)
    return Order.model_validate(base)


def invoice(**overrides: Any) -> Invoice:
    base: dict[str, Any] = {
        "tenant_id": TENANT,
        "cx_id": CUSTOMER,
        "invoice_id": "INV-2026-08",
        "state": "due",
        "issued_on": NOW - timedelta(days=29),
        "due_on": NOW + timedelta(days=2),
        "total_minor": 6300,
        "outstanding_minor": 6300,
        "currency": Currency.GBP,
    }
    base.update(overrides)
    return Invoice.model_validate(base)


def network(**overrides: Any) -> NetworkStatus:
    base: dict[str, Any] = {
        "tenant_id": TENANT,
        "area_ref": "AREA-EDI-04",
        "state": "degraded",
        "incident_id": "INC-5512",
        "started_at": NOW - timedelta(hours=6),
        "estimated_resolution": NOW + timedelta(hours=6),
        "affected_services": ["broadband"],
        "message": "Engineers are working on a fault affecting broadband in this area.",
        "updated_at": NOW,
    }
    base.update(overrides)
    return NetworkStatus.model_validate(base)


def ticket(**overrides: Any) -> Ticket:
    base: dict[str, Any] = {
        "tenant_id": TENANT,
        "ticket_id": "TCK-0001",
        "cx_id": CUSTOMER,
        "category": "billing",
        "subject": "Charged twice",
        "description": "My August bill shows the same charge twice.",
        "priority": "normal",
        "state": "open",
        "created_at": NOW,
        "created_by": "auth0|customer-1",
        "updated_at": NOW,
        "cancellable_until": NOW + timedelta(minutes=15),
        "case_id": None,
    }
    base.update(overrides)
    return Ticket.model_validate(base)


def callback(**overrides: Any) -> Callback:
    base: dict[str, Any] = {
        "tenant_id": TENANT,
        "callback_id": "CB-0001",
        "cx_id": CUSTOMER,
        "scheduled_for": NOW + timedelta(days=1),
        "window": "morning",
        "reason": "Discuss the bill",
        "state": "scheduled",
        "created_at": NOW,
        "created_by": "auth0|customer-1",
        "cancellable_until": NOW + timedelta(hours=20),
    }
    base.update(overrides)
    return Callback.model_validate(base)


def approval(**overrides: Any) -> ApprovalRequest:
    base: dict[str, Any] = {
        "tenant_id": TENANT,
        "request_id": "APR-0001",
        "cx_id": CUSTOMER,
        "action": "refund",
        "amount_minor": 450,
        "currency": Currency.GBP,
        "reason": "duplicate_charge",
        "justification": "Charged twice in August.",
        "evidence": {"invoice_id": "INV-2026-08", "outstanding_minor": 6300},
        "state": ApprovalState.PENDING,
        "requested_by": "auth0|customer-1",
        "requested_by_role": "customer",
        "created_at": NOW,
        "expires_at": NOW + timedelta(days=2),
        "decision": None,
        "case_id": None,
    }
    base.update(overrides)
    return ApprovalRequest.model_validate(base)


def case(**overrides: Any) -> Case:
    base: dict[str, Any] = {
        "tenant_id": TENANT,
        "case_id": "CASE-0001",
        "cx_id": CUSTOMER,
        "status": CaseStatus.ACTIVE,
        "started_at": NOW,
        "updated_at": NOW,
        "steps": [],
        "tool_steps_used": 0,
        "consent_recorded_at": NOW,
    }
    base.update(overrides)
    return Case.model_validate(base)


def audit(**overrides: Any) -> AuditRecord:
    base: dict[str, Any] = {
        "tenant_id": TENANT,
        "seq": 1,
        "record_id": "AUD-0001",
        "at": NOW,
        "correlation_id": "corr-1",
        "actor_sub": "auth0|customer-1",
        "actor_role": "customer",
        "cx_ref": "ref_abc",
        "action": "get_customer_account",
        "resource": "customers/CX-1234",
        "decision": "accepted",
        "outcome": "success",
        "previous_hash": "0" * 64,
        "entry_hash": "1" * 64,
    }
    base.update(overrides)
    return AuditRecord.model_validate(base)
