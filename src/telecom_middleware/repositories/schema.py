"""Collections, indexes and validators: the database's shape, as code.

Two things live here and nowhere else.

**Indexes are declared next to the query they serve.** A query the code can express but
no index serves is a latency incident waiting for a busy Tuesday, so each entry names
the read path it exists for, and a test asserts the deployed collection has every one.

**Validators are the last line, not the first.** Input is validated at the API edge into
typed models; the collection validators exist so a write from anywhere else - a
migration, a console, a future service - still cannot store a document this service
would then fail to read.

Applying them is idempotent, so the startup path can run it every time and a deployment
never needs a separate migration step for index changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pymongo import ASCENDING, DESCENDING, IndexModel


@dataclass(frozen=True, slots=True)
class CollectionSpec:
    name: str
    #: Why this collection exists, in one line, for whoever opens the database.
    purpose: str
    indexes: tuple[IndexModel, ...]
    validator: dict[str, Any] | None = None


def _required(*names: str) -> list[str]:
    return list(names)


CUSTOMERS = CollectionSpec(
    name="customers",
    purpose="Account records. The passcode is present only as an Argon2id hash.",
    indexes=(
        IndexModel([("tenant_id", ASCENDING), ("cx_id", ASCENDING)], name="tenant_cx", unique=True),
    ),
    validator={
        "$jsonSchema": {
            "bsonType": "object",
            "required": _required(
                "tenant_id", "cx_id", "account_status", "account_type", "passcode"
            ),
            "properties": {
                "tenant_id": {"bsonType": "string"},
                "cx_id": {"bsonType": "string"},
                "account_status": {"enum": ["active", "suspended", "closed", "pending"]},
                "account_type": {"enum": ["consumer", "business"]},
                "passcode": {
                    "bsonType": "object",
                    "required": _required("hash"),
                    "properties": {"hash": {"bsonType": "string"}},
                },
            },
        }
    },
)

SERVICES = CollectionSpec(
    name="services",
    purpose="Active services per customer. Read path: list a customer's services.",
    indexes=(
        IndexModel(
            [("tenant_id", ASCENDING), ("cx_id", ASCENDING), ("status", ASCENDING)],
            name="tenant_cx_status",
        ),
        IndexModel(
            [("tenant_id", ASCENDING), ("cx_id", ASCENDING), ("service_id", ASCENDING)],
            name="tenant_cx_service",
            unique=True,
        ),
    ),
)

ORDERS = CollectionSpec(
    name="orders",
    purpose="Order history. Read path: most recent orders, or one by reference.",
    indexes=(
        IndexModel(
            [("tenant_id", ASCENDING), ("cx_id", ASCENDING), ("placed_at", DESCENDING)],
            name="tenant_cx_recent",
        ),
        IndexModel(
            [("tenant_id", ASCENDING), ("cx_id", ASCENDING), ("order_id", ASCENDING)],
            name="tenant_cx_order",
            unique=True,
        ),
    ),
)

INVOICES = CollectionSpec(
    name="invoices",
    purpose="Billing summaries. Read path: most recent invoices, or one by reference.",
    indexes=(
        IndexModel(
            [("tenant_id", ASCENDING), ("cx_id", ASCENDING), ("issued_on", DESCENDING)],
            name="tenant_cx_recent",
        ),
        IndexModel(
            [("tenant_id", ASCENDING), ("cx_id", ASCENDING), ("invoice_id", ASCENDING)],
            name="tenant_cx_invoice",
            unique=True,
        ),
    ),
)

NETWORK_STATUS = CollectionSpec(
    name="network_status",
    purpose="Area incidents, shared across the customers in that area.",
    indexes=(
        IndexModel(
            [("tenant_id", ASCENDING), ("area_ref", ASCENDING)], name="tenant_area", unique=True
        ),
    ),
)

AGENT_ASSIGNMENTS = CollectionSpec(
    name="agent_assignments",
    purpose="Which accounts an agent may act on. The only source of that answer.",
    indexes=(
        IndexModel(
            [("tenant_id", ASCENDING), ("agent_sub", ASCENDING), ("cx_id", ASCENDING)],
            name="tenant_agent_cx",
            unique=True,
        ),
        # Expiring an assignment is a business event, so the TTL index is partial: only
        # documents that were given an expiry are swept.
        IndexModel(
            [("expires_at", ASCENDING)],
            name="assignment_ttl",
            expireAfterSeconds=0,
            partialFilterExpression={"expires_at": {"$type": "date"}},
        ),
    ),
)

TICKETS = CollectionSpec(
    name="tickets",
    purpose="Support tickets. Read paths: a customer's tickets, and the open queue.",
    indexes=(
        IndexModel(
            [("tenant_id", ASCENDING), ("ticket_id", ASCENDING)], name="tenant_ticket", unique=True
        ),
        IndexModel(
            [("tenant_id", ASCENDING), ("cx_id", ASCENDING), ("created_at", DESCENDING)],
            name="tenant_cx_recent",
        ),
        IndexModel(
            [("tenant_id", ASCENDING), ("state", ASCENDING), ("created_at", DESCENDING)],
            name="tenant_state_recent",
        ),
    ),
)

CALLBACKS = CollectionSpec(
    name="callbacks",
    purpose="Scheduled callbacks.",
    indexes=(
        IndexModel(
            [("tenant_id", ASCENDING), ("callback_id", ASCENDING)],
            name="tenant_callback",
            unique=True,
        ),
        IndexModel(
            [("tenant_id", ASCENDING), ("scheduled_for", ASCENDING)], name="tenant_schedule"
        ),
    ),
)

APPROVAL_REQUESTS = CollectionSpec(
    name="approval_requests",
    purpose="Restricted actions awaiting a human. Low volume, highest value.",
    indexes=(
        IndexModel(
            [("tenant_id", ASCENDING), ("request_id", ASCENDING)],
            name="tenant_request",
            unique=True,
        ),
        # The supervisor queue: oldest pending first, so nothing waits forever.
        IndexModel(
            [("tenant_id", ASCENDING), ("state", ASCENDING), ("created_at", ASCENDING)],
            name="tenant_state_oldest",
        ),
        IndexModel(
            [("tenant_id", ASCENDING), ("cx_id", ASCENDING), ("created_at", DESCENDING)],
            name="tenant_cx_recent",
        ),
    ),
    validator={
        "$jsonSchema": {
            "bsonType": "object",
            "required": _required("tenant_id", "request_id", "state", "requested_by"),
            "properties": {
                "state": {"enum": ["pending", "approved", "rejected", "expired"]},
                # Money is an integer count of minor units. A double here would be a
                # silent rounding bug that no application test would catch.
                "amount_minor": {"bsonType": ["long", "int", "null"]},
            },
        }
    },
)

CASES = CollectionSpec(
    name="cases",
    purpose="Voice case state, so an interrupted call resumes rather than restarts.",
    indexes=(
        IndexModel(
            [("tenant_id", ASCENDING), ("case_id", ASCENDING)], name="tenant_case", unique=True
        ),
        IndexModel(
            [
                ("tenant_id", ASCENDING),
                ("cx_id", ASCENDING),
                ("status", ASCENDING),
                ("updated_at", DESCENDING),
            ],
            name="tenant_cx_resume",
        ),
    ),
)

AUDIT_RECORDS = CollectionSpec(
    name="audit_records",
    purpose="Append-only, hash-chained. One record per state change and per refusal.",
    indexes=(
        IndexModel([("tenant_id", ASCENDING), ("seq", ASCENDING)], name="tenant_seq", unique=True),
        IndexModel(
            [("tenant_id", ASCENDING), ("correlation_id", ASCENDING)], name="tenant_correlation"
        ),
        IndexModel([("tenant_id", ASCENDING), ("at", DESCENDING)], name="tenant_recent"),
    ),
)

OUTBOX = CollectionSpec(
    name="outbox",
    purpose="Events awaiting relay, written in the same transaction as their change.",
    indexes=(
        IndexModel([("event.event_id", ASCENDING)], name="event_id", unique=True),
        IndexModel([("status", ASCENDING), ("created_at", ASCENDING)], name="relay_scan"),
        IndexModel([("event.tenant_id", ASCENDING), ("event.sequence", ASCENDING)], name="replay"),
    ),
)

TENANT_SEQUENCES = CollectionSpec(
    name="tenant_sequences",
    purpose="Monotonic event sequence per tenant, so subscribers can resume exactly.",
    indexes=(IndexModel([("tenant_id", ASCENDING)], name="tenant", unique=True),),
)

IDEMPOTENCY_KEYS = CollectionSpec(
    name="idempotency_keys",
    purpose="Write deduplication. Self-expiring, so nothing has to sweep it.",
    indexes=(
        IndexModel(
            [("tenant_id", ASCENDING), ("scope", ASCENDING), ("key", ASCENDING)],
            name="tenant_scope_key",
            unique=True,
        ),
        IndexModel([("expires_at", ASCENDING)], name="idempotency_ttl", expireAfterSeconds=0),
    ),
)

STREAM_TOKENS = CollectionSpec(
    name="stream_tokens",
    purpose="Change-stream resume tokens, so a restart continues where it stopped.",
    indexes=(IndexModel([("watcher", ASCENDING)], name="watcher", unique=True),),
)

ALL_COLLECTIONS: tuple[CollectionSpec, ...] = (
    CUSTOMERS,
    SERVICES,
    ORDERS,
    INVOICES,
    NETWORK_STATUS,
    AGENT_ASSIGNMENTS,
    TICKETS,
    CALLBACKS,
    APPROVAL_REQUESTS,
    CASES,
    AUDIT_RECORDS,
    OUTBOX,
    TENANT_SEQUENCES,
    IDEMPOTENCY_KEYS,
    STREAM_TOKENS,
)

#: Collections whose changes are worth watching. Watching everything would ship the
#: audit trail's full contents through the change stream for no reason.
WATCHED_COLLECTIONS: tuple[str, ...] = (OUTBOX.name,)


async def apply_schema(database: Any) -> None:
    """Create collections, validators and indexes. Safe to run on every startup."""
    existing = set(await database.list_collection_names())
    for spec in ALL_COLLECTIONS:
        if spec.name not in existing:
            await database.create_collection(
                spec.name, **({"validator": spec.validator} if spec.validator else {})
            )
        elif spec.validator is not None:
            await database.command("collMod", spec.name, validator=spec.validator)
        if spec.indexes:
            await database[spec.name].create_indexes(list(spec.indexes))


async def missing_indexes(database: Any) -> dict[str, list[str]]:
    """Index names declared here but absent from the database, per collection.

    Used by a test and by the readiness path, so a deployment that skipped the schema
    step is loud rather than merely slow.
    """
    gaps: dict[str, list[str]] = {}
    names = set(await database.list_collection_names())
    for spec in ALL_COLLECTIONS:
        if spec.name not in names:
            gaps[spec.name] = [index.document["name"] for index in spec.indexes]
            continue
        present = set(await database[spec.name].index_information())
        declared = {index.document["name"] for index in spec.indexes}
        absent = sorted(declared - present)
        if absent:
            gaps[spec.name] = absent
    return gaps
