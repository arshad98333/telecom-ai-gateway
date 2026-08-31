/* Add a customer of your own.
 *
 * Two things have to be right or the record exists and can never be used:
 *
 *   1. passcode.hash must be a real Argon2id hash. Generate one first - in VS Code run
 *      the task "Hash a passcode", or in a terminal:
 *
 *          uv run telecom-middleware hash-passcode 1234
 *
 *      Paste the whole $argon2id$... string below. A placeholder here produces an
 *      account that authenticates against nothing, which is a confusing hour to spend.
 *
 *   2. tenant_id must match the tenant in the token you call with. Every query the
 *      service makes filters on it, so a mismatched tenant looks exactly like a
 *      customer that does not exist.
 */

use("telecom");

const now = new Date();

db.customers.replaceOne(
  { tenant_id: "tenant-eu-1", cx_id: "CX-7777" },
  {
    tenant_id: "tenant-eu-1",
    cx_id: "CX-7777",
    account_status: "active",          // active | suspended | closed | pending
    account_type: "consumer",          // consumer | business
    display_name: "A. Newcomer",
    customer_since: new Date("2024-05-01T00:00:00Z"),
    billing_postcode_suffix: "1XY",
    email: "a.newcomer@example.com",
    phone: "+44 7700 900777",
    passcode: {
      hash: "PASTE_THE_ARGON2_HASH_HERE",
      failed_attempts: NumberLong(0),
      locked_until: null,
      updated_at: now,
    },
    created_at: now,
    updated_at: now,
  },
  { upsert: true }
);

// A service, so the network endpoint has an area to look up for them.
db.services.replaceOne(
  { tenant_id: "tenant-eu-1", cx_id: "CX-7777", service_id: "AREA-EDI-04" },
  {
    tenant_id: "tenant-eu-1",
    cx_id: "CX-7777",
    service_id: "AREA-EDI-04",
    kind: "broadband",                 // mobile | broadband | landline | tv
    plan_name: "Fibre 100",
    status: "active",
    monthly_price_minor: NumberLong(2900),   // £29.00, in pennies
    currency: "GBP",
    contract_end_date: null,
  },
  { upsert: true }
);

// An invoice, so there is something to refund against.
db.invoices.replaceOne(
  { tenant_id: "tenant-eu-1", cx_id: "CX-7777", invoice_id: "INV-2026-09" },
  {
    tenant_id: "tenant-eu-1",
    cx_id: "CX-7777",
    invoice_id: "INV-2026-09",
    state: "due",                      // paid | due | overdue | disputed | cancelled
    issued_on: new Date("2026-09-01T00:00:00Z"),
    due_on: new Date("2026-10-01T00:00:00Z"),
    total_minor: NumberLong(2900),
    outstanding_minor: NumberLong(2900),
    currency: "GBP",
  },
  { upsert: true }
);

print("CX-7777 written. Now mint a token for them:");
print("  uv run --env-file .env python scripts/dev_token.py --cx-id CX-7777 --write-env");
print("then send the requests in requests.http with @cx changed to CX-7777.");
