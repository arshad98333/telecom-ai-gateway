// GENERATED FILE - do not edit by hand.
// Regenerate with:  uv run python scripts/export_seed.py
//
// Loads the demo dataset and every declared index into the current database.
// Run it against your cluster with:
//
//   mongosh "<your connection string>" --file scripts/seed.mongodb.js
//
// It is safe to run twice: every insert is an upsert keyed the way the unique
// index is, so a second run updates rather than duplicating.
// The demo passcode for every seeded customer is 4821.

print("using database: " + db.getName());

// --- collections and validators --------------------------------------------

if (!db.getCollectionNames().includes("customers")) {
  db.createCollection("customers", { validator: {"$jsonSchema": {"bsonType": "object", "required": ["tenant_id", "cx_id", "account_status", "account_type", "passcode"], "properties": {"tenant_id": {"bsonType": "string"}, "cx_id": {"bsonType": "string"}, "account_status": {"enum": ["active", "suspended", "closed", "pending"]}, "account_type": {"enum": ["consumer", "business"]}, "passcode": {"bsonType": "object", "required": ["hash"], "properties": {"hash": {"bsonType": "string"}}}}}} });
}
else { db.runCommand({ collMod: "customers", validator: {"$jsonSchema": {"bsonType": "object", "required": ["tenant_id", "cx_id", "account_status", "account_type", "passcode"], "properties": {"tenant_id": {"bsonType": "string"}, "cx_id": {"bsonType": "string"}, "account_status": {"enum": ["active", "suspended", "closed", "pending"]}, "account_type": {"enum": ["consumer", "business"]}, "passcode": {"bsonType": "object", "required": ["hash"], "properties": {"hash": {"bsonType": "string"}}}}}} }); }
if (!db.getCollectionNames().includes("services")) {
  db.createCollection("services");
}
if (!db.getCollectionNames().includes("orders")) {
  db.createCollection("orders");
}
if (!db.getCollectionNames().includes("invoices")) {
  db.createCollection("invoices");
}
if (!db.getCollectionNames().includes("network_status")) {
  db.createCollection("network_status");
}
if (!db.getCollectionNames().includes("agent_assignments")) {
  db.createCollection("agent_assignments");
}
if (!db.getCollectionNames().includes("tickets")) {
  db.createCollection("tickets");
}
if (!db.getCollectionNames().includes("callbacks")) {
  db.createCollection("callbacks");
}
if (!db.getCollectionNames().includes("approval_requests")) {
  db.createCollection("approval_requests", { validator: {"$jsonSchema": {"bsonType": "object", "required": ["tenant_id", "request_id", "state", "requested_by"], "properties": {"state": {"enum": ["pending", "approved", "rejected", "expired"]}, "amount_minor": {"bsonType": ["long", "int", "null"]}}}} });
}
else { db.runCommand({ collMod: "approval_requests", validator: {"$jsonSchema": {"bsonType": "object", "required": ["tenant_id", "request_id", "state", "requested_by"], "properties": {"state": {"enum": ["pending", "approved", "rejected", "expired"]}, "amount_minor": {"bsonType": ["long", "int", "null"]}}}} }); }
if (!db.getCollectionNames().includes("cases")) {
  db.createCollection("cases");
}
if (!db.getCollectionNames().includes("audit_records")) {
  db.createCollection("audit_records");
}
if (!db.getCollectionNames().includes("outbox")) {
  db.createCollection("outbox");
}
if (!db.getCollectionNames().includes("tenant_sequences")) {
  db.createCollection("tenant_sequences");
}
if (!db.getCollectionNames().includes("idempotency_keys")) {
  db.createCollection("idempotency_keys");
}
if (!db.getCollectionNames().includes("stream_tokens")) {
  db.createCollection("stream_tokens");
}

// --- indexes ---------------------------------------------------------------

