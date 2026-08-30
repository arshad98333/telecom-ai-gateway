"""The check that tells someone why a new cluster is not working."""

from __future__ import annotations

from typing import Any

import pytest

from telecom_middleware.services.diagnostics import inspect_store


class FakeCollection:
    def __init__(self, count: int = 0, indexes: dict[str, Any] | None = None) -> None:
        self._count = count
        self._indexes = indexes or {}

    async def count_documents(self, query: dict[str, Any]) -> int:
        del query
        return self._count

    async def index_information(self) -> dict[str, Any]:
        return self._indexes


class FakeDatabase:
    def __init__(
        self,
        *,
        version: str = "7.0.14",
        set_name: str | None = "atlas-abc123-shard-0",
        primary: bool = True,
        collections: list[str] | None = None,
        indexes_present: bool = True,
        counts: dict[str, int] | None = None,
    ) -> None:
        from telecom_middleware.repositories.schema import ALL_COLLECTIONS

        self._version = version
        self._set_name = set_name
        self._primary = primary
        self._names = collections if collections is not None else [s.name for s in ALL_COLLECTIONS]
        self._counts = counts or {}
        self._index_map: dict[str, dict[str, Any]] = {
            spec.name: (
                {index.document["name"]: {} for index in spec.indexes} if indexes_present else {}
            )
            for spec in ALL_COLLECTIONS
        }

    async def command(self, name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        if name == "buildInfo":
            return {"version": self._version}
        if name == "hello":
            return {"setName": self._set_name, "isWritablePrimary": self._primary}
        return {"ok": 1}

    async def list_collection_names(self) -> list[str]:
        return list(self._names)

    def __getitem__(self, name: str) -> FakeCollection:
        return FakeCollection(self._counts.get(name, 0), self._index_map.get(name, {}))


class FakeStore:
    def __init__(self, database: Any, *, reachable: bool = True) -> None:
        self.database = database
        self._reachable = reachable

    async def ping(self) -> None:
        if not self._reachable:
            raise ConnectionError("connection to mongodb+srv://user:hunter2@cluster refused")


async def test_a_healthy_cluster_passes_every_check() -> None:
    report = await inspect_store(FakeStore(FakeDatabase(counts={"customers": 2})))

    assert report.ok
    assert report.counts["customers"] == 2
    assert "2 customer(s) present" in report.render()


async def test_an_unreachable_cluster_says_what_to_look_at_and_leaks_nothing() -> None:
    report = await inspect_store(FakeStore(FakeDatabase(), reachable=False))

    rendered = report.render()
    assert not report.ok
    assert "network access list" in rendered
    # A pymongo error message quotes the URI, password and all.
    assert "hunter2" not in rendered
    assert "mongodb+srv" not in rendered


async def test_a_standalone_server_is_reported_as_the_problem_it_is() -> None:
    report = await inspect_store(FakeStore(FakeDatabase(set_name=None)))

    assert not report.ok
    assert "transactions and change streams both need a replica set" in report.render()


async def test_connecting_to_a_secondary_is_reported() -> None:
    report = await inspect_store(FakeStore(FakeDatabase(primary=False)))

    assert not report.ok
    assert "secondary" in report.render()


async def test_an_old_server_version_fails_the_check() -> None:
    report = await inspect_store(FakeStore(FakeDatabase(version="5.0.9")))

    assert not report.ok
    assert "5.0.9" in report.render()


async def test_a_database_with_no_schema_applied_says_which_command_to_run() -> None:
    report = await inspect_store(FakeStore(FakeDatabase(indexes_present=False)))

    assert not report.ok
    assert "telecom-middleware migrate" in report.render()


async def test_an_empty_but_correct_database_suggests_seeding() -> None:
    report = await inspect_store(FakeStore(FakeDatabase(counts={})))

    assert report.ok
    assert "telecom-middleware seed" in report.render()


async def test_the_in_memory_store_has_nothing_to_inspect() -> None:
    from telecom_middleware.repositories.memory import MemoryStore

    report = await inspect_store(MemoryStore())

    assert report.ok
    assert "nothing to inspect" in report.render()


@pytest.mark.parametrize("failing", ["buildInfo", "hello"])
async def test_a_command_that_fails_is_reported_rather_than_raised(failing: str) -> None:
    class Broken(FakeDatabase):
        async def command(self, name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
            if name == failing:
                raise RuntimeError("not authorized on admin to execute command")
            return await super().command(name, *args, **kwargs)

    report = await inspect_store(FakeStore(Broken()))

    assert not report.ok
    assert "RuntimeError" in report.render()
