"""Duplicate writes are the risk this store exists to remove."""

import asyncio
import json
from typing import Any

import pytest

from telecom_mcp.adapters.idempotency import (
    MemoryIdempotencyStore,
    RedisIdempotencyStore,
    ReservationState,
    fingerprint,
    scoped_key,
)
from telecom_mcp.domain.errors import IdempotencyKeyReusedError
from tests.fakes import FrozenClock

ARGS: dict[str, Any] = {"cx_id": "CX-1234", "subject": "Bill looks wrong"}


class FakeRedis:
    """Just enough Redis to exercise the adapter: SET NX EX, GET, DELETE, PING."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
        del ex
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)

    async def ping(self) -> None:
        return None


def memory() -> MemoryIdempotencyStore:
    return MemoryIdempotencyStore(clock=FrozenClock(), ttl_s=86_400)


def stores() -> list[Any]:
    return [memory(), RedisIdempotencyStore(FakeRedis())]


def test_the_key_is_namespaced_by_tenant_and_customer() -> None:
    a = scoped_key("tenant-eu-1", "CX-1234", "create_support_ticket", "k")
    b = scoped_key("tenant-us-9", "CX-1234", "create_support_ticket", "k")

    assert a != b


def test_the_request_fingerprint_ignores_key_order_but_not_content() -> None:
    assert fingerprint("t", "c", "tool", {"a": 1, "b": 2}) == fingerprint(
        "t", "c", "tool", {"b": 2, "a": 1}
    )
    assert fingerprint("t", "c", "tool", {"a": 1}) != fingerprint("t", "c", "tool", {"a": 2})


@pytest.mark.parametrize("store", stores(), ids=["memory", "redis"])
async def test_the_first_call_reserves_the_key(store: Any) -> None:
    reservation = await store.reserve("k1", fingerprint("t", "c", "tool", ARGS))

    assert reservation.state is ReservationState.NEW


@pytest.mark.parametrize("store", stores(), ids=["memory", "redis"])
async def test_a_repeat_while_the_first_call_is_running_does_not_start_a_second(
    store: Any,
) -> None:
    request_hash = fingerprint("t", "c", "tool", ARGS)
    await store.reserve("k1", request_hash)

    assert (await store.reserve("k1", request_hash)).state is ReservationState.IN_PROGRESS


@pytest.mark.parametrize("store", stores(), ids=["memory", "redis"])
async def test_a_repeat_after_completion_returns_the_original_result(store: Any) -> None:
    request_hash = fingerprint("t", "c", "tool", ARGS)
    await store.reserve("k1", request_hash)
    await store.complete("k1", {"ticket_id": "TCK-1"})

    repeat = await store.reserve("k1", request_hash)

    assert repeat.state is ReservationState.COMPLETED
    assert repeat.result == {"ticket_id": "TCK-1"}


@pytest.mark.parametrize("store", stores(), ids=["memory", "redis"])
async def test_reusing_a_key_with_different_input_is_an_error_not_a_silent_replay(
    store: Any,
) -> None:
    await store.reserve("k1", fingerprint("t", "c", "tool", ARGS))

    with pytest.raises(IdempotencyKeyReusedError):
        await store.reserve("k1", fingerprint("t", "c", "tool", {"cx_id": "CX-9999"}))


@pytest.mark.parametrize("store", stores(), ids=["memory", "redis"])
async def test_a_failed_call_releases_its_reservation_so_a_genuine_retry_proceeds(
    store: Any,
) -> None:
    request_hash = fingerprint("t", "c", "tool", ARGS)
    await store.reserve("k1", request_hash)

    await store.release("k1")

    assert (await store.reserve("k1", request_hash)).state is ReservationState.NEW


@pytest.mark.parametrize("store", stores(), ids=["memory", "redis"])
async def test_releasing_a_completed_key_does_not_lose_the_stored_result(store: Any) -> None:
    request_hash = fingerprint("t", "c", "tool", ARGS)
    await store.reserve("k1", request_hash)
    await store.complete("k1", {"ticket_id": "TCK-1"})

    await store.release("k1")

    assert (await store.reserve("k1", request_hash)).state is ReservationState.COMPLETED


async def test_two_simultaneous_reservations_produce_exactly_one_winner() -> None:
    store = memory()
    request_hash = fingerprint("t", "c", "tool", ARGS)

    results = await asyncio.gather(*(store.reserve("k1", request_hash) for _ in range(20)))

    assert [r.state for r in results].count(ReservationState.NEW) == 1


async def test_the_redis_reservation_is_atomic_under_a_race() -> None:
    store = RedisIdempotencyStore(FakeRedis())
    request_hash = fingerprint("t", "c", "tool", ARGS)

    results = await asyncio.gather(*(store.reserve("k1", request_hash) for _ in range(20)))

    assert [r.state for r in results].count(ReservationState.NEW) == 1


async def test_an_expired_entry_frees_the_key_for_reuse() -> None:
    clock = FrozenClock()
    store = MemoryIdempotencyStore(clock=clock, ttl_s=86_400)
    request_hash = fingerprint("t", "c", "tool", ARGS)
    await store.reserve("k1", request_hash)
    await store.complete("k1", {"ticket_id": "TCK-1"})

    clock.advance(86_401)

    assert (await store.reserve("k1", request_hash)).state is ReservationState.NEW


async def test_an_entry_is_still_there_one_second_before_it_expires() -> None:
    clock = FrozenClock()
    store = MemoryIdempotencyStore(clock=clock, ttl_s=86_400)
    request_hash = fingerprint("t", "c", "tool", ARGS)
    await store.reserve("k1", request_hash)
    await store.complete("k1", {"ticket_id": "TCK-1"})

    clock.advance(86_399)

    assert (await store.reserve("k1", request_hash)).state is ReservationState.COMPLETED


async def test_a_redis_entry_that_vanished_between_calls_is_treated_as_a_first_call() -> None:
    redis = FakeRedis()
    store = RedisIdempotencyStore(redis)
    request_hash = fingerprint("t", "c", "tool", ARGS)
    await store.reserve("k1", request_hash)
    redis.store.clear()  # the key expired between the SET NX and the GET

    assert (await store.reserve("k1", request_hash)).state is ReservationState.NEW


async def test_the_stored_redis_payload_is_json_and_carries_the_request_hash() -> None:
    redis = FakeRedis()
    store = RedisIdempotencyStore(redis)
    request_hash = fingerprint("t", "c", "tool", ARGS)

    await store.reserve("k1", request_hash)

    assert json.loads(redis.store["k1"])["hash"] == request_hash