db.getCollection("customers").createIndexes([
  { key: { "tenant_id": 1, "cx_id": 1 }, name: "tenant_cx", unique: true }
]);
db.getCollection("services").createIndexes([
  { key: { "tenant_id": 1, "cx_id": 1, "status": 1 }, name: "tenant_cx_status" },
  { key: { "tenant_id": 1, "cx_id": 1, "service_id": 1 }, name: "tenant_cx_service", unique: true }
]);
db.getCollection("orders").createIndexes([
  { key: { "tenant_id": 1, "cx_id": 1, "placed_at": -1 }, name: "tenant_cx_recent" },
  { key: { "tenant_id": 1, "cx_id": 1, "order_id": 1 }, name: "tenant_cx_order", unique: true }
]);
db.getCollection("invoices").createIndexes([
  { key: { "tenant_id": 1, "cx_id": 1, "issued_on": -1 }, name: "tenant_cx_recent" },
  { key: { "tenant_id": 1, "cx_id": 1, "invoice_id": 1 }, name: "tenant_cx_invoice", unique: true }
]);
db.getCollection("network_status").createIndexes([
  { key: { "tenant_id": 1, "area_ref": 1 }, name: "tenant_area", unique: true }
]);
db.getCollection("agent_assignments").createIndexes([
  { key: { "tenant_id": 1, "agent_sub": 1, "cx_id": 1 }, name: "tenant_agent_cx", unique: true },
  { key: { "expires_at": 1 }, name: "assignment_ttl", expireAfterSeconds: NumberLong(0), partialFilterExpression: {"expires_at": {"$type": "date"}} }
]);
db.getCollection("tickets").createIndexes([
  { key: { "tenant_id": 1, "ticket_id": 1 }, name: "tenant_ticket", unique: true },
  { key: { "tenant_id": 1, "cx_id": 1, "created_at": -1 }, name: "tenant_cx_recent" },
  { key: { "tenant_id": 1, "state": 1, "created_at": -1 }, name: "tenant_state_recent" }
]);
db.getCollection("callbacks").createIndexes([
  { key: { "tenant_id": 1, "callback_id": 1 }, name: "tenant_callback", unique: true },
  { key: { "tenant_id": 1, "scheduled_for": 1 }, name: "tenant_schedule" }
]);
db.getCollection("approval_requests").createIndexes([
  { key: { "tenant_id": 1, "request_id": 1 }, name: "tenant_request", unique: true },
  { key: { "tenant_id": 1, "state": 1, "created_at": 1 }, name: "tenant_state_oldest" },
  { key: { "tenant_id": 1, "cx_id": 1, "created_at": -1 }, name: "tenant_cx_recent" }
]);
db.getCollection("cases").createIndexes([
  { key: { "tenant_id": 1, "case_id": 1 }, name: "tenant_case", unique: true },
  { key: { "tenant_id": 1, "cx_id": 1, "status": 1, "updated_at": -1 }, name: "tenant_cx_resume" }
]);
db.getCollection("audit_records").createIndexes([
  { key: { "tenant_id": 1, "seq": 1 }, name: "tenant_seq", unique: true },
  { key: { "tenant_id": 1, "correlation_id": 1 }, name: "tenant_correlation" },
  { key: { "tenant_id": 1, "at": -1 }, name: "tenant_recent" }
]);
db.getCollection("outbox").createIndexes([
  { key: { "event.event_id": 1 }, name: "event_id", unique: true },
  { key: { "status": 1, "created_at": 1 }, name: "relay_scan" },
  { key: { "event.tenant_id": 1, "event.sequence": 1 }, name: "replay" }
]);
db.getCollection("tenant_sequences").createIndexes([
  { key: { "tenant_id": 1 }, name: "tenant", unique: true }
]);
db.getCollection("idempotency_keys").createIndexes([
  { key: { "tenant_id": 1, "scope": 1, "key": 1 }, name: "tenant_scope_key", unique: true },
  { key: { "expires_at": 1 }, name: "idempotency_ttl", expireAfterSeconds: NumberLong(0) }
]);
db.getCollection("stream_tokens").createIndexes([
  { key: { "watcher": 1 }, name: "watcher", unique: true }
]);

// --- documents -------------------------------------------------------------

