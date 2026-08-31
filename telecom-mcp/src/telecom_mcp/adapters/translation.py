"""Translating the middleware's shapes into the frozen v1 tool contract.

The two services deliberately disagree about representation. The middleware stores and
transports money as an integer count of minor units, which is the right answer for a
system of record. The tool contract exposes a decimal amount, which is the right answer
for something a model reads aloud to a customer.

Rather than change either, the adapter translates - which is the job an adapter exists
for, and the reason the tool contract can stay frozen while the service behind it
evolves. Every translation is total: an unexpected shape raises rather than producing a
plausible wrong number.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

#: Every currency this system handles has two decimal places. A currency with a
#: different exponent must be added deliberately in both services, not assumed here.
MINOR_UNIT_EXPONENT = 2


def minor_to_decimal(minor_units: Any) -> Decimal:
    """Convert integer minor units to the decimal amount the tool contract exposes."""
    if isinstance(minor_units, bool) or not isinstance(minor_units, int):
        raise ValueError(f"expected integer minor units, got {type(minor_units).__name__}")
    return Decimal(minor_units).scaleb(-MINOR_UNIT_EXPONENT)


def decimal_to_minor(amount: Decimal | str) -> int:
    """Convert a decimal amount to integer minor units, refusing to round."""
    value = Decimal(amount)
    scaled = value.scaleb(MINOR_UNIT_EXPONENT)
    if scaled != scaled.to_integral_value():
        raise ValueError(f"{value} has more precision than the currency supports")
    return int(scaled)


def account(payload: dict[str, Any]) -> dict[str, Any]:
    return payload


def services(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "services": [
            {
                "service_id": item["service_id"],
                "kind": item["kind"],
                "plan_name": item["plan_name"],
                "status": item["status"],
                "monthly_price": minor_to_decimal(item["monthly_price_minor"]),
                "currency": item["currency"],
                "contract_end_date": item.get("contract_end_date"),
            }
            for item in payload["services"]
        ],
        "total_count": payload["total_count"],
        "truncated": payload["truncated"],
    }


def orders(payload: dict[str, Any]) -> dict[str, Any]:
    return payload


def invoices(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "invoices": [
            {
                "invoice_id": item["invoice_id"],
                "state": item["state"],
                "issued_on": item["issued_on"],
                "due_on": item["due_on"],
                "total": minor_to_decimal(item["total_minor"]),
                "outstanding": minor_to_decimal(item["outstanding_minor"]),
                "currency": item["currency"],
            }
            for item in payload["invoices"]
        ],
        "total_outstanding": minor_to_decimal(payload["total_outstanding_minor"]),
        "currency": payload["currency"],
        "truncated": payload["truncated"],
    }


def network(payload: dict[str, Any]) -> dict[str, Any]:
    return payload


def ticket(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticket_id": payload["ticket_id"],
        # The middleware distinguishes more states than the tool contract exposes; a
        # ticket that is already in progress still reads as open to the caller.
        "state": "open" if payload["state"] in ("open", "in_progress") else "queued",
        "created_at": payload["created_at"],
        "cancellable_until": payload.get("cancellable_until"),
        "deduplicated": payload.get("deduplicated", False),
    }


def callback(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "callback_id": payload["callback_id"],
        "scheduled_for": payload["scheduled_for"],
        "window": payload["window"],
        "cancellable_until": payload["cancellable_until"],
        "deduplicated": payload.get("deduplicated", False),
    }


def refund_approval(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "approval_request_id": payload["request_id"],
        # The tool contract has one pending state; the middleware's decided states never
        # come back from this call, because it only ever creates a request.
        "state": "pending_approval",
        "submitted_at": payload["created_at"],
        "approver_role": "supervisor_approver",
        "money_moved": False,
        "deduplicated": payload.get("deduplicated", False),
    }


def refund_request_body(body: dict[str, Any]) -> dict[str, Any]:
    """Turn the tool contract's decimal amount into the middleware's minor units."""
    translated = dict(body)
    translated["amount_minor"] = decimal_to_minor(str(translated.pop("amount")))
    return translated
