#!/usr/bin/env python3
"""Generate the mongosh seed script from the code, so the two cannot drift.

There are two ways to get the demo dataset into a database: run
``telecom-middleware seed``, which needs Python, or run a mongosh script, which does
not. Maintaining the second by hand would guarantee it eventually disagrees with the
first - a field renamed here, an index added there, and the two paths quietly produce
different databases.

So the mongosh script is generated: this runs the real seeder against an in-memory
store, reads the real index definitions out of the schema module, and writes both out.
Regenerating is one command, and the generated file says so at the top.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from telecom_middleware.repositories.memory import MemoryStore
from telecom_middleware.repositories.schema import ALL_COLLECTIONS
from telecom_middleware.services.seed import DEMO_PASSCODE, DEMO_SUPERVISOR, seed_demo_data

GENERATOR = "scripts/export_seed.py"

#: A fixed salt, used only for the demo passcode in this generated file.
#:
#: Argon2 salts are random by design, and ``hash_passcode`` keeps it that way. Here the
#: randomness would mean the generated file changes on every regeneration, so a diff
#: would never tell anyone whether the data actually changed. The passcode is published
#: in the file's own header, so a fixed salt for it costs nothing - and this constant is
#: deliberately unreachable from the service's hashing path.
_DEMO_SALT = b"telecom-demo-salt-16"[:16]


def deterministic_demo_hash(passcode: str) -> str:
    """The demo passcode hashed reproducibly, so regenerating produces no spurious diff."""
    from argon2.low_level import Type, hash_secret

    return hash_secret(
        secret=passcode.encode("utf-8"),
        salt=_DEMO_SALT,
        time_cost=3,
        memory_cost=65_536,
        parallelism=4,
        hash_len=32,
        type=Type.ID,
    ).decode("ascii")


class FixedClock:
    """A fixed instant, so regenerating without changing anything produces no diff."""

    def now(self) -> datetime:
        return datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC)


async def collect(tenant_id: str) -> dict[str, list[dict[str, Any]]]:
    """Run the real seeder and read back what it wrote."""
    store = MemoryStore()
    await seed_demo_data(store, tenant_id=tenant_id, clock=FixedClock())

    documents: dict[str, list[dict[str, Any]]] = {
        "customers": [
            c.model_dump(mode="python") for c in store.customers.items.all_for(tenant_id)
        ],
        "services": [
            service.model_dump(mode="python")
            for bucket in store.services.items.values()
            for service in bucket
        ],
        "orders": [
            order.model_dump(mode="python")
            for bucket in store.orders.items.values()
            for order in bucket
        ],
        "invoices": [
            invoice.model_dump(mode="python")
            for bucket in store.invoices.items.values()
            for invoice in bucket
        ],
        "network_status": [
            status.model_dump(mode="python") for status in store.network.items.all_for(tenant_id)
        ],
        "tickets": [t.model_dump(mode="python") for t in store.tickets.items.all_for(tenant_id)],
        "callbacks": [
            c.model_dump(mode="python") for c in store.callbacks.items.all_for(tenant_id)
        ],
        "approval_requests": [
            a.model_dump(mode="python") for a in store.approvals.items.all_for(tenant_id)
        ],
        "agent_assignments": [
            {
                "tenant_id": tenant,
                "agent_sub": agent,
                "cx_id": cx_id,
                "assigned_at": FixedClock().now(),
                "assigned_by": DEMO_SUPERVISOR,
            }
            for tenant, agent, cx_id in sorted(store.assignments.pairs)
            if tenant == tenant_id
        ],
    }
    # Substituted after the fact so the seeder itself keeps its random salt: only this
    # generated file is made reproducible, and only for the demo passcode.
    demo_hash = deterministic_demo_hash(DEMO_PASSCODE)
    for customer in documents["customers"]:
        customer["passcode"]["hash"] = demo_hash

    return documents


def to_js_value(value: Any) -> str:
    """Render a Python value as JavaScript that mongosh will store with the right type."""
    if isinstance(value, datetime):
        # ISODate, not a string: a date stored as text sorts wrong and cannot be
        # compared against a real date in a query.
        return f'ISODate("{value.astimezone(UTC).isoformat().replace("+00:00", "Z")}")'
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, int):
        # NumberLong so money in minor units is a 64-bit integer rather than a double.
        # A double here is a rounding bug waiting for a large invoice.
        return f"NumberLong({value})"
    if isinstance(value, float):
        raise TypeError("the seed data must not contain a float; money is minor units")
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(to_js_value(item) for item in value) + "]"
    if isinstance(value, dict):
        pairs = ", ".join(f"{json.dumps(key)}: {to_js_value(item)}" for key, item in value.items())
        return "{" + pairs + "}"
    return json.dumps(str(value))


def validator_statements() -> list[str]:
    """Create each collection with its validator, so the mongosh path is not weaker.

    Without this, an upsert would create the collection with no validation at all, and
    the database built by the script would accept documents the service refuses.
    """
    lines: list[str] = []
    for spec in ALL_COLLECTIONS:
        options = f", {{ validator: {to_js_value(spec.validator)} }}" if spec.validator else ""
        lines.append(
            f'if (!db.getCollectionNames().includes("{spec.name}")) {{\n'
            f'  db.createCollection("{spec.name}"{options});\n'
            f"}}"
            + (
                f'\nelse {{ db.runCommand({{ collMod: "{spec.name}", '
                f"validator: {to_js_value(spec.validator)} }}); }}"
                if spec.validator
                else ""
            )
        )
    return lines


def index_statements() -> list[str]:
    """Emit createIndexes calls straight from the schema module's definitions."""
    lines: list[str] = []
    for spec in ALL_COLLECTIONS:
        if not spec.indexes:
            continue
        rendered = []
        for index in spec.indexes:
            document = dict(index.document)
            name = document.pop("name")
            keys = dict(document.pop("key"))
            options = {key: value for key, value in document.items() if key != "key"}
            option_text = "".join(
                f", {key}: {to_js_value(value)}" for key, value in sorted(options.items())
            )
            key_text = ", ".join(f'"{field}": {direction}' for field, direction in keys.items())
            rendered.append(f'  {{ key: {{ {key_text} }}, name: "{name}"{option_text} }}')
        lines.append(
            f'db.getCollection("{spec.name}").createIndexes([\n' + ",\n".join(rendered) + "\n]);"
        )
    return lines


