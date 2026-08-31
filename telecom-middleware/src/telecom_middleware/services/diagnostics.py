"""Answer "is this database actually usable by this service", in one command.

Pointing a service at a new cluster fails in a small number of specific ways, and each
of them produces an unhelpful error much later: the host is unreachable, the credentials
are wrong, the user cannot see the database, it is a standalone rather than a replica
set so transactions and change streams do not exist, or the schema step never ran so
every query is a collection scan.

This checks all of them up front and says which one is wrong. It never prints the
connection string: a URI carries the password, and a diagnostic that leaks it into a
terminal, a screenshot or a support ticket is worse than no diagnostic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class StoreReport:
    checks: list[Check] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append(Check(name=name, ok=ok, detail=detail))

    def render(self) -> str:
        lines = [
            f"{'PASS' if c.ok else 'FAIL'}  {c.name}" + (f" - {c.detail}" if c.detail else "")
            for c in self.checks
        ]
        if self.counts:
            lines.append("")
            lines.append("documents:")
            lines += [f"  {name}: {count}" for name, count in sorted(self.counts.items())]
        return "\n".join(lines)


async def inspect_store(store: Any) -> StoreReport:
    """Run every check that can fail when a cluster is newly configured."""
    report = StoreReport()

    database = getattr(store, "database", None)
    if database is None:
        report.add("store", True, "in-memory store; nothing to inspect")
        return report

    try:
        await store.ping()
    except Exception as exc:  # noqa: BLE001 - any failure means unusable
        # The type name, not the message: a pymongo error message quotes the URI.
        report.add(
            "reachable",
            False,
            f"{type(exc).__name__} - check the host, the network "
            "access list, and the username and password",
        )
        return report
    report.add("reachable", True)

    try:
        info = await database.command("buildInfo")
        version = str(info.get("version", "unknown"))
        major = int(version.split(".")[0])
        report.add("server version", major >= 7, version)
    except Exception as exc:  # noqa: BLE001
        report.add("server version", False, type(exc).__name__)
        return report

    try:
        hello = await database.command("hello")
    except Exception as exc:  # noqa: BLE001
        report.add("replica set", False, type(exc).__name__)
        return report

    set_name = hello.get("setName")
    report.add(
        "replica set",
        bool(set_name),
        f"set {set_name}"
        if set_name
        else "standalone - transactions and change streams both need a replica set",
    )
    report.add(
        "writable primary",
        bool(hello.get("isWritablePrimary") or hello.get("ismaster")),
        "" if hello.get("isWritablePrimary") else "connected to a secondary or no primary elected",
    )

    from telecom_middleware.repositories.schema import ALL_COLLECTIONS, missing_indexes

    try:
        gaps = await missing_indexes(database)
    except Exception as exc:  # noqa: BLE001
        report.add("indexes", False, f"{type(exc).__name__} - can this user read the database?")
        return report

    report.add(
        "indexes",
        not gaps,
        "every declared index is present"
        if not gaps
        else f"missing in {', '.join(sorted(gaps))} - run: telecom-middleware migrate",
    )

    for spec in ALL_COLLECTIONS:
        try:
            report.counts[spec.name] = await database[spec.name].count_documents({})
        except Exception:  # noqa: BLE001 - a count that fails is not fatal to the check
            report.counts[spec.name] = -1

    if report.counts.get("customers", 0) < 1:
        report.add(
            "demo data",
            True,
            "no customers yet - run: telecom-middleware seed",
        )
    else:
        report.add("demo data", True, f"{report.counts['customers']} customer(s) present")

    return report
