"""The fixed demo dataset `seed_bulk.py` loads.

Pulled out of the orchestration script on its own: this module is nothing but data —
customer profiles, service plans, order summaries, ticket seeds, callback reasons,
approval cases and area messages — with no control flow of its own. Splitting it out
keeps `seed_bulk.py` focused on *how* the dataset gets written and this module focused
on *what* it contains, so growing the dataset never means scrolling past a load loop to
find the right tuple.
"""

from __future__ import annotations

from telecom_middleware.domain.models import (
    NetworkState,
    RefundReason,
    ServiceKind,
    TicketCategory,
    TicketPriority,
)

DEMO_PASSCODE = "4821"
DEMO_AGENT = "auth0|agent-7"
DEMO_SUPERVISOR = "auth0|supervisor-1"

#: Collections this script writes, and the section each one gets in the report.
MANAGED = (
    "customers",
    "services",
    "orders",
    "invoices",
    "network_status",
    "agent_assignments",
    "tickets",
    "callbacks",
    "approval_requests",
    "cases",
)

#: Collections deliberately not written, with the reason shown in the report.
UNTOUCHED = {
    "audit_records": "Append-only and hash-chained. An invented row breaks verification.",
    "outbox": "Written inside the transaction that produces the event it carries.",
    "tenant_sequences": "One document per tenant, maintained by the service itself.",
    "idempotency_keys": "Write-deduplication with a TTL; entries expire themselves.",
    "stream_tokens": "Change-stream resume positions, owned by the running service.",
}

CUSTOMER_PROFILES = (
    ("CX-2001", "active", "consumer", "A. Whitfield", "1PQ"),
    ("CX-2002", "active", "consumer", "B. Nakamura", "3RS"),
    ("CX-2003", "suspended", "consumer", "C. Oyelaran", "7TU"),
    ("CX-2004", "active", "business", "Delta Freight Ltd", "2VW"),
    ("CX-2005", "active", "consumer", "E. Marchetti", "5XY"),
    ("CX-2006", "pending", "consumer", "F. Kowalski", "8ZA"),
    ("CX-2007", "active", "business", "Greenfield Clinics", "4BC"),
    ("CX-2008", "active", "consumer", "H. Sandoval", "6DE"),
    ("CX-2009", "closed", "consumer", "I. Petrov", "9FG"),
    ("CX-2010", "active", "business", "Juniper Retail Group", "1HJ"),
    ("CX-2011", "active", "consumer", "K. Adebayo", "3KL"),
    ("CX-2012", "suspended", "business", "Lakeside Haulage", "5MN"),
)

SERVICE_PLANS = (
    (ServiceKind.MOBILE, "Unlimited 5G", 2400),
    (ServiceKind.BROADBAND, "Fibre 500", 3900),
    (ServiceKind.BROADBAND, "Fibre 900", 5900),
    (ServiceKind.LANDLINE, "Line Rental", 1800),
    (ServiceKind.TV, "Entertainment Pack", 2900),
    (ServiceKind.MOBILE, "Business 5G 100GB", 3200),
)

ORDER_SUMMARIES = (
    "Replacement router",
    "SIM swap, keep number",
    "Upgrade to Fibre 900",
    "New TV box",
    "Landline reconnection",
    "Business line install",
    "Handset return label",
    "Static IP add-on",
    "Second SIM for tablet",
    "Engineer visit, faulty socket",
    "Broadband speed uplift",
    "Cancel TV add-on",
)

TICKET_SEEDS = (
    (TicketCategory.NETWORK, "Broadband drops each evening", TicketPriority.NORMAL),
    (TicketCategory.BILLING, "Charged twice in August", TicketPriority.HIGH),
    (TicketCategory.DEVICE, "Router keeps rebooting", TicketPriority.NORMAL),
    (TicketCategory.ACCOUNT, "Cannot reset my passcode", TicketPriority.HIGH),
    (TicketCategory.ORDER, "Order shows dispatched, nothing arrived", TicketPriority.NORMAL),
    (TicketCategory.NETWORK, "No signal indoors since Tuesday", TicketPriority.HIGH),
    (TicketCategory.BILLING, "Refund for outage not applied", TicketPriority.NORMAL),
    (TicketCategory.OTHER, "Request itemised call log", TicketPriority.LOW),
    (TicketCategory.DEVICE, "TV box audio out of sync", TicketPriority.LOW),
    (TicketCategory.NETWORK, "Slow speeds at peak hours", TicketPriority.NORMAL),
    (TicketCategory.ACCOUNT, "Change billing address", TicketPriority.LOW),
    (TicketCategory.BILLING, "Dispute early termination fee", TicketPriority.HIGH),
)

CALLBACK_REASONS = (
    "Discuss the outage credit",
    "Confirm the upgrade date",
    "Review the disputed charge",
    "Walk through the router setup",
    "Arrange the engineer visit",
    "Explain the final bill",
    "Confirm the number port",
    "Reschedule the install",
    "Go through the contract options",
    "Follow up on the refund request",
)

APPROVAL_CASES = (
    (450, RefundReason.SERVICE_OUTAGE, "Broadband unavailable for three days."),
    (1200, RefundReason.BILLING_ERROR, "Charged for a plan the customer never held."),
    (300, RefundReason.DUPLICATE_CHARGE, "Two identical debits on the same day."),
    (800, RefundReason.GOODWILL, "Repeated faults over two consecutive months."),
    (2500, RefundReason.BILLING_ERROR, "Business line billed at consumer rate in error."),
    (150, RefundReason.SERVICE_OUTAGE, "Mobile data down for one afternoon."),
    (600, RefundReason.DUPLICATE_CHARGE, "Duplicate installation fee."),
    (950, RefundReason.GOODWILL, "Retention offer agreed on the call."),
    (400, RefundReason.SERVICE_OUTAGE, "TV service interrupted during the outage window."),
    (1750, RefundReason.BILLING_ERROR, "Cancelled add-on kept billing for four months."),
)

AREA_MESSAGES = (
    (NetworkState.OPERATIONAL, "No known issues in this area."),
    (NetworkState.DEGRADED, "Slower speeds while engineers work on a local fault."),
    (NetworkState.OUTAGE, "Broadband is down in this area. Engineers are on site."),
    (NetworkState.PLANNED_MAINTENANCE, "Planned work overnight; brief interruptions expected."),
    (NetworkState.OPERATIONAL, "Service restored. Monitoring for recurrence."),
    (NetworkState.DEGRADED, "Mobile coverage reduced after a mast fault."),
    (NetworkState.OPERATIONAL, "No known issues in this area."),
    (NetworkState.OUTAGE, "A cable fault is affecting broadband and landline."),
    (NetworkState.PLANNED_MAINTENANCE, "Exchange upgrade this Sunday, 01:00 to 05:00."),
    (NetworkState.OPERATIONAL, "No known issues in this area."),
)