// customers: 2 document(s)
db.getCollection("customers").replaceOne(
  {"tenant_id": "tenant-eu-1", "cx_id": "CX-1234"},
  {"tenant_id": "tenant-eu-1", "cx_id": "CX-1234", "account_status": "active", "account_type": "consumer", "display_name": "J. Okonkwo", "customer_since": ISODate("2021-06-17T12:00:00Z"), "billing_postcode_suffix": "4AB", "email": "jo@example.com", "phone": "+44 7700 900123", "passcode": {"hash": "$argon2id$v=19$m=65536,t=3,p=4$dGVsZWNvbS1kZW1vLXNhbA$fxEa3m2uhbbH/3PPhXRLylQjNP/fCsQIdYurMLw6y7M", "failed_attempts": NumberLong(0), "locked_until": null, "updated_at": ISODate("2026-08-30T12:00:00Z")}, "created_at": ISODate("2026-08-30T12:00:00Z"), "updated_at": ISODate("2026-08-30T12:00:00Z")},
  { upsert: true }
);
db.getCollection("customers").replaceOne(
  {"tenant_id": "tenant-eu-1", "cx_id": "CX-5555"},
  {"tenant_id": "tenant-eu-1", "cx_id": "CX-5555", "account_status": "suspended", "account_type": "business", "display_name": "Rivera Logistics Ltd", "customer_since": ISODate("2020-02-03T12:00:00Z"), "billing_postcode_suffix": "9ZQ", "email": "accounts@rivera.example", "phone": "+44 7700 900555", "passcode": {"hash": "$argon2id$v=19$m=65536,t=3,p=4$dGVsZWNvbS1kZW1vLXNhbA$fxEa3m2uhbbH/3PPhXRLylQjNP/fCsQIdYurMLw6y7M", "failed_attempts": NumberLong(0), "locked_until": null, "updated_at": ISODate("2026-08-30T12:00:00Z")}, "created_at": ISODate("2026-08-30T12:00:00Z"), "updated_at": ISODate("2026-08-30T12:00:00Z")},
  { upsert: true }
);

// services: 2 document(s)
db.getCollection("services").replaceOne(
  {"tenant_id": "tenant-eu-1", "cx_id": "CX-1234", "service_id": "AREA-EDI-04"},
  {"tenant_id": "tenant-eu-1", "cx_id": "CX-1234", "service_id": "AREA-EDI-04", "kind": "mobile", "plan_name": "Unlimited 5G", "status": "active", "monthly_price_minor": NumberLong(2400), "currency": "GBP", "contract_end_date": ISODate("2027-03-18T12:00:00Z")},
  { upsert: true }
);
db.getCollection("services").replaceOne(
  {"tenant_id": "tenant-eu-1", "cx_id": "CX-1234", "service_id": "SVC-002"},
  {"tenant_id": "tenant-eu-1", "cx_id": "CX-1234", "service_id": "SVC-002", "kind": "broadband", "plan_name": "Fibre 500", "status": "active", "monthly_price_minor": NumberLong(3900), "currency": "GBP", "contract_end_date": ISODate("2027-03-18T12:00:00Z")},
  { upsert: true }
);

// orders: 1 document(s)
db.getCollection("orders").replaceOne(
  {"tenant_id": "tenant-eu-1", "cx_id": "CX-1234", "order_id": "ORD-9001"},
  {"tenant_id": "tenant-eu-1", "cx_id": "CX-1234", "order_id": "ORD-9001", "state": "dispatched", "placed_at": ISODate("2026-08-20T12:00:00Z"), "expected_by": ISODate("2026-09-02T12:00:00Z"), "summary": "Replacement router"},
  { upsert: true }
);

// invoices: 2 document(s)
db.getCollection("invoices").replaceOne(
  {"tenant_id": "tenant-eu-1", "cx_id": "CX-1234", "invoice_id": "INV-2026-08"},
  {"tenant_id": "tenant-eu-1", "cx_id": "CX-1234", "invoice_id": "INV-2026-08", "state": "due", "issued_on": ISODate("2026-08-01T12:00:00Z"), "due_on": ISODate("2026-09-01T12:00:00Z"), "total_minor": NumberLong(6300), "outstanding_minor": NumberLong(6300), "currency": "GBP"},
  { upsert: true }
);
db.getCollection("invoices").replaceOne(
  {"tenant_id": "tenant-eu-1", "cx_id": "CX-5555", "invoice_id": "INV-2026-06"},
  {"tenant_id": "tenant-eu-1", "cx_id": "CX-5555", "invoice_id": "INV-2026-06", "state": "overdue", "issued_on": ISODate("2026-06-01T12:00:00Z"), "due_on": ISODate("2026-07-01T12:00:00Z"), "total_minor": NumberLong(41000), "outstanding_minor": NumberLong(41000), "currency": "GBP"},
  { upsert: true }
);

