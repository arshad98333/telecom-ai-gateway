"""Idempotency for write operations.

A write without an idempotency key cannot be safely retried, so every write tool
requires one. The store turns a repeat into a lookup: the first call reserves the key
and executes; a repeat with the same key and the same input returns the original
result and executes nothing.

Three states matter, and skipping the middle one is the usual bug. A key can be
*reserved* (a call is in flight), *completed* (a result is stored), or absent. A repeat
that arrives while the first call is still running must not start a second execution;
it is told to wait and retry, which is safer than guessing.

The key is scoped by tenant and customer as well as by the key itself, so two tenants
cannot collide, deliberately or accidentally, in the same namespace.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Any, Protocol

from telecom_mcp.domain.errors import IdempotencyKeyReusedError
from telecom_mcp.domain.ports import Clock

DEFAULT_TTL_S = 86_400  # 24 hours


class ReservationState(StrEnum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class Reservation:
    state: ReservationState
    #: Present only when the state is COMPLETED.
    result: dict[str, Any] | None = None


def fingerprint(tenant_id: str, cx_id: str, tool: str, arguments: dict[str, Any]) -> str:
    """A stable hash of what the caller asked for.

    Reusing a key with different arguments is a caller bug, and returning the first
    result for the second request would hide it. Hashing the request is how we detect
    it rather than paper over it.
    """
    payload = json.dumps(
        {"tenant": tenant_id, "cx": cx_id, "tool": tool, "arguments": arguments},
        sort_keys=True,
        default=str,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def scoped_key(tenant_id: str, cx_id: str, tool: str, key: str) -> str:
    """Namespace the key so two tenants cannot collide."""
    return f"idem:{tenant_id}:{cx_id}:{tool}:{key}"


class IdempotencyStore(Protocol):
    async def reserve(self, key: str, request_hash: str) -> Reservation: ...

    async def complete(self, key: str, result: dict[str, Any]) -> None: ...

    async def release(self, key: str) -> None:
        """Drop a reservation whose call failed, so a genuine retry can proceed."""
        ...

    async def ping(self) -> None: ...


@dataclass(slots=True)
class _Entry:
    request_hash: str
    expires_at: float
    result: dict[str, Any] | None = None


class MemoryIdempotencyStore:
    """Single-process store. Correct, and only correct for one replica.

    Production must use the Redis store, which is why the settings validator refuses
    this one when the environment is production.
    """

    def __init__(self, *, clock: Clock, ttl_s: int = DEFAULT_TTL_S) -> None:
        self._clock = clock
        self._ttl_s = ttl_s
        self._entries: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()

    async def reserve(self, key: str, request_hash: str) -> Reservation:
        async with self._lock:
            self._evict_expired()
            entry = self._entries.get(key)
            if entry is None:
                self._entries[key] = _Entry(
                    request_hash=request_hash,
                    expires_at=self._clock.monotonic() + self._ttl_s,
                )
                return Reservation(ReservationState.NEW)
            if entry.request_hash != request_hash:
                raise IdempotencyKeyReusedError(operation="reserve")
            if entry.result is None:
                return Reservation(ReservationState.IN_PROGRESS)
            return Reservation(ReservationState.COMPLETED, result=entry.result)

    async def complete(self, key: str, result: dict[str, Any]) -> None:
        async with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                entry.result = result
                entry.expires_at = self._clock.monotonic() + self._ttl_s

    async def release(self, key: str) -> None:
        async with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry.result is None:
                del self._entries[key]

    async def ping(self) -> None:
        return None

    def _evict_expired(self) -> None:
        now = self._clock.monotonic()
        expired = [key for key, entry in self._entries.items() if entry.expires_at <= now]
        for key in expired:
            del self._entries[key]


class RedisIdempotencyStore:
    """Shared store, so deduplication holds across replicas.

    ``SET NX`` is what makes the reservation atomic: two replicas racing on the same
    key produce exactly one winner, and the loser is told the call is in progress
    rather than starting a second one.
    """

    def __init__(self, client: Any, *, ttl_s: int = DEFAULT_TTL_S) -> None:
        self._client = client
        self._ttl_s = ttl_s

    async def reserve(self, key: str, request_hash: str) -> Reservation:
        payload = json.dumps({"hash": request_hash, "result": None})
        created = await self._client.set(key, payload, nx=True, ex=self._ttl_s)
        if created:
            return Reservation(ReservationState.NEW)

        raw = await self._client.get(key)
        if raw is None:
            # The entry expired between the two calls; treat this attempt as the first.
            await self._client.set(key, payload, ex=self._ttl_s)
            return Reservation(ReservationState.NEW)

        stored = json.loads(raw)
        if stored.get("hash") != request_hash:
            raise IdempotencyKeyReusedError(operation="reserve")
        result = stored.get("result")
        if result is None:
            return Reservation(ReservationState.IN_PROGRESS)
        return Reservation(ReservationState.COMPLETED, result=result)

    async def complete(self, key: str, result: dict[str, Any]) -> None:
        raw = await self._client.get(key)
        request_hash = json.loads(raw).get("hash") if raw else None
        payload = json.dumps({"hash": request_hash, "result": result}, default=str)
        await self._client.set(key, payload, ex=self._ttl_s)

    async def release(self, key: str) -> None:
        raw = await self._client.get(key)
        if raw is not None and json.loads(raw).get("result") is None:
            await self._client.delete(key)

    async def ping(self) -> None:
        await self._client.ping()
