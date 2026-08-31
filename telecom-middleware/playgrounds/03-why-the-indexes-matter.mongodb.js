/* Prove the indexes are doing the work, rather than trusting that they are.
 *
 * Every read path in the service maps to exactly one compound index, tenant first. A
 * query the code can express but no index serves is a latency incident waiting for a
 * busy Tuesday, so it is worth seeing the plan once with your own eyes.
 */

use("telecom");

// What is actually built. Each name here is declared in repositories/schema.py next to
// the query it exists for.
print("indexes on invoices:");
db.invoices.getIndexes().forEach((index) => print("  " + index.name + "  " + JSON.stringify(index.key)));

// The real read path: one customer's most recent invoices, inside their tenant.
// Look for "IXSCAN" and the index name in the winning plan. A "COLLSCAN" here would
// mean the schema step never ran.
db.invoices
  .find({ tenant_id: "tenant-eu-1", cx_id: "CX-1234" })
  .sort({ issued_on: -1 })
  .limit(5)
  .explain("executionStats");

// The supervisor queue: oldest pending first, so nothing waits forever.
db.approval_requests
  .find({ tenant_id: "tenant-eu-1", state: "pending" })
  .sort({ created_at: 1 })
  .explain("executionStats");

// Tenant isolation is a property of the data path, not a check somewhere above it.
// The same customer reference in another tenant is a different record, and the service
// can only ever ask for one tenant because every repository method requires it.
print("CX-1234 in tenant-eu-1: " + db.customers.countDocuments({ tenant_id: "tenant-eu-1", cx_id: "CX-1234" }));
print("CX-1234 in tenant-us-9: " + db.customers.countDocuments({ tenant_id: "tenant-us-9", cx_id: "CX-1234" }));

// The audit chain. Each record carries the hash of the one before it, so an edit or a
// deletion breaks it from that point. `telecom-middleware` verifies it in one pass;
// this is the raw view.
db.audit_records
  .find({ tenant_id: "tenant-eu-1" }, { _id: 0, seq: 1, action: 1, decision: 1, cx_ref: 1, previous_hash: 1, entry_hash: 1 })
  .sort({ seq: 1 })
  .limit(10)
  .toArray();