// network_status: 2 document(s)
db.getCollection("network_status").replaceOne(
  {"tenant_id": "tenant-eu-1", "area_ref": "AREA-EDI-04"},
  {"tenant_id": "tenant-eu-1", "area_ref": "AREA-EDI-04", "state": "degraded", "incident_id": "INC-5512", "started_at": ISODate("2026-08-30T06:00:00Z"), "estimated_resolution": ISODate("2026-08-30T18:00:00Z"), "affected_services": ["broadband"], "message": "Engineers are working on a fault affecting broadband in this area.", "updated_at": ISODate("2026-08-30T12:00:00Z")},
  { upsert: true }
);
db.getCollection("network_status").replaceOne(
  {"tenant_id": "tenant-eu-1", "area_ref": "AREA-DEFAULT"},
  {"tenant_id": "tenant-eu-1", "area_ref": "AREA-DEFAULT", "state": "operational", "incident_id": null, "started_at": null, "estimated_resolution": null, "affected_services": [], "message": "No known issues in this area.", "updated_at": ISODate("2026-08-30T12:00:00Z")},
  { upsert: true }
);

// tickets: 1 document(s)
db.getCollection("tickets").replaceOne(
  {"tenant_id": "tenant-eu-1", "ticket_id": "TCK-seed-0001"},
  {"tenant_id": "tenant-eu-1", "ticket_id": "TCK-seed-0001", "cx_id": "CX-1234", "category": "network", "subject": "Broadband drops every evening", "description": "The connection drops around eight most evenings.", "priority": "normal", "state": "open", "created_at": ISODate("2026-08-29T12:00:00Z"), "created_by": "auth0|customer-1234", "updated_at": ISODate("2026-08-29T12:00:00Z"), "cancellable_until": null, "case_id": null},
  { upsert: true }
);

// callbacks: 1 document(s)
db.getCollection("callbacks").replaceOne(
  {"tenant_id": "tenant-eu-1", "callback_id": "CB-seed-0001"},
  {"tenant_id": "tenant-eu-1", "callback_id": "CB-seed-0001", "cx_id": "CX-1234", "scheduled_for": ISODate("2026-08-31T12:00:00Z"), "window": "morning", "reason": "Discuss the outage credit", "state": "scheduled", "created_at": ISODate("2026-08-30T12:00:00Z"), "created_by": "auth0|customer-1234", "cancellable_until": ISODate("2026-08-31T08:00:00Z")},
  { upsert: true }
);

// approval_requests: 1 document(s)
db.getCollection("approval_requests").replaceOne(
  {"tenant_id": "tenant-eu-1", "request_id": "APR-seed-0001"},
  {"tenant_id": "tenant-eu-1", "request_id": "APR-seed-0001", "cx_id": "CX-1234", "action": "refund", "amount_minor": NumberLong(450), "currency": "GBP", "reason": "service_outage", "justification": "Broadband unavailable for three days in August.", "evidence": {"invoice_id": "INV-2026-08", "invoice_total_minor": NumberLong(6300), "incident_id": "INC-5512"}, "state": "pending", "requested_by": "auth0|customer-1234", "requested_by_role": "customer", "created_at": ISODate("2026-08-30T10:00:00Z"), "expires_at": ISODate("2026-09-01T12:00:00Z"), "decision": null, "case_id": null},
  { upsert: true }
);

// agent_assignments: 1 document(s)
db.getCollection("agent_assignments").replaceOne(
  {"tenant_id": "tenant-eu-1", "agent_sub": "auth0|agent-7", "cx_id": "CX-5555"},
  {"tenant_id": "tenant-eu-1", "agent_sub": "auth0|agent-7", "cx_id": "CX-5555", "assigned_at": ISODate("2026-08-30T12:00:00Z"), "assigned_by": "auth0|supervisor-1"},
  { upsert: true }
);

// --- what landed -----------------------------------------------------------

for (const name of ["customers", "services", "orders", "invoices", "network_status", "tickets", "callbacks", "approval_requests", "agent_assignments"]) {
  print(name + ": " + db.getCollection(name).countDocuments({ tenant_id: "tenant-eu-1" }) + " document(s)");
}

print("done. Every seeded customer has passcode 4821.");
