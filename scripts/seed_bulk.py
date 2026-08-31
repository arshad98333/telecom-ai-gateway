#!/usr/bin/env python3
"""Load at least ten records into every business collection, then report what landed.

Runs through the service's own repositories and domain models rather than writing BSON
by hand, so every document satisfies the same Pydantic validation and the same
collection validators the API enforces. A record this script writes is a record the API
can read back.

    uv run --env-file .env python scripts/seed_bulk.py
    uv run --env-file .env python scripts/seed_bulk.py --tenant tenant-eu-1
    uv run --env-file .env python scripts/seed_bulk.py --report docs/seeded-records.html

Idempotent. Collections with an upsert path are rewritten in place; the insert-only ones
(tickets, callbacks, approvals) skip records whose reference already exists, so a second
run reports zeros rather than failing on a duplicate key.

Five collections are deliberately left alone, and that is not an oversight:

  audit_records     append-only and hash-chained; a fabricated entry breaks the chain
  outbox            written in the same transaction as the change it describes
  tenant_sequences  one document per tenant by design, maintained by the service
  idempotency_keys  write-deduplication with a TTL; entries expire themselves
  stream_tokens     change-stream resume positions, owned by the running service

Every one of those is machinery the service writes for itself. Filling them with
invented rows would not demonstrate anything and would corrupt the two guarantees --
the audit chain and the event sequence -- that the rest of the design rests on.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pymongo.errors import DuplicateKeyError

from telecom_middleware.api.container import build_context
from telecom_middleware.config.settings import load_settings
from telecom_middleware.domain.models import (
    AccountStatus,
    ApprovalAction,
    ApprovalRequest,
    ApprovalState,
    Callback,
    CallbackWindow,
    Case,
    CaseStatus,
    CaseStep,
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


def say(message: str = "") -> None:
    print(message)  # noqa: T201 - a script, and this is its whole output


async def insert_or_skip(coroutine: Any) -> bool:
    """Insert one record; report False when its reference already exists."""
    try:
        await coroutine
    except DuplicateKeyError:
        return False
    return True


async def load(store: Any, *, tenant_id: str, now: datetime) -> dict[str, int]:
    """Write the dataset. Returns the number of records written per collection."""
    written: dict[str, int] = dict.fromkeys(MANAGED, 0)
    passcode = PasscodeState(hash=hash_passcode(DEMO_PASSCODE), updated_at=now)

    # --- customers -----------------------------------------------------------------
    for index, (cx_id, status, kind, name, postcode) in enumerate(CUSTOMER_PROFILES):
        await store.customers.upsert(
            Customer(
                tenant_id=tenant_id,
                cx_id=cx_id,
                account_status=AccountStatus(status),
                account_type=kind,
                display_name=name,
                customer_since=now - timedelta(days=400 + index * 137),
                billing_postcode_suffix=postcode,
                email=f"{cx_id.lower()}@example.com",
                phone=f"+44 7700 9{index:05d}",
                passcode=passcode,
                created_at=now,
                updated_at=now,
            )
        )
        written["customers"] += 1

    # --- services ------------------------------------------------------------------
    # Two services for the first four accounts, one each for the rest: twelve rows, and
    # a customer with more than one service to read back.
    service_index = 0
    for index, (cx_id, *_rest) in enumerate(CUSTOMER_PROFILES[:8]):
        count = 2 if index < 4 else 1
        for slot in range(count):
            kind, plan, price = SERVICE_PLANS[service_index % len(SERVICE_PLANS)]
            await store.services.upsert(
                Service(
                    tenant_id=tenant_id,
                    cx_id=cx_id,
                    service_id=f"SVC-B-{service_index + 1:04d}",
                    kind=kind,
                    plan_name=plan,
                    status="active" if slot == 0 else "suspended",
                    monthly_price_minor=price,
                    currency=Currency.GBP,
                    contract_end_date=now + timedelta(days=90 + service_index * 30),
                )
            )
            written["services"] += 1
            service_index += 1

    # --- orders --------------------------------------------------------------------
    states = list(OrderState)
    for index, summary in enumerate(ORDER_SUMMARIES):
        cx_id = CUSTOMER_PROFILES[index % len(CUSTOMER_PROFILES)][0]
        await store.orders.upsert(
            Order(
                tenant_id=tenant_id,
                cx_id=cx_id,
                order_id=f"ORD-B-{index + 1:04d}",
                state=states[index % len(states)],
                placed_at=now - timedelta(days=3 + index * 4),
                expected_by=now + timedelta(days=2 + index),
                summary=summary,
            )
        )
        written["orders"] += 1

    # --- invoices ------------------------------------------------------------------
    invoice_states = [
        InvoiceState.PAID,
        InvoiceState.DUE,
        InvoiceState.OVERDUE,
        InvoiceState.DISPUTED,
    ]
    for index in range(12):
        cx_id = CUSTOMER_PROFILES[index % len(CUSTOMER_PROFILES)][0]
        state = invoice_states[index % len(invoice_states)]
        total = 2400 + index * 850
        await store.invoices.upsert(
            Invoice(
                tenant_id=tenant_id,
                cx_id=cx_id,
                invoice_id=f"INV-B-{index + 1:04d}",
                state=state,
                issued_on=now - timedelta(days=30 + index * 30),
                due_on=now - timedelta(days=index * 30),
                total_minor=total,
                outstanding_minor=0 if state is InvoiceState.PAID else total,
                currency=Currency.GBP,
            )
        )
        written["invoices"] += 1

    # --- network_status ------------------------------------------------------------
    for index, (state, message) in enumerate(AREA_MESSAGES):
        disrupted = state in (NetworkState.DEGRADED, NetworkState.OUTAGE)
        await store.network.upsert(
            NetworkStatus(
                tenant_id=tenant_id,
                area_ref=f"AREA-BULK-{index + 1:02d}",
                state=state,
                incident_id=f"INC-B-{index + 1:04d}" if disrupted else None,
                started_at=now - timedelta(hours=4 + index) if disrupted else None,
                estimated_resolution=now + timedelta(hours=6) if disrupted else None,
                affected_services=[ServiceKind.BROADBAND] if disrupted else [],
                message=message,
                updated_at=now,
            )
        )
        written["network_status"] += 1

    # --- agent_assignments ---------------------------------------------------------
    # The agent gets ten of the twelve accounts, so a refusal on the other two stays
    # demonstrable rather than theoretical.
    for cx_id, *_rest in CUSTOMER_PROFILES[:10]:
        await store.assignments.assign(
            tenant_id, DEMO_AGENT, cx_id, by=DEMO_SUPERVISOR, now=now
        )
        written["agent_assignments"] += 1

    # --- tickets -------------------------------------------------------------------
    ticket_states = list(TicketState)
    for index, (category, subject, priority) in enumerate(TICKET_SEEDS):
        cx_id = CUSTOMER_PROFILES[index % len(CUSTOMER_PROFILES)][0]
        created = now - timedelta(days=index + 1, hours=index)
        ok = await insert_or_skip(
            store.tickets.insert(
                Ticket(
                    tenant_id=tenant_id,
                    ticket_id=f"TCK-bulk-{index + 1:04d}",
                    cx_id=cx_id,
                    category=category,
                    subject=subject,
                    description=f"{subject}. Raised during a support call and not yet closed.",
                    priority=priority,
                    state=ticket_states[index % len(ticket_states)],
                    created_at=created,
                    created_by=f"auth0|customer-{cx_id.lower()}",
                    updated_at=created + timedelta(hours=2),
                )
            )
        )
        written["tickets"] += int(ok)

    # --- callbacks -----------------------------------------------------------------
    windows = list(CallbackWindow)
    for index, reason in enumerate(CALLBACK_REASONS):
        cx_id = CUSTOMER_PROFILES[index % len(CUSTOMER_PROFILES)][0]
        ok = await insert_or_skip(
            store.callbacks.insert(
                Callback(
                    tenant_id=tenant_id,
                    callback_id=f"CB-bulk-{index + 1:04d}",
                    cx_id=cx_id,
                    scheduled_for=now + timedelta(days=index + 1),
                    window=windows[index % len(windows)],
                    reason=reason,
                    state="scheduled",
                    created_at=now,
                    created_by=f"auth0|customer-{cx_id.lower()}",
                    cancellable_until=now + timedelta(days=index + 1, hours=-4),
                )
            )
        )
        written["callbacks"] += int(ok)

    # --- approval_requests ---------------------------------------------------------
    # All left pending: an approved or rejected request carries a decision record that
    # only the approval service should ever write.
    for index, (amount, reason, justification) in enumerate(APPROVAL_CASES):
        cx_id = CUSTOMER_PROFILES[index % len(CUSTOMER_PROFILES)][0]
        ok = await insert_or_skip(
            store.approvals.insert(
                ApprovalRequest(
                    tenant_id=tenant_id,
                    request_id=f"APR-bulk-{index + 1:04d}",
                    cx_id=cx_id,
                    action=ApprovalAction.REFUND,
                    amount_minor=amount,
                    currency=Currency.GBP,
                    reason=reason,
                    justification=justification,
                    evidence={
                        "invoice_id": f"INV-B-{index + 1:04d}",
                        "invoice_total_minor": 2400 + index * 850,
                    },
                    state=ApprovalState.PENDING,
                    requested_by=f"auth0|customer-{cx_id.lower()}",
                    requested_by_role="customer",
                    created_at=now - timedelta(hours=index + 1),
                    expires_at=now + timedelta(days=2),
                )
            )
        )
        written["approval_requests"] += int(ok)

    # --- cases ---------------------------------------------------------------------
    case_states = list(CaseStatus)
    for index in range(10):
        cx_id = CUSTOMER_PROFILES[index % len(CUSTOMER_PROFILES)][0]
        started = now - timedelta(hours=index + 1)
        await store.cases.upsert(
            Case(
                tenant_id=tenant_id,
                case_id=f"CASE-bulk-{index + 1:04d}",
                cx_id=cx_id,
                status=case_states[index % len(case_states)],
                started_at=started,
                updated_at=started + timedelta(minutes=12),
                steps=[
                    CaseStep(
                        at=started,
                        intent="identify_customer",
                        tool="customers.authenticate",
                        outcome="verified",
                    ),
                    CaseStep(
                        at=started + timedelta(minutes=3),
                        intent="explain_charge",
                        tool="invoices.list",
                        outcome="answered",
                    ),
                ],
                tool_steps_used=2,
                consent_recorded_at=started,
            )
        )
        written["cases"] += 1

    return written


async def collection_counts(store: Any, names: list[str]) -> dict[str, int]:
    """Live counts straight from the database, so the report states fact, not intent."""
    database = store._database  # noqa: SLF001 - a script, reading its own deployment
    return {name: await database[name].count_documents({}) for name in names}


def render_report(
    *,
    tenant_id: str,
    when: datetime,
    written: dict[str, int],
    totals: dict[str, int],
    database: str,
) -> str:
    """A single self-contained page: what was written, where, and what was not."""

    def row(name: str) -> str:
        return (
            f"<tr><td><code>{html.escape(name)}</code></td>"
            f"<td class='n'>{written.get(name, 0)}</td>"
            f"<td class='n'>{totals.get(name, 0)}</td></tr>"
        )

    managed_rows = "\n".join(row(name) for name in MANAGED)
    untouched_rows = "\n".join(
        f"<tr><td><code>{html.escape(name)}</code></td>"
        f"<td class='n'>{totals.get(name, 0)}</td>"
        f"<td>{html.escape(reason)}</td></tr>"
        for name, reason in UNTOUCHED.items()
    )
    total_written = sum(written.values())

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Seeded records &mdash; {html.escape(database)}</title>
<style>
  :root {{
    --bg: #ffffff; --fg: #16181d; --muted: #5f6672;
    --line: #e3e6ea; --head: #f6f7f9; --accent: #12805c;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #14161a; --fg: #e8eaed; --muted: #9aa1ac;
      --line: #2a2e35; --head: #1c1f25; --accent: #4ecb9a;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 2.5rem 1.25rem 4rem; background: var(--bg); color: var(--fg);
    font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }}
  main {{ max-width: 820px; margin: 0 auto; }}
  h1 {{ font-size: 1.5rem; margin: 0 0 .35rem; letter-spacing: -.01em; }}
  h2 {{ font-size: 1.05rem; margin: 2.5rem 0 .5rem; }}
  p.sub {{ color: var(--muted); margin: 0 0 2rem; }}
  p.note {{ color: var(--muted); margin: .4rem 0 1rem; }}
  dl.meta {{
    display: grid; grid-template-columns: max-content 1fr; gap: .3rem 1.25rem;
    margin: 0 0 1rem; padding: 1rem 1.15rem; background: var(--head);
    border: 1px solid var(--line); border-radius: 8px;
  }}
  dl.meta dt {{ color: var(--muted); }}
  dl.meta dd {{ margin: 0; }}
  .scroll {{ overflow-x: auto; }}
  table {{ width: 100%; border-collapse: collapse; font-size: .94rem; }}
  th, td {{ text-align: left; padding: .55rem .7rem; border-bottom: 1px solid var(--line); }}
  th {{ background: var(--head); font-weight: 600; white-space: nowrap; }}
  td.n, th.n {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .9em; }}
  tfoot td {{ font-weight: 600; border-bottom: none; }}
  .accent {{ color: var(--accent); }}
</style>
</head>
<body>
<main>
  <h1>Seeded records</h1>
  <p class="sub">What this run wrote, and what it deliberately did not.</p>

  <dl class="meta">
    <dt>Database</dt><dd><code>{html.escape(database)}</code></dd>
    <dt>Tenant</dt><dd><code>{html.escape(tenant_id)}</code></dd>
    <dt>Run at</dt><dd>{when.strftime("%Y-%m-%d %H:%M:%S UTC")}</dd>
    <dt>Records written</dt><dd class="accent">{total_written}</dd>
  </dl>

  <h2>Collections written</h2>
  <p class="note">
    &ldquo;Written&rdquo; counts this run only. Upsert collections rewrite in place, so a
    second run reports the same number; insert-only collections (tickets, callbacks,
    approvals) skip references that already exist and report&nbsp;0.
  </p>
  <div class="scroll">
  <table>
    <thead>
      <tr><th>Collection</th><th class="n">Written this run</th><th class="n">Total in database</th></tr>
    </thead>
    <tbody>
{managed_rows}
    </tbody>
    <tfoot>
      <tr><td>Total</td><td class="n">{total_written}</td><td class="n">{sum(totals.get(n, 0) for n in MANAGED)}</td></tr>
    </tfoot>
  </table>
  </div>

  <h2>Collections left alone</h2>
  <p class="note">
    Each of these is written by the service itself. Inserting invented rows would break
    the audit chain or the event sequence the rest of the design depends on.
  </p>
  <div class="scroll">
  <table>
    <thead>
      <tr><th>Collection</th><th class="n">Total in database</th><th>Why</th></tr>
    </thead>
    <tbody>
{untouched_rows}
    </tbody>
  </table>
  </div>

  <h2>Reference ranges</h2>
  <div class="scroll">
  <table>
    <thead><tr><th>Collection</th><th>References</th></tr></thead>
    <tbody>
      <tr><td><code>customers</code></td><td><code>CX-2001</code> &ndash; <code>CX-2012</code></td></tr>
      <tr><td><code>services</code></td><td><code>SVC-B-0001</code> &ndash; <code>SVC-B-0012</code></td></tr>
      <tr><td><code>orders</code></td><td><code>ORD-B-0001</code> &ndash; <code>ORD-B-0012</code></td></tr>
      <tr><td><code>invoices</code></td><td><code>INV-B-0001</code> &ndash; <code>INV-B-0012</code></td></tr>
      <tr><td><code>network_status</code></td><td><code>AREA-BULK-01</code> &ndash; <code>AREA-BULK-10</code></td></tr>
      <tr><td><code>agent_assignments</code></td><td><code>{html.escape(DEMO_AGENT)}</code> &rarr; <code>CX-2001</code> &ndash; <code>CX-2010</code></td></tr>
      <tr><td><code>tickets</code></td><td><code>TCK-bulk-0001</code> &ndash; <code>TCK-bulk-0012</code></td></tr>
      <tr><td><code>callbacks</code></td><td><code>CB-bulk-0001</code> &ndash; <code>CB-bulk-0010</code></td></tr>
      <tr><td><code>approval_requests</code></td><td><code>APR-bulk-0001</code> &ndash; <code>APR-bulk-0010</code></td></tr>
      <tr><td><code>cases</code></td><td><code>CASE-bulk-0001</code> &ndash; <code>CASE-bulk-0010</code></td></tr>
    </tbody>
  </table>
  </div>

  <p class="note">
    Passcode for every seeded customer is <code>4821</code>, Argon2id-hashed exactly as a
    real one would be. Demo credential, demo dataset.
  </p>
</main>
</body>
</html>
"""


async def run(args: argparse.Namespace) -> int:
    settings = load_settings()
    context = build_context(settings, configure_logs=False)
    await context.store.start()
    now = context.clock.now()
    try:
        written = await load(context.store, tenant_id=args.tenant, now=now)
        totals = await collection_counts(
            context.store, [*MANAGED, *UNTOUCHED]
        )
        database = context.store._database.name  # noqa: SLF001
    finally:
        await context.store.close()

    say()
    for name in MANAGED:
        say(f"  {name:<20} +{written[name]:<4} ({totals[name]} total)")
    say()
    say(f"  {sum(written.values())} records written into '{database}'")

    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        render_report(
            tenant_id=args.tenant,
            when=now,
            written=written,
            totals=totals,
            database=database,
        ),
        encoding="utf-8",
    )
    say(f"  report written to {report}")
    say()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Load ten or more records into every business collection."
    )
    parser.add_argument("--tenant", default="tenant-eu-1")
    parser.add_argument(
        "--report",
        default="docs/seeded-records.html",
        help="where to write the HTML summary (default: docs/seeded-records.html)",
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
