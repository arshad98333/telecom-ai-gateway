"""Writing the audit record and the event, which always happen together.

Every state change and every refusal produces one audit record. Every state change also
produces one event. Both are written inside the caller's transaction, so a change
cannot commit without its record, and a record cannot survive a change that rolled back.

The audit chain is per tenant: each record carries the hash of the previous one, so
deleting or editing a record breaks the chain from that point and a single pass finds
it. The sequence is enforced by a unique index, which turns a gap or a duplicate into a
write error at the moment it happens rather than a discovery months later.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any, Literal, Protocol

from telecom_middleware.domain.events import DomainEvent, EventType
from telecom_middleware.domain.models import AuditRecord
from telecom_middleware.observability.redaction import Redactor
from telecom_middleware.security.principal import Principal

GENESIS_HASH = "0" * 64


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdGenerator(Protocol):
    def new_id(self) -> str: ...


def compute_hash(payload: dict[str, Any]) -> str:
    """Sorted keys, so the hash does not depend on field order."""
    return sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def chain_payload(record: dict[str, Any]) -> dict[str, Any]:
    """The hashed content: everything except the hash derived from it."""
    return {key: value for key, value in record.items() if key != "entry_hash"}


@dataclass(slots=True)
class Recorder:
    """Writes audit records and events for one request."""

    store: Any
    redactor: Redactor
    clock: Clock
    ids: IdGenerator

    async def audit(
        self,
        *,
        principal: Principal,
        action: str,
        resource: str,
        decision: Literal["accepted", "rejected"],
        outcome: str,
        correlation_id: str,
        cx_id: str | None = None,
        case_id: str | None = None,
        failure_reason: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> AuditRecord:
        """Append one record, chained to the tenant's previous one."""
        previous_seq, previous_hash = await self.store.audit.head(principal.tenant_id)
        cx_ref = self.redactor.pseudonym(cx_id) if cx_id else None
        # A resource identifier such as "customers/CX-1234" carries the customer
        # reference in clear, which would put back exactly what cx_ref removes. The
        # same substitution is applied so one record cannot undo the other.
        if cx_id and cx_ref:
            resource = resource.replace(cx_id, cx_ref)
        body: dict[str, Any] = {
            "tenant_id": principal.tenant_id,
            "seq": previous_seq + 1,
            "record_id": self.ids.new_id(),
            "at": self.clock.now(),
            "correlation_id": correlation_id,
            "case_id": case_id,
            "actor_sub": principal.subject,
            "actor_role": str(principal.role),
            "cx_ref": cx_ref,
            "action": action,
            "resource": resource,
            "decision": decision,
            "outcome": outcome,
            "failure_reason": failure_reason,
            "previous_hash": previous_hash,
            # The detail is redacted before it is stored: an audit trail holding a
            # customer's passcode is a breach waiting to be discovered.
            "detail": self.redactor.redact(detail or {}, in_logs=True),
        }
        record = AuditRecord.model_validate({**body, "entry_hash": compute_hash(body)})
        await self.store.audit.append(record)
        return record

    async def emit(
        self,
        *,
        event_type: EventType,
        principal: Principal,
        subject: str,
        correlation_id: str,
        cx_id: str | None = None,
        case_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> DomainEvent:
        """Write one event to the outbox and hand it to any live watcher."""
        sequence = await self.store.outbox.next_sequence(principal.tenant_id)
        cx_ref = self.redactor.pseudonym(cx_id) if cx_id else None
        if cx_id and cx_ref:
            subject = subject.replace(cx_id, cx_ref)
        event = DomainEvent(
            event_id=self.ids.new_id(),
            type=event_type,
            tenant_id=principal.tenant_id,
            sequence=sequence,
            occurred_at=self.clock.now(),
            correlation_id=correlation_id,
            subject=subject,
            cx_ref=cx_ref,
            case_id=case_id,
            actor_sub=principal.subject,
            # An event body is fanned out to every subscriber, so it is redacted with
            # the telemetry rules rather than the owner's-own-data rules.
            payload=self.redactor.redact(payload or {}, in_logs=True),
        )
        await self.store.outbox.add(event)
        await self.store.publish(event)
        return event


def verify_chain(records: list[AuditRecord]) -> int | None:
    """Index of the first broken record, or None when the chain is intact.

    Records must be supplied oldest first. Used by the audit endpoint and by the
    verification command, so "has anything been tampered with" is a question with a
    one-pass answer rather than an opinion.
    """
    expected_previous = GENESIS_HASH
    expected_seq = 1
    for index, record in enumerate(records):
        if record.previous_hash != expected_previous or record.seq != expected_seq:
            return index
        body = record.model_dump(mode="python")
        if record.entry_hash != compute_hash(chain_payload(body)):
            return index
        expected_previous = record.entry_hash
        expected_seq += 1
    return None