def render(documents: dict[str, list[dict[str, Any]]], tenant_id: str) -> str:
    parts: list[str] = [
        "// GENERATED FILE - do not edit by hand.",
        f"// Regenerate with:  uv run python {GENERATOR}",
        "//",
        "// Loads the demo dataset and every declared index into the current database.",
        "// Run it against your cluster with:",
        "//",
        '//   mongosh "<your connection string>" --file scripts/seed.mongodb.js',
        "//",
        "// It is safe to run twice: every insert is an upsert keyed the way the unique",
        "// index is, so a second run updates rather than duplicating.",
        f"// The demo passcode for every seeded customer is {DEMO_PASSCODE}.",
        "",
        'print("using database: " + db.getName());',
        "",
        "// --- collections and validators --------------------------------------------",
        "",
    ]
    parts.extend(validator_statements())
    parts += [
        "",
        "// --- indexes ---------------------------------------------------------------",
        "",
    ]
    parts.extend(index_statements())
    parts += [
        "",
        "// --- documents -------------------------------------------------------------",
        "",
    ]

    keys_by_collection = {
        "customers": ("tenant_id", "cx_id"),
        "services": ("tenant_id", "cx_id", "service_id"),
        "orders": ("tenant_id", "cx_id", "order_id"),
        "invoices": ("tenant_id", "cx_id", "invoice_id"),
        "network_status": ("tenant_id", "area_ref"),
        "tickets": ("tenant_id", "ticket_id"),
        "callbacks": ("tenant_id", "callback_id"),
        "approval_requests": ("tenant_id", "request_id"),
        "agent_assignments": ("tenant_id", "agent_sub", "cx_id"),
    }

    for collection, rows in documents.items():
        if not rows:
            continue
        parts.append(f"// {collection}: {len(rows)} document(s)")
        for row in rows:
            key_fields = keys_by_collection[collection]
            selector = {field: row[field] for field in key_fields}
            parts.append(
                f'db.getCollection("{collection}").replaceOne(\n'
                f"  {to_js_value(selector)},\n"
                f"  {to_js_value(row)},\n"
                f"  {{ upsert: true }}\n"
                f");"
            )
        parts.append("")

    counts = ", ".join(f'"{name}"' for name in documents if documents[name])
    parts += [
        "// --- what landed -----------------------------------------------------------",
        "",
        f"for (const name of [{counts}]) {{",
        '  print(name + ": " + db.getCollection(name).countDocuments({ tenant_id: '
        + json.dumps(tenant_id)
        + ' }) + " document(s)");',
        "}",
        "",
        'print("done. Every seeded customer has passcode ' + DEMO_PASSCODE + '.");',
        "",
    ]
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default="tenant-eu-1")
    parser.add_argument("--out", default="scripts/seed.mongodb.js")
    args = parser.parse_args()

    documents = asyncio.run(collect(args.tenant))
    Path(args.out).write_text(render(documents, args.tenant), encoding="utf-8")
    total = sum(len(rows) for rows in documents.values())
    print(f"wrote {args.out}: {total} documents across {len(documents)} collections")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
