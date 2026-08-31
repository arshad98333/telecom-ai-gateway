/* Look at what the seed loaded.
 *
 * Open with the MongoDB extension connected to your cluster, then press the play
 * button (or Ctrl+Alt+S) to run the whole file. Each block prints its own result.
 */

use("telecom");

// Everything, in one glance.
print("collection counts:");
for (const name of db.getCollectionNames().sort()) {
  print("  " + name.padEnd(20) + db.getCollection(name).countDocuments({}));
}

// The two seeded customers. Note what is NOT here: the passcode. Only its Argon2id
// hash is stored, and even that is projected away below because nothing should ever
// need to read it.
db.customers
  .find({ tenant_id: "tenant-eu-1" }, { _id: 0, "passcode.hash": 0, email: 0, phone: 0 })
  .toArray();

// Money is an integer count of pennies. 6300 is £63.00. A decimal here would be a
// rounding bug waiting for a large invoice, which is why the collection validator
// refuses a double in amount_minor.
db.invoices
  .find({ tenant_id: "tenant-eu-1" }, { _id: 0, invoice_id: 1, state: 1, total_minor: 1, outstanding_minor: 1, currency: 1 })
  .toArray();

// The pending refund waiting for a supervisor. The evidence was read from the invoice
// when the request was raised, not copied from what the customer said.
db.approval_requests.find({ state: "pending" }, { _id: 0 }).toArray();

// Which accounts an agent may act on. This collection is the only answer to that
// question - the service never takes it from a token claim.
db.agent_assignments.find({}, { _id: 0 }).toArray();
